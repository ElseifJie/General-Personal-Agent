"""Doubao TTS: HTTP synthesis per sentence (validated), PCM for the browser."""

from __future__ import annotations

import base64
import io
import uuid
import wave

import httpx

TTS_HTTP_URL = "https://openspeech.bytedance.com/api/v1/tts"


class TTSError(RuntimeError):
    pass


class TTSClient:
    def __init__(
        self,
        app_id: str,
        access_token: str,
        speaker: str,
        cluster: str = "volcano_tts",
        speed_ratio: float = 1.0,
    ):
        self.app_id = app_id
        self.access_token = access_token
        self.speaker = speaker
        self.cluster = cluster
        self.speed_ratio = speed_ratio
        self._http = httpx.AsyncClient(timeout=60.0)
        self._cache: dict[str, tuple[float, bytes]] = {}
        self._cache_ttl = 3600.0

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, text: str, encoding: str) -> bytes:
        body = {
            "app": {"appid": self.app_id, "token": self.access_token, "cluster": self.cluster},
            "user": {"uid": "general-personal-agent"},
            "audio": {
                "voice_type": self.speaker,
                "encoding": encoding,
                "speed_ratio": self.speed_ratio,
            },
            "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"},
        }
        resp = await self._http.post(
            TTS_HTTP_URL,
            headers={"Authorization": f"Bearer;{self.access_token}"},
            json=body,
        )
        payload = resp.json()
        if resp.status_code != 200 or payload.get("code") != 3000:
            raise TTSError(
                f"TTS failed: HTTP {resp.status_code} code={payload.get('code')} msg={payload.get('message')}"
            )
        return base64.b64decode(payload["data"])

    async def synthesize_wav(self, text: str) -> bytes:
        return await self._request(text, "wav")

    async def synthesize_pcm(self, text: str, use_cache: bool = True) -> bytes:
        """Return 24 kHz s16le mono PCM. Deterministic sentences are cached."""
        import time

        now = time.monotonic()
        cached = self._cache.get(text)
        if use_cache and cached and now - cached[0] < self._cache_ttl:
            return cached[1]
        data = await self._request(text, "pcm")
        if data[:4] == b"RIFF":
            with wave.open(io.BytesIO(data)) as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise TTSError("unexpected TTS wav format")
                data = w.readframes(w.getnframes())
        if use_cache:
            if len(self._cache) >= 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[text] = (now, data)
        return data
