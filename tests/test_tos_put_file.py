"""TOSClient.put_file passes a file handle (not a full in-memory copy)."""

from __future__ import annotations

from pathlib import Path

from general_personal_agent.clients.tos import TOSClient


class _Sdk:
    def __init__(self):
        self.calls: list[dict] = []

    def put_object_from_file(self, bucket, key, path, **kwargs):
        self.calls.append(
            {
                "bucket": bucket,
                "key": key,
                "content_length": kwargs.get("content_length"),
                "content_type": kwargs.get("content_type"),
                "bytes": Path(path).read_bytes(),
            }
        )


async def test_put_file_streams_handle(tmp_path: Path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"payload")
    client = TOSClient("https://tos-cn-beijing.volces.com", "cn-beijing", "ak", "sk", "bucket")
    sdk = _Sdk()
    client._client = sdk
    assert await client.put_file("k/a.bin", str(path), "text/plain") == "k/a.bin"
    call = sdk.calls[0]
    assert call["bytes"] == b"payload"
    assert call["content_length"] == 7
    assert call["content_type"] == "text/plain"
