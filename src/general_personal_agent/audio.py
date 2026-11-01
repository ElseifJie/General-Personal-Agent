"""PCM / WAV helpers."""

from __future__ import annotations

import io
import struct
import wave


def wav_to_pcm16k(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit WAV, got {w.getsampwidth() * 8}-bit")
        channels = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels > 1:
        samples = samples[::channels]
    if rate != 16000:
        ratio = rate / 16000
        resampled: list[int] = []
        i = 0.0
        while int(i) < len(samples) - 1:
            resampled.append(samples[int(i)])
            i += ratio
        samples = resampled
    return struct.pack(f"<{len(samples)}h", *samples)


def wav_to_pcm(data: bytes, sample_rate: int) -> bytes:
    """Decode 16-bit mono WAV, rejecting a mismatched sample rate lazily."""
    with wave.open(io.BytesIO(data)) as w:
        raw = w.readframes(w.getnframes())
        channels = w.getnchannels()
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels > 1:
        samples = samples[::channels]
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm_duration_ms(pcm: bytes, sample_rate: int = 16000) -> int:
    return int(len(pcm) / 2 / sample_rate * 1000)


def split_pcm(pcm: bytes, chunk_bytes: int) -> list[bytes]:
    return [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
