"""Minimal Postgres access layer used by the GPA runtime."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import asyncpg

from general_personal_agent.settings import Settings


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, settings: Settings) -> Database:
        pool = await asyncpg.create_pool(dsn=settings.db_dsn, min_size=1, max_size=8, init=_init_connection)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def get_app_user(self, app_user_id: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT app_user_id, display_name, role, status FROM app_user WHERE app_user_id = $1",
            app_user_id,
        )
        return dict(row) if row else None

    async def list_app_users(self) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT app_user_id, display_name, role FROM app_user ORDER BY app_user_id"
        )
        return [dict(r) for r in rows]

    async def get_identity(self, tenant_id: str, app_user_id: str) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT i.tenant_id, i.app_user_id, i.ov_account, i.ov_user, i.ov_peer,
                   i.key_ref, i.status, u.role
              FROM ov_identity i
              LEFT JOIN app_user u ON u.app_user_id = i.app_user_id
             WHERE i.tenant_id = $1 AND i.app_user_id = $2
            """,
            tenant_id,
            app_user_id,
        )
        return dict(row) if row else None

    async def purge_context_traces(self, ttl_days: int) -> None:
        await self.pool.execute(
            "DELETE FROM context_trace WHERE created_at < now() - ($1 || ' days')::interval",
            str(ttl_days),
        )

    async def enqueue_outbox(self, item: dict[str, Any]) -> bool:
        """Insert one outbox row with an atomic per-conversation seq."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                item.get("conversation_id", ""),
            )
            seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM context_outbox"
                " WHERE conversation_id = $1",
                item.get("conversation_id", ""),
            )
            inserted = await conn.execute(
                """
                INSERT INTO context_outbox
                  (tenant_id, app_user_id, conversation_id, kind, payload,
                   idempotency_key, seq)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                item["tenant_id"],
                item["app_user_id"],
                item.get("conversation_id", ""),
                item["kind"],
                item.get("payload") or {},
                item["idempotency_key"],
                int(seq or 1),
            )
            return inserted == "INSERT 0 1"

    async def next_outbox_seq(self, conversation_id: str) -> int:
        value = await self.pool.fetchval(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM context_outbox WHERE conversation_id = $1",
            conversation_id,
        )
        return int(value or 1)

    async def claim_outbox(self, limit: int) -> list[dict]:
        """Claim one available row per conversation, reclaiming expired leases.

        Picking exactly one row per conversation is what preserves per-
        conversation order: a failed message in backoff blocks later messages
        and the final commit instead of being overtaken by them.
        """
        token = uuid4().hex
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                WITH picked AS (
                  SELECT DISTINCT ON (o.conversation_id) o.id
                    FROM context_outbox o
                   WHERE ((o.status = 'pending' AND o.next_at <= now())
                          OR (o.status = 'inflight'
                              AND (o.lease_until IS NULL OR o.lease_until < now())))
                     AND NOT EXISTS (
                       SELECT 1 FROM context_outbox p
                        WHERE p.conversation_id = o.conversation_id
                          AND p.status IN ('pending', 'inflight')
                          AND p.seq < o.seq
                     )
                   ORDER BY o.conversation_id, o.seq, o.id
                   LIMIT $1
                )
                UPDATE context_outbox c
                   SET status='inflight', claim_token=$2,
                       lease_until=now() + interval '2 minutes', updated_at=now()
                  FROM picked p
                 WHERE c.id = p.id
                   AND (c.status = 'pending'
                        OR (c.status = 'inflight'
                            AND (c.lease_until IS NULL OR c.lease_until < now())))
                RETURNING c.*
                """,
                limit,
                token,
            )
            return [dict(row) for row in rows]

    async def complete_outbox(self, outbox_id: int, claim_token: str | None = None) -> bool:
        status = await self.pool.execute(
            """
            UPDATE context_outbox
               SET status='done', lease_until=NULL, claim_token=NULL, updated_at=now()
             WHERE id=$1 AND ($2::text IS NULL OR claim_token=$2)
            """,
            outbox_id,
            claim_token,
        )
        return status == "UPDATE 1"

    async def fail_outbox(
        self, outbox_id: int, attempts: int, delay_s: float, error: str, dead: bool,
        claim_token: str | None = None,
    ) -> bool:
        status = await self.pool.execute(
            """
            UPDATE context_outbox
               SET status = CASE WHEN $5 THEN 'dead' ELSE 'pending' END,
                   attempts = $2, next_at = now() + ($3 || ' seconds')::interval,
                   last_error = $4, lease_until=NULL, claim_token=NULL,
                   updated_at = now()
             WHERE id = $1 AND ($6::text IS NULL OR claim_token = $6)
            """,
            outbox_id,
            attempts,
            str(delay_s),
            error[:500],
            dead,
            claim_token,
        )
        return status == "UPDATE 1"

    async def list_outbox(self, conversation_id: str) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT * FROM context_outbox WHERE conversation_id = $1 ORDER BY id",
            conversation_id,
        )
        return [dict(r) for r in rows]

    async def pending_outbox_count(self) -> int:
        value = await self.pool.fetchval(
            "SELECT COUNT(*) FROM context_outbox WHERE status IN ('pending','inflight')"
        )
        return int(value or 0)
