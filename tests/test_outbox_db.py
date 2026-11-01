"""Real-Postgres outbox claim/lease behavior (the CTE is not exercised by fakes)."""

from __future__ import annotations

import pytest

from general_personal_agent.outbox import enqueue

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.integration
async def test_real_claim_orders_one_row_per_conversation_and_reclaims(gpa_env):
    db = gpa_env.db
    user = gpa_env.user
    for seq, key in enumerate(("a", "b")):
        await enqueue(db, "demo", user, "c1", "ov.message", {"n": seq}, f"c1:{key}")
    await enqueue(db, "demo", user, "c2", "ov.message", {"n": 2}, "c2:a")

    first = await db.claim_outbox(10)
    assert len(first) == 2
    assert {row["conversation_id"] for row in first} == {"c1", "c2"}
    c1 = next(row for row in first if row["conversation_id"] == "c1")
    assert c1["payload"]["n"] == 0
    assert c1["claim_token"]

    # The next c1 row is blocked until the first one completes.
    assert await db.complete_outbox(c1["id"], c1["claim_token"]) is True
    nxt = await db.claim_outbox(10)
    assert [row["conversation_id"] for row in nxt] == ["c1"]
    assert nxt[0]["payload"]["n"] == 1

    # Stale tokens cannot complete a reclaimed row.
    await db.pool.execute(
        "UPDATE context_outbox SET status='inflight', lease_until=now() - interval '1 second'"
        " WHERE id=$1",
        nxt[0]["id"],
    )
    reclaimed = await db.claim_outbox(10)
    assert reclaimed[0]["id"] == nxt[0]["id"]
    assert await db.complete_outbox(nxt[0]["id"], nxt[0]["claim_token"]) is False
    assert await db.complete_outbox(reclaimed[0]["id"], reclaimed[0]["claim_token"]) is True


@pytest.mark.integration
async def test_legacy_inflight_without_lease_is_reclaimed(gpa_env):
    db = gpa_env.db
    user = gpa_env.user
    await db.pool.execute(
        """
        INSERT INTO context_outbox
          (tenant_id, app_user_id, conversation_id, kind, payload,
           idempotency_key, seq, status, lease_until)
        VALUES ('demo', $1, 'legacy-c', 'ov.message', '{"n":0}'::jsonb,
                'legacy:0', 1, 'inflight', NULL)
        """,
        user,
    )
    await enqueue(db, "demo", user, "legacy-c", "ov.message", {"n": 1}, "legacy:1")
    first = await db.claim_outbox(10)
    assert len(first) == 1
    assert first[0]["idempotency_key"] == "legacy:0"
    assert first[0]["claim_token"]
    assert await db.complete_outbox(first[0]["id"], first[0]["claim_token"]) is True
    second = await db.claim_outbox(10)
    assert [row["idempotency_key"] for row in second] == ["legacy:1"]
