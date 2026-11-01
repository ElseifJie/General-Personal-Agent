"""Bounded upload reads/spooling for multipart endpoints."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass

from fastapi import UploadFile

from general_personal_agent.gpa.errors import GpaError

UPLOAD_CHUNK = 1 << 20


def _too_large(max_bytes: int) -> GpaError:
    return GpaError("VALIDATION_ERROR", f"file too large (max {max_bytes // 1024 // 1024}MB)")


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload in bounded chunks (kept for callers that need bytes)."""
    size = getattr(file, "size", None)
    if size is not None and int(size) > max_bytes:
        raise _too_large(max_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass
class SpooledUpload:
    path: str
    size: int
    sha256: str

    def cleanup(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


async def spool_upload(file: UploadFile, max_bytes: int) -> SpooledUpload:
    """Stream an upload to a temp file while hashing and enforcing the limit.

    The request body is never fully buffered in memory before validation; a
    service can then hand the path to TOS/extraction without a 50MB copy.
    """
    size = getattr(file, "size", None)
    if size is not None and int(size) > max_bytes:
        raise _too_large(max_bytes)
    fd, path = tempfile.mkstemp(prefix="gpa-upload-", suffix=".bin")
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise _too_large(max_bytes)
                digest.update(chunk)
                out.write(chunk)
        return SpooledUpload(path=path, size=total, sha256=digest.hexdigest())
    except Exception:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
