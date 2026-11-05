"""GPA voice pipeline: ASR -> ConversationService -> TTS, one conversation.

The voice adapter never owns context or task execution. It turns microphone
audio into a voice turn on the same conversation as text, then relays the
agent/task events back as text deltas and 24 kHz PCM frames.
"""

from __future__ import annotations

import array
import asyncio
import base64
import math
import time
from collections.abc import Callable
from typing import Any

from general_personal_agent.clients.asr import StreamingASR
from general_personal_agent.clients.tts_stream import TTSStreamClient
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.voice.fillers import (
    filler_for,
    tool_filler,
    tool_label,
)
from general_personal_agent.gpa.voice.tts_text import sanitize_for_tts
from general_personal_agent.voice.sentences import chunk_text, has_readable

logger = get_logger(__name__)

ASRFactory = Callable[[Any], StreamingASR]
TTSStreamFactory = Callable[[Any], TTSStreamClient]

# A short, fixed, privacy-safe cue when the voice entry resumed an
# existing conversation. It never includes the conversation title.
RESUME_PHRASE = "接着刚才那个继续。"
_RESUME_PCM_CACHE: bytes | None = None

# Streaming TTS returns many tiny PCM chunks. Sending each one as its own
# ``audio`` frame makes the browser create a separate AudioBuffer for every
# ~20 ms, so a chunk that lands a little late produces a gap right on the
# first one or two characters. Coalescing into ~170 ms frames (24 kHz s16le
# mono) gives the player one continuous buffer at the start of a sentence.
_AUDIO_FRAME_BYTES = 8 * 1024

# The bigmodel ASR (enable_ddc=True) can hallucinate a plausible short phrase
# when fed silence/room noise -- e.g. a quick tap while the voice page is still
# "连接中". That phantom transcript used to become a real user turn. Require a
# little genuine voiced audio before trusting a non-empty ASR final.
_ASR_NOISE_RMS_FLOOR = 300  # int16 RMS; real speech after AGC clears this easily
_ASR_MIN_SPEECH_MS = 120.0


def _voiced_ms(chunk: bytes, sample_rate: int) -> float:
    """Milliseconds of s16le mono PCM whose RMS clears the noise floor."""
    usable = len(chunk) - (len(chunk) % 2)
    if usable < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[:usable])
    if not samples:
        return 0.0
    total = 0
    for value in samples:
        total += value * value
    if math.sqrt(total / len(samples)) < _ASR_NOISE_RMS_FLOOR:
        return 0.0
    return len(samples) / max(1, sample_rate) * 1000.0


