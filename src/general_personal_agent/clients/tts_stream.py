"""Doubao TTS v3 WebSocket streaming.

Tries the unidirectional endpoint first; with legacy appid/token credentials it
currently rejects the speaker/resource pair, so we transparently fall back to the
bidirectional endpoint (used in a one-way text-in/audio-out fashion).

Protocol frames: 4-byte header + event + optional connection/session id + payload.
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
from collections.abc import AsyncIterator

import websockets

from general_personal_agent.logging import get_logger
from general_personal_agent.settings import Settings

logger = get_logger(__name__)
_UNIDIRECTIONAL_BROKEN = False

FULL_CLIENT = 0b0001
WITH_EVENT = 0b0100
ERROR = 0b1111
JSON_SERIAL = 0b0001
NO_SERIAL = 0b0000
NO_COMPRESSION = 0b0000

EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_CONNECTION_STARTED = 50
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_TASK_REQUEST = 200
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352

CONNECTION_EVENTS = {
    EVENT_START_CONNECTION,
    EVENT_FINISH_CONNECTION,
    EVENT_CONNECTION_STARTED,
    51,
    52,
}


def _frame(
    event: int,
    payload: dict,
    *,
    connection_id: str | None = None,
    session_id: str | None = None,
) -> bytes:
    header = bytes([0x11, (FULL_CLIENT << 4) | WITH_EVENT, (JSON_SERIAL << 4) | NO_COMPRESSION, 0])
    body = struct.pack(">i", event)
    if connection_id is not None:
        raw = connection_id.encode()
        body += struct.pack(">I", len(raw)) + raw
    if session_id is not None:
        raw = session_id.encode()
        body += struct.pack(">I", len(raw)) + raw
    data = json.dumps(payload, ensure_ascii=False).encode()
    body += struct.pack(">I", len(data)) + data
    return header + body


def _parse(raw: bytes) -> tuple[int | None, str | None, int, bytes]:
    header_size = raw[0] & 0x0F
    msg_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    serialization = raw[2] >> 4
    offset = header_size * 4
    event = None
    session_id = None
    if flags & WITH_EVENT:
        event = struct.unpack(">i", raw[offset : offset + 4])[0]
        offset += 4
        if event not in CONNECTION_EVENTS:
            size = struct.unpack(">I", raw[offset : offset + 4])[0]
            offset += 4
            session_id = raw[offset : offset + size].decode("utf-8", "ignore")
            offset += size
        elif event in (EVENT_CONNECTION_STARTED, 51, 52):
            size = struct.unpack(">I", raw[offset : offset + 4])[0]
            offset += 4 + size
    if msg_type == ERROR:
        offset += 4  # error code
    size = struct.unpack(">I", raw[offset : offset + 4])[0]
    payload = raw[offset + 4 : offset + 4 + size]
    return event, session_id, serialization, payload


class TTSStreamClient:
    """One persistent WS connection, one session per sentence."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connect_id = str(uuid.uuid4())
        self._session_id: str | None = None
        self._active_url = ""
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        if self._ws is not None:
            return
        global _UNIDIRECTIONAL_BROKEN
        fallback = self.settings.tts_ws_fallback_url
        if _UNIDIRECTIONAL_BROKEN and fallback:
            urls = [fallback]
        else:
            urls = [self.settings.tts_ws_url]
            if fallback and fallback not in urls:
                urls.append(fallback)
        headers = {
            "X-Api-App-Key": self.settings.tool_vespeech_app_id,
            "X-Api-Access-Key": self.settings.tool_vespeech_api_key,
            "X-Api-Resource-Id": self.settings.tts_resource_id,
            "X-Api-Connect-Id": self._connect_id,
        }
        last_error: Exception | None = None
        for url in urls:
            try:
                ws = await websockets.connect(url, additional_headers=headers, max_size=None)
                await ws.send(_frame(EVENT_START_CONNECTION, {}))
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                if b'{"error"' in raw:
                    raise RuntimeError(raw[raw.find(b'{"error"') :][:200].decode("utf-8", "ignore"))
                event, _, _, _ = _parse(raw)
                if event != EVENT_CONNECTION_STARTED:
                    raise RuntimeError(f"unexpected connection event: {event}")
                self._ws = ws
                self._active_url = url
                logger.info("TTS WS connected: %s", url)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if url == self.settings.tts_ws_url and fallback:
                    _UNIDIRECTIONAL_BROKEN = True
                logger.warning("TTS WS connect failed (%s): %s", url, str(exc)[:160])
        raise RuntimeError(f"TTS WS unavailable: {last_error}")

    async def warmup(self) -> None:
        """Open the TTS connection early so the first spoken sentence is fast."""
        async with self._lock:
            await self._connect()

    async def _start_session(self) -> str:
        assert self._ws is not None
        params = {
            "speaker": self.settings.tool_vespeech_speaker,
            "audio_params": {"format": "pcm", "sample_rate": self.settings.tts_sample_rate},
        }
        await self._ws.send(
            _frame(
                EVENT_START_SESSION,
                {"event": EVENT_START_SESSION, "namespace": "BidirectionalTTS", "req_params": params},
                connection_id=self._connect_id,
            )
        )
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            if b'{"error"' in raw:
                raise RuntimeError(raw[raw.find(b'{"error"') :][:200].decode("utf-8", "ignore"))
            event, session_id, _, _ = _parse(raw)
            if event == EVENT_SESSION_STARTED and session_id:
                self._session_id = session_id
                return session_id

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield 24 kHz s16le mono PCM chunks as they arrive."""
        if not text.strip():
            return
        async with self._lock:
            await self._connect()
            assert self._ws is not None
            session_id = await self._start_session()
            await self._ws.send(
                _frame(
                    EVENT_TASK_REQUEST,
                    {"event": 200, "namespace": "BidirectionalTTS", "req_params": {"text": text}},
                    session_id=session_id,
                )
            )
            await self._ws.send(_frame(EVENT_FINISH_SESSION, {}, session_id=session_id))
            try:
                while True:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=20)
                    if b'{"error"' in raw:
                        raise RuntimeError(raw[raw.find(b'{"error"') :][:200].decode("utf-8", "ignore"))
                    event, _, serialization, payload = _parse(raw)
                    if event == EVENT_TTS_RESPONSE and serialization == NO_SERIAL and payload:
                        yield payload
                    elif event == EVENT_SESSION_FINISHED:
                        break
            finally:
                self._session_id = None

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await ws.send(_frame(EVENT_FINISH_CONNECTION, {}))
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
