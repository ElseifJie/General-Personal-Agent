"""Postgres + Redis smoke."""

from __future__ import annotations

import asyncpg
import redis.asyncio as redis

from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    conn = await asyncpg.connect(settings.db_dsn)
    try:
        tables = [
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        ]
        info("tables", ", ".join(tables))
        ok = check(len(tables) >= 8, "postgres tables", str(len(tables)))
        for table, expected in (("app_user", 3), ("ov_identity", 3)):
            count = await conn.fetchval(f"SELECT count(*) FROM {table}")
            ok &= check(count >= expected, f"{table} seed", str(count))
        rows = await conn.fetch("SELECT app_user_id, ov_user FROM ov_identity ORDER BY 1")
        info("identities", ", ".join(f"{r['app_user_id']}->{r['ov_user']}" for r in rows))
    finally:
        await conn.close()

    client = redis.from_url(settings.redis_url)
    try:
        ok &= check(bool(await client.ping()), "redis ping")
        await client.set("general-personal-agent:smoke", "ok", ex=60)
        ok &= check(await client.get("general-personal-agent:smoke") == b"ok", "redis set/get")
        await client.delete("general-personal-agent:smoke")
    finally:
        await client.aclose()
    return ok


if __name__ == "__main__":
    run(main)
