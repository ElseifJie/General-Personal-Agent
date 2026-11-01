"""Doubao bidirectional streaming ASR (16 kHz raw PCM)."""

from __future__ import annotations

import asyncio
import gzip
import json
import uuid
from collections.abc import Awaitable, Callable

import websockets

PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001
FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY = 0b0010
SERVER_ERROR = 0b1111
NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
JSON_SERIAL = 0b0001
NO_SERIAL = 0b0000
GZIP = 0b0001


def _packet(msg_type: int, flags: int, serial: int, comp: int, payload: bytes) -> bytes:
    header = bytes(
        [
            (PROTOCOL_VERSION << 4) | HEADER_SIZE,
            (msg_type << 4) | flags,
            (serial << 4) | comp,
            0,
        ]
    )
    return header + len(payload).to_bytes(4, "big") + payload


def _full_request(body: dict) -> bytes:
    return _packet(
        FULL_CLIENT_REQUEST,
        NO_SEQUENCE,
        JSON_SERIAL,
        GZIP,
        gzip.compress(json.dumps(body).encode()),
    )


def _audio(pcm: bytes, last: bool = False) -> bytes:
    return _packet(
        AUDIO_ONLY,
        NEG_SEQUENCE if last else NO_SEQUENCE,
        NO_SERIAL,
        GZIP,
        gzip.compress(pcm),
    )


def _parse(message: bytes) -> tuple[str, object]:
    if len(message) < 8:
        return "short", message
    msg_type = (message[1] >> 4) & 0x0F
    flags = message[1] & 0x0F
    serial = (message[2] >> 4) & 0x0F
    comp = message[2] & 0x0F
    offset = 4
    if flags in (POS_SEQUENCE, NEG_SEQUENCE):
        offset += 4
    size = int.from_bytes(message[offset : offset + 4], "big")
    offset += 4
    payload = message[offset : offset + size]
    if comp == GZIP:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    if msg_type == SERVER_ERROR:
        return "error", payload.decode("utf-8", "ignore")
    if serial == JSON_SERIAL:
        try:
            return "json", json.loads(payload)
        except Exception:
            return "text", payload.decode("utf-8", "ignore")
    return "raw", payload


class StreamingASR:
    """One ASR WebSocket per utterance.

    ``send`` may be called with arbitrary chunk sizes; audio is buffered and
    forwarded in 100 ms frames. ``finish`` flushes, appends 400 ms of silence
    (otherwise the last word is truncated) and returns the final text.
    """

    def __init__(
        self,
        ws_url: str,
        app_id: str,
        access_token: str,
        resource_id: str = "volc.bigasr.sauc.duration",
        sample_rate: int = 16000,
        enable_nonstream: bool = False,
    ):
        self.ws_url = ws_url
        self.app_id = app_id
        self.access_token = access_token
        self.resource_id = resource_id
        self.sample_rate = sample_rate
        self.enable_nonstream = enable_nonstream
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._receiver: asyncio.Task | None = None
        self._buffer = bytearray()
        self._frame = int(sample_rate * 2 * 0.1)
        self._text = ""
        self._closed = False
        self._on_partial: Callable[[str], Awaitable[None]] | None = None

    async def start(self, on_partial: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._on_partial = on_partial
        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self._ws = await websockets.connect(self.ws_url, additional_headers=headers, max_size=None)
        await self._ws.send(
            _full_request(
                {
                    "user": {"uid": "general-personal-agent"},
                    "audio": {
                        "format": "pcm",
                        "sample_rate": self.sample_rate,
                        "channel": 1,
                        "codec": "raw",
                        "bits": 16,
                    },
                    "request": {
                        "model_name": "bigmodel",
                        "enable_itn": True,
                        "enable_punc": True,
                        "enable_ddc": True,
                        "show_utterances": True,
                        "enable_nonstream": self.enable_nonstream,
                    },
                }
            )
        )
        self._receiver = asyncio.create_task(self._receive())

    async def _receive(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                kind, data = _parse(message)
                if kind != "json" or not isinstance(data, dict):
                    continue
                body = data.get("result") or {}
                text = body.get("text") or ""
                utterances = body.get("utterances") or []
                if utterances:
                    text = "".join(u.get("text", "") for u in utterances)
                if text and text != self._text:
                    self._text = text
                    if self._on_partial is not None:
                        await self._on_partial(text)
        except websockets.ConnectionClosedOK:
            pass
        except Exception:
            pass
        finally:
            self._closed = True

    async def _flush(self, final: bool = False) -> None:
        assert self._ws is not None
        while len(self._buffer) >= self._frame:
            frame = bytes(self._buffer[: self._frame])
            del self._buffer[: self._frame]
            await self._ws.send(_audio(frame))
        if final and self._buffer:
            await self._ws.send(_audio(bytes(self._buffer)))
            self._buffer.clear()

    async def send(self, pcm: bytes) -> None:
        if self._ws is None:
            await self.start()
        self._buffer.extend(pcm)
        await self._flush()

    async def finish(self, timeout: float = 15.0) -> str:
        if self._ws is None:
            return ""
        await self._flush(final=True)
        silence = b"\x00\x00" * int(self.sample_rate * 0.4)
        await self._ws.send(_audio(silence))
        await self._ws.send(_audio(b"", last=True))
        if self._receiver is not None:
            try:
                await asyncio.wait_for(self._receiver, timeout=timeout)
            except TimeoutError:
                self._receiver.cancel()
        await self.close()
        return self._text.strip()

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._receiver is not None and not self._receiver.done():
            self._receiver.cancel()
        self._receiver = None
