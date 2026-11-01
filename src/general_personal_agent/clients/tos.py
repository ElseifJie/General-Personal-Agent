"""TOS wrapper (STS credentials supported)."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator

import tos

READ_CHUNK = 4 * 1024 * 1024


class TOSClient:
    def __init__(
        self,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        session_token: str = "",
        prefix: str = "general-personal-agent",
    ):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = tos.TosClientV2(
            access_key,
            secret_key,
            endpoint,
            region,
            security_token=session_token or None,
        )

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        await asyncio.to_thread(
            self._client.put_object,
            self.bucket,
            key,
            content=data,
            content_type=content_type,
        )
        return key

    async def put_file(
        self, key: str, path: str, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload a local file without loading it into the event loop."""

        def _put() -> None:
            self._client.put_object_from_file(
                self.bucket,
                key,
                path,
                content_length=os.path.getsize(path),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        return key

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, self.bucket, key)

    async def get_bytes(self, key: str) -> tuple[bytes, str]:
        """Read an object server-side; returns (body, content_type).

        Used by the authenticated download proxy so browsers never need a
        presigned URL (which dies with the STS token / TTL).
        """

        def _read() -> tuple[bytes, str]:
            output = self._client.get_object(self.bucket, key)
            return output.read(), getattr(output, "content_type", "") or ""

        return await asyncio.to_thread(_read)

    async def copy_object(self, source_key: str, target_key: str, source_bucket: str | None = None) -> str:
        """Server-side copy (same region/bucket credentials); returns target key."""
        await asyncio.to_thread(
            self._client.copy_object,
            self.bucket,
            target_key,
            source_bucket or self.bucket,
            source_key,
        )
        return target_key

    async def iter_range(
        self, key: str, start: int, end: int, chunk_size: int = READ_CHUNK
    ) -> AsyncIterator[bytes]:
        """Yield an inclusive byte range in bounded chunks (HTTP Range proxy)."""
        offset = max(0, start)
        while offset <= end:
            chunk_end = min(offset + chunk_size - 1, end)

            def _read(start_at: int = offset, end_at: int = chunk_end) -> bytes:
                output = self._client.get_object(self.bucket, key, range_start=start_at, range_end=end_at)
                return output.read()

            data = await asyncio.to_thread(_read)
            if not data:
                return
            yield data
            offset += len(data)

    async def iter_bytes(self, key: str, chunk_size: int = READ_CHUNK) -> AsyncIterator[bytes]:
        """Range-read an object in chunks (StreamingResponse 下载代理用).

        全量 get_bytes 会把大产物整个读进内存；这里按 4MB 分块拉取。
        """
        offset = 0
        while True:
            start, end = offset, offset + chunk_size - 1

            def _read_range(start: int = start, end: int = end) -> bytes:
                output = self._client.get_object(self.bucket, key, range_start=start, range_end=end)
                return output.read()

            data = await asyncio.to_thread(_read_range)
            if not data:
                return
            yield data
            if len(data) < chunk_size:
                return
            offset += len(data)

    def signed_url(self, key: str, expires: int = 3600) -> str:
        return self._client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get, self.bucket, key, expires=expires
        ).signed_url

    def object_key(self, *parts: str) -> str:
        return "/".join([self.prefix, *[str(p).strip("/") for p in parts if p]])

    @staticmethod
    def stamp() -> str:
        return str(int(time.time() * 1000))
