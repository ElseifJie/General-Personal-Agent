"""Uploads are rejected before the whole body is buffered."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.upload import read_upload, spool_upload


class _Upload:
    def __init__(self, chunks, size=None):
        self._chunks = list(chunks)
        self.size = size

    async def read(self, size):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        return chunk[:size]


@pytest.mark.asyncio
async def test_declared_size_is_rejected_before_read():
    with pytest.raises(GpaError) as caught:
        await read_upload(_Upload([b"x" * 100], size=100), 10)
    assert caught.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_chunked_body_is_bounded_without_full_buffer():
    upload = _Upload([b"a" * 6, b"b" * 6, b"c"])
    with pytest.raises(GpaError):
        await read_upload(upload, 10)


@pytest.mark.asyncio
async def test_spool_upload_hashes_and_cleans_up():
    upload = _Upload([b"abc", b"def"], size=6)
    spooled = await spool_upload(upload, 10)
    assert spooled.size == 6
    assert spooled.sha256 == hashlib.sha256(b"abcdef").hexdigest()
    path = Path(spooled.path)
    assert path.read_bytes() == b"abcdef"
    spooled.cleanup()
    assert not path.exists()
