"""Real TOS smoke: upload -> server-side copy -> ranged read -> delete."""

from __future__ import annotations

import asyncio
import uuid

from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.settings import get_settings


async def main() -> None:
    settings = get_settings()
    tos = TOSClient(
        settings.tos_endpoint,
        settings.database_tos_region,
        settings.volcengine_access_key,
        settings.volcengine_secret_key,
        settings.database_tos_bucket_name,
        session_token=settings.volcengine_session_token,
        prefix=settings.tos_prefix,
    )
    tag = uuid.uuid4().hex[:10]
    source = tos.object_key("source-e2e", f"{tag}.txt")
    target = tos.object_key("tasks", "artifact-e2e", f"{tag}.txt")
    payload = b"0123456789abcdef"
    try:
        await tos.put_bytes(source, payload, content_type="text/plain")
        await tos.copy_object(source, target)
        head = b"".join([chunk async for chunk in tos.iter_range(target, 0, 4)])
        assert head == payload[:5], head
        body, content_type = await tos.get_bytes(target)
        assert body == payload
        print("TOS artifact copy/range PASS", target, content_type)
    finally:
        for key in (target, source):
            try:
                await tos.delete(key)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    asyncio.run(main())