class GpaVoiceSession:
    def __init__(
        self,
        *,
        websocket,
        gpa,
        conversation: dict,
        settings,
        tts_http,
        asr_factory: ASRFactory | None = None,
        tts_stream_factory: TTSStreamFactory | None = None,
        resume_cue: bool = False,
    ):
        self.ws = websocket
        self.gpa = gpa
        self.conversation = conversation
        self.conversation_id = conversation["conversation_id"]
        self.tenant_id = conversation["tenant_id"]
        self.app_user_id = conversation["app_user_id"]
        self.settings = settings
        # Kept for constructor compatibility (startup readiness probe). It is
        # deliberately NOT used to synthesise anything the user can hear: the
        # legacy HTTP v1 endpoint renders the same voice_type through a
        # different acoustic model than the streamed v3 engine, so every
        # audible phrase now goes through ``tts_stream`` to keep one voice.
        self.tts_http = tts_http
        self.asr_factory = asr_factory or self._default_asr
        self.tts_stream_factory = tts_stream_factory or TTSStreamClient
        self.tts_stream: TTSStreamClient = self.tts_stream_factory(settings)
        self.asr: StreamingASR | None = None
        self.queue = None
        self.unsubscribe = lambda: None
        self._consumer: asyncio.Task | None = None
        self._tts_warmup: asyncio.Task | None = None
        self._closed = False
        self._turn_id: str | None = None
        self._turn_started = 0.0
        self._asr_ms = 0
        self._ttft_ms = 0
        self._first_audio_ms = 0
        self._assistant_text = ""
        self._tts_spoken = False
        self._answer_audio_started = False
        self._generation = 0
        self._last_metrics: dict[str, Any] = {}
        # Filler / barge-in state.
        self._filler_enabled = bool(getattr(settings, "gpa_voice_filler_enabled", True))
        self._filler_spoken = False
        self._filler_ms = 0
        self._filler_pcm: dict[str, bytes] = {}
        self._tts_lock = asyncio.Lock()
        self._tts_active = False
        self._resume_cue = bool(resume_cue)
        self._resume_cue_played = False
        self._resume_cue_task: asyncio.Task | None = None
        self._user_audio_started = False
        self._speech_ms = 0.0

    def _default_asr(self, settings) -> StreamingASR:
        return StreamingASR(
            settings.asr_ws_url,
            settings.tool_vespeech_app_id,
            settings.tool_vespeech_api_key,
            sample_rate=settings.asr_sample_rate,
            enable_nonstream=settings.asr_enable_nonstream,
        )

    # ------------------------------------------------------------------ start
    async def start(self) -> None:
        self.queue, self.unsubscribe = self.gpa.bus.subscribe(self.conversation_id)
        self._consumer = asyncio.create_task(self._consume(), name="gpa-voice-events")
        self._tts_warmup = asyncio.create_task(self._warm_tts(), name="gpa-voice-tts-warmup")
        await self._send(
            {
                "type": "ready",
                "conversationId": self.conversation_id,
                "activeChannel": self.conversation.get("active_channel", "voice"),
                "channels": list(self.conversation.get("channels") or []),
                "sampleRate": self.settings.tts_sample_rate,
                "asrSampleRate": self.settings.asr_sample_rate,
                "user": {"id": self.app_user_id},
            }
        )
        if self._resume_cue:
            self._resume_cue_task = asyncio.create_task(
                self._play_resume_cue(), name="gpa-voice-resume-cue"
            )

    async def _play_resume_cue(self) -> None:
        """Play one short cached cue when the entry resumed a conversation.

        Best effort: TTS failure or an already-talking user simply skips it.
        The phrase is cached for the process so later sessions are instant.
        """
        global _RESUME_PCM_CACHE
        if not self._resume_cue or self._closed or self._user_audio_started:
            return
        pcm = _RESUME_PCM_CACHE
        if not pcm:
            try:
                # Same engine as fillers and the answer: a cue rendered by the
                # legacy HTTP client would be heard as a second voice right at
                # the "接入" moment, before the user has even spoken.
                pcm = await self.tts_stream.synthesize_pcm(RESUME_PHRASE)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - cue must never break voice
                logger.debug("resume cue synth failed: %s", exc)
                return
            if pcm:
                _RESUME_PCM_CACHE = pcm
        if (
            not pcm
            or self._closed
            or self._user_audio_started
            or self._resume_cue_played
        ):
            return
        self._resume_cue_played = True
        duration_ms = int(
            len(pcm) / max(1, int(self.settings.tts_sample_rate) * 2) * 1000
        )
        async with self._tts_lock:
            # Never interleave the cue with an answer that already started:
            # two TTS sources on one socket sound like two different voices.
            if self._closed or self._turn_id is not None or self._user_audio_started:
                return
            await self._send(
                {
                    "type": "resume_cue",
                    "text": RESUME_PHRASE,
                    "duration_ms": duration_ms,
                }
            )
            await self._send_audio(pcm, count_first=False)

    async def _warm_tts(self) -> None:
        try:
            await self.tts_stream.warmup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("TTS warmup skipped: %s", exc)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self.ws.send_json(payload)
        except Exception:
            self._closed = True

    # ------------------------------------------------------------------ audio
    async def audio(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._user_audio_started = True
        if self.asr is None:
            asr = self.asr_factory(self.settings)
            try:
                await asr.start(on_partial=self._on_partial)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ASR start failed: %s", exc)
                await self._send(
                    {"type": "error", "code": "UPSTREAM_UNAVAILABLE", "message": "语音识别暂不可用"}
                )
                return
            self.asr = asr
            self._speech_ms = 0.0
        self._speech_ms += _voiced_ms(
            chunk, int(getattr(self.settings, "asr_sample_rate", 16000))
        )
        await self.asr.send(chunk)

    async def _on_partial(self, text: str) -> None:
        await self._send({"type": "partial", "text": text})

    async def audio_end(self) -> None:
        asr, self.asr = self.asr, None
        speech_ms = self._speech_ms
        self._speech_ms = 0.0
        if asr is None:
            await self._send({"type": "final", "text": "", "asr_ms": 0})
            return
        started = time.perf_counter()
        try:
            text = await asr.finish()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ASR finish failed: %s", exc)
            text = ""
        asr_ms = int((time.perf_counter() - started) * 1000)
        if text.strip() and speech_ms < _ASR_MIN_SPEECH_MS:
            # The bigmodel ASR can invent a plausible short word on a
            # silence/noise-only tap (typically while the page is still
            # "连接中"). Letting it through starts a real turn: the agent then
            # answers a question nobody asked and may address the user by that
            # made-up word as if it were their name.
            logger.info("dropping noise-only ASR final %r (voiced_ms=%.0f)", text, speech_ms)
            text = ""
        await self._send({"type": "final", "text": text, "asr_ms": asr_ms})
        if not text.strip():
            return
        if self._turn_id:
            await self.interrupt()
        await self._start_turn(text, asr_ms=asr_ms)

    async def _start_turn(self, text: str, *, asr_ms: int) -> None:
        try:
            accepted = await self.gpa.conversations.submit_text(
                self.conversation_id,
                self.tenant_id,
                self.app_user_id,
                content=text,
                channel="voice",
            )
        except GpaError as exc:
            if exc.code == "CONFLICT":
                await self.interrupt()
                try:
                    accepted = await self.gpa.conversations.submit_text(
                        self.conversation_id,
                        self.tenant_id,
                        self.app_user_id,
                        content=text,
                        channel="voice",
                    )
                except GpaError as retry_exc:
                    await self._send({"type": "error", "code": retry_exc.code, "message": retry_exc.message})
                    return
            else:
                await self._send({"type": "error", "code": exc.code, "message": exc.message})
                return
        self._turn_id = accepted["turn_id"]
        self._turn_started = time.perf_counter()
        self._asr_ms = asr_ms
        self._ttft_ms = 0
        self._first_audio_ms = 0
        self._filler_ms = 0
        self._filler_spoken = False
        self._assistant_text = ""
        self._tts_spoken = False
        self._answer_audio_started = False
        if self._filler_enabled:
            phrase = filler_for(text)
            if phrase:
                await self._speak_filler(phrase)

    async def _speak_filler(self, phrase: str) -> None:
        """Play a short phrase in the answer voice before the answer starts."""
        if not phrase or self._closed or self._filler_spoken:
            return
        self._filler_spoken = True
        generation = self._generation
        pcm = self._filler_pcm.get(phrase)
        if not pcm and not self._answer_audio_started:
            try:
                # Same engine as the answer, otherwise the filler is heard as a
                # second, duller voice right before the reply.
                pcm = await self.tts_stream.synthesize_pcm(phrase)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - filler must not break the turn
                logger.debug("filler synth failed (%s): %s", phrase, exc)
                pcm = b""
            if pcm:
                self._filler_pcm[phrase] = pcm
        if not pcm or generation != self._generation or self._closed:
            return
        async with self._tts_lock:
            if generation != self._generation or self._closed:
                return
            if self._answer_audio_started:
                return  # the real answer already started; never append it after
            # Announce only what is actually played, so the transcript never
            # shows a filler that was dropped for a fast answer.
            await self._send({"type": "filler", "text": phrase})
            await self._send_audio(pcm, count_first=False)
            if not self._filler_ms:
                self._filler_ms = self._elapsed_ms()

    async def interrupt(self) -> None:
        """Barge-in: stop playback, cancel the running turn, keep task events.

        The consumer and its queue stay alive: old ``message.*`` events are
        filtered by ``_turn_id``, while ``task.*``/``artifact`` events queued
        during the barge-in must still reach the workbench.
        """
        await self._send({"type": "audio_stop"})
        await self._send({"type": "interrupted"})
        self._generation += 1
        self._turn_id = None
        self._assistant_text = ""
        self._filler_spoken = False
        self._tts_spoken = False
        self._answer_audio_started = False
        cue, self._resume_cue_task = self._resume_cue_task, None
        if cue is not None and not cue.done():
            cue.cancel()
        warmup, self._tts_warmup = self._tts_warmup, None
        if warmup is not None and not warmup.done():
            warmup.cancel()
        # A TTS session cancelled mid-sentence leaves the socket in an unknown
        # state; replace the client only when audio was actually in flight.
        if self._tts_active:
            old, self.tts_stream = self.tts_stream, self.tts_stream_factory(self.settings)
            asyncio.create_task(self._close_tts(old), name="gpa-voice-tts-close")
        try:
            await asyncio.wait_for(
                self.gpa.conversations.stop(
                    self.conversation_id, self.tenant_id, self.app_user_id, "barge-in"
                ),
                timeout=0.9,
            )
        except Exception as exc:  # noqa: BLE001 - ack already sent; cancellation continues
            logger.warning("voice cancel ack slow/failed: %s", exc)

    @staticmethod
    async def _close_tts(client: TTSStreamClient) -> None:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- consume
    async def _consume(self) -> None:
        while not self._closed:
            try:
                message = await self.queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._handle_event(message)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - one bad event must not kill the voice loop
                logger.exception("voice event handling failed")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        turn_id = event.get("turn_id")
        if kind in {"message.delta", "message.final", "turn.settled"} and turn_id != self._turn_id:
            return
        payload = event.get("payload") or {}
        if kind == "message.delta":
            text = str(payload.get("text") or "")
            if not text:
                return
            if self._ttft_ms == 0:
                self._ttft_ms = self._elapsed_ms()
            self._assistant_text += text
            # Deltas only drive the live transcript. TTS waits for the final so
            # one answer is one generation: speaking each sentence turned every
            # sentence boundary into a fresh voice/prosody (and the closing
            # sentence sounded like a different speaker).
            await self._send({"type": "text.delta", "text": text})
        elif kind == "message.final":
            text = str(payload.get("text") or "")
            if text:
                self._assistant_text = text
                await self._speak_answer(text)
        elif kind == "tool.call":
            if self._turn_id is None or (turn_id and turn_id != self._turn_id):
                return  # stale tool event from a cancelled/settled turn
            name = str(payload.get("name") or "")
            await self._send({"type": "tool_progress", "name": name, "label": tool_label(name)})
            phrase = tool_filler(name)
            if phrase and self._filler_enabled and not self._filler_spoken:
                await self._speak_filler(phrase)
        elif kind == "turn.settled":
            if self._assistant_text and not self._tts_spoken:
                await self._speak_answer(self._assistant_text)
            outcome = str(payload.get("outcome") or payload.get("status") or "completed")
            self._last_metrics = {
                "asr_ms": self._asr_ms,
                "filler_ms": self._filler_ms,
                "ttft_ms": self._ttft_ms,
                "first_audio_ms": self._first_audio_ms,
                "total_ms": self._elapsed_ms(),
            }
            await self._send(
                {
                    "type": "turn.metrics",
                    "turnId": turn_id,
                    "outcome": outcome,
                    "final": True,
                    "text": self._assistant_text,
                    "metrics": self._last_metrics,
                }
            )
            self._turn_id = None
        elif kind in {"task.created", "task.status"}:
            task = {
                "taskId": event.get("task_id") or payload.get("taskId"),
                **payload,
            }
            await self._send({"type": "task", "task": task})
        elif kind == "artifact":
            artifact = {"taskId": event.get("task_id"), **payload}
            await self._send({"type": "artifact", "artifact": artifact})
        elif kind == "error":
            await self._send(
                {
                    "type": "error",
                    "code": str(payload.get("code") or "UPSTREAM_UNAVAILABLE"),
                    "message": str(payload.get("message") or "本轮出错"),
                }
            )

    def _elapsed_ms(self) -> int:
        if not self._turn_started:
            return 0
        return int((time.perf_counter() - self._turn_started) * 1000)

    async def _speak_answer(self, text: str) -> None:
        """Speak the whole answer in one continuous TTS generation."""
        if self._tts_spoken or self._closed:
            return
        chunks = chunk_text(sanitize_for_tts(text))
        if not chunks:
            return
        self._tts_spoken = True
        for chunk in chunks:
            await self._speak(chunk)

    async def _speak(self, sentence: str) -> None:
        sentence = sanitize_for_tts(sentence)
        if not sentence.strip() or not has_readable(sentence):
            return
        generation = self._generation
        async with self._tts_lock:
            if generation != self._generation or self._closed:
                return
            self._answer_audio_started = True
            self._tts_active = True
            try:
                await self._stream_sentence(sentence, generation, allow_retry=True)
            finally:
                self._tts_active = False

    async def _stream_sentence(self, sentence: str, generation: int, *, allow_retry: bool) -> None:
        """Speak one sentence through the single streaming TTS engine.

        Tiny upstream PCM chunks are coalesced into ~170 ms frames so the
        browser plays one continuous buffer at the start of the sentence
        instead of a fresh AudioBuffer every few samples. If the stream fails
        before any audio reached the browser, retry once with a fresh
        connection of the SAME engine -- never fall back to the HTTP engine,
        which would be heard as a second voice.
        """
        pending = bytearray()
        sent_audio = False

        async def flush() -> None:
            nonlocal sent_audio
            if not pending or generation != self._generation or self._closed:
                return
            pcm = bytes(pending)
            pending.clear()
            await self._send_audio(pcm)
            sent_audio = True

        try:
            async for chunk in self.tts_stream.stream(sentence):
                if generation != self._generation or self._closed:
                    return
                if not chunk:
                    continue
                # PCM is 16-bit: keep the buffer sample-aligned so no frame
                # boundary ever splits a sample.
                if len(chunk) % 2:
                    chunk = chunk[:-1]
                if not chunk:
                    continue
                pending += chunk
                while len(pending) >= _AUDIO_FRAME_BYTES:
                    frame = bytes(pending[:_AUDIO_FRAME_BYTES])
                    del pending[:_AUDIO_FRAME_BYTES]
                    await self._send_audio(frame)
                    sent_audio = True
            await flush()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("streaming TTS failed: %s", exc)
            if generation != self._generation or self._closed:
                return
            # Whatever PCM we already hold is a valid prefix of this sentence;
            # play it rather than dropping the words, then stop (never replay
            # it: that would duplicate words / change the voice).
            await flush()
            old, self.tts_stream = self.tts_stream, self.tts_stream_factory(self.settings)
            asyncio.create_task(self._close_tts(old), name="gpa-voice-tts-close")
        if sent_audio or not allow_retry or generation != self._generation or self._closed:
            return
        # Nothing reached the browser: retry once through the same engine.
        await self._stream_sentence(sentence, generation, allow_retry=False)

    async def _send_audio(self, pcm: bytes, *, count_first: bool = True) -> None:
        if not pcm:
            return
        # s16le PCM must be emitted in whole samples; an odd trailing byte
        # would misalign every following sample in the browser.
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if not pcm:
            return
        if count_first and self._first_audio_ms == 0 and self._turn_started:
            self._first_audio_ms = self._elapsed_ms()
        for offset in range(0, len(pcm), 32 * 1024):
            await self._send(
                {
                    "type": "audio",
                    "data": base64.b64encode(pcm[offset : offset + 32 * 1024]).decode(),
                    "sample_rate": self.settings.tts_sample_rate,
                }
            )

    # ------------------------------------------------------------------ close
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        # Leaving the voice page ends the live session but does
        # not cancel the running turn. Only an explicit interrupt/stop does.
        cue, self._resume_cue_task = self._resume_cue_task, None
        if cue is not None and not cue.done():
            cue.cancel()
        warmup, self._tts_warmup = self._tts_warmup, None
        if warmup is not None and not warmup.done():
            warmup.cancel()
        consumer, self._consumer = self._consumer, None
        if consumer is not None and not consumer.done():
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):
                pass
        self.unsubscribe()
        if self.asr is not None:
            try:
                await self.asr.close()
            except Exception:  # noqa: BLE001
                pass
            self.asr = None
        try:
            await self.tts_stream.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self._last_metrics)
