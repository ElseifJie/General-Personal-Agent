"""Incremental sentence splitter tuned for a stable TTS voice.

Every TTS request is an independent generation. Cutting one in the middle of a
spoken clause restarts the prosody (final tone, pause) and may change the
engine's loudness, which listeners hear as "the voice/timbre changed in the
middle of a sentence".  Chunks therefore only end on punctuation:

* a hard boundary (``。！？；``) ends a chunk once it has ``min_chars``;
* a comma ends a chunk once the ``first_max``/``max_chars`` budget is reached;
* text without any punctuation is only cut at ``max_chars``.
"""

from __future__ import annotations

import re

BOUNDARIES = "。！？；!?;\n"
SOFT_BOUNDARY = "，,、"
_SENTENCE_RE = re.compile(r"[^。！？；!?\n]+[。！？；!?]?")


def has_readable(text: str) -> bool:
    """True when text contains anything TTS can speak (CJK counts as alnum)."""
    return any(ch.isalnum() for ch in text or "")


def chunk_text(text: str, *, max_chars: int = 120) -> list[str]:
    """Group a complete answer into as few TTS requests as possible.

    Short answers stay in one request so the whole reply is a single
    continuous generation; longer ones are packed at sentence boundaries up to
    ``max_chars``.  Only an over-long sentence is broken further, and then at
    its own punctuation (never mid-clause).
    """
    text = (text or "").strip()
    if not text:
        return []
    pieces = [piece.strip() for piece in _SENTENCE_RE.findall(text) if piece.strip()]
    if not pieces:
        pieces = [text]
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        if len(piece) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            splitter = SentenceSplitter(
                min_chars=1, max_chars=max_chars, first_max=max_chars
            )
            chunks.extend(splitter.feed(piece))
            tail = splitter.flush()
            if tail:
                chunks.append(tail)
            continue
        if buffer and len(buffer) + len(piece) > max_chars:
            chunks.append(buffer)
            buffer = ""
        buffer += piece
    if buffer:
        chunks.append(buffer)
    return chunks


class SentenceSplitter:
    """Incrementally cut streamed text into TTS requests at punctuation."""

    def __init__(self, min_chars: int = 4, max_chars: int = 60, first_max: int = 20):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.first_max = first_max
        self._buffer = ""
        self._sentences = 0

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        out: list[str] = []
        while True:
            index = self._find_boundary()
            if index < 0:
                break
            sentence = self._buffer[: index + 1].strip()
            self._buffer = self._buffer[index + 1 :]
            if len(sentence) >= self.min_chars:
                out.append(sentence)
                self._sentences += 1
            elif sentence:
                self._buffer = sentence + self._buffer
                break
        return out

    def _find_boundary(self) -> int:
        limit = self.first_max if self._sentences == 0 else self.max_chars
        soft = -1
        for i, char in enumerate(self._buffer):
            # A too-short sentence is merged into the next one instead of
            # becoming a tiny request (and instead of stalling the buffer).
            if char in BOUNDARIES and i + 1 >= self.min_chars:
                return i
            if char in SOFT_BOUNDARY and i + 1 >= self.min_chars:
                soft = i
            if i + 1 >= limit and soft >= 0:
                return soft
            if i + 1 >= self.max_chars:
                # A clause with no punctuation at all: cut at a word boundary
                # when there is one, otherwise at the hard cap.
                space = self._last_space(i)
                return space if space >= self.min_chars else i
        return -1

    def _last_space(self, index: int) -> int:
        floor = max(self.min_chars - 1, index - 20)
        for j in range(index, floor, -1):
            if self._buffer[j].isspace():
                return j - 1 if j > 0 else 0
        return -1

    def flush(self) -> str:
        text = self._buffer.strip()
        self._buffer = ""
        return text if len(text) >= 1 else ""
