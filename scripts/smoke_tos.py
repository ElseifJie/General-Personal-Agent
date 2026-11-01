"""TOS smoke: put, signed GET, delete."""

from __future__ import annotations

import httpx

from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    tos = TOSClient(
        settings.tos_endpoint,
        settings.database_tos_region,
        settings.volcengine_access_key,
        settings.volcengine_secret_key,
        settings.database_tos_bucket_name,
        session_token=settings.volcengine_session_token,
        prefix=settings.tos_prefix,
    )
    key = tos.object_key("_smoke", f"{tos.stamp()}.txt")
    payload = b"tos smoke ok"
    await tos.put_bytes(key, payload, "text/plain")
    info("uploaded", key)
    url = tos.signed_url(key, settings.tos_signed_url_ttl_s)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url)
    ok = check(resp.status_code == 200 and resp.content == payload, "signed url GET", str(resp.status_code))
    await tos.delete(key)
    info("deleted", key)
    return ok


if __name__ == "__main__":
    run(main)
