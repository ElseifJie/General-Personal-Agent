"""Outbox lease/reclaim and per-conversation ordering (unit)."""

from __future__ import annotations

import time

from tests.fakes import FakeDatabase

from general_personal_agent.outbox import OutboxWorker, enqueue


async def _seed(db: FakeDatabase):
    await enqueue(
        db, "demo", "alice", "c1", "ov.message",
        {"role": "user", "content": "one"}, idempotency_key="c1:1",
    )
    await enqueue(
        db, "demo", "alice", "c1", "ov.message",
        {"role": "assistant", "content": "two"}, idempotency_key="c1:2",
    )
    await enqueue(
        db, "demo", "alice", "c2", "ov.message",
        {"role": "user", "content": "other"}, idempotency_key="c2:1",
    )


async def test_claim_blocks_later_rows_per_conversation():
    db = FakeDatabase()
    await _seed(db)
    claimed = await db.claim_outbox(10)
    assert [row["conversation_id"] for row in claimed] == ["c1", "c2"]
    # c1:2 is blocked by the still-inflight c1:1.
    assert await db.claim_outbox(10) == []


async def test_expired_lease_is_reclaimed_with_a_new_token():
    db = FakeDatabase()
    await _seed(db)
    first = (await db.claim_outbox(10))[0]
    row = db.outbox[first["id"]]
    row["lease_until"] = time.monotonic() - 1
    reclaimed = await db.claim_outbox(10)
    assert reclaimed and reclaimed[0]["id"] == first["id"]
    assert reclaimed[0]["claim_token"] != first["claim_token"]
    # The stale worker cannot complete the reclaimed row.
    assert await db.complete_outbox(first["id"], first["claim_token"]) is False
    assert await db.complete_outbox(reclaimed[0]["id"], reclaimed[0]["claim_token"]) is True
    # Now the next row in c1 becomes eligible.
    nxt = await db.claim_outbox(10)
    assert nxt and nxt[0]["id"] == db.outbox[2]["id"]


async def test_failed_message_blocks_later_rows_until_it_is_done():
    db = FakeDatabase()
    await _seed(db)
    claimed = await db.claim_outbox(10)
    first = claimed[0]
    await db.fail_outbox(
        first["id"], 1, 60, "temporary", False, claim_token=first["claim_token"]
    )
    # c1:1 is pending but in backoff; c1:2 must not overtake it.
    remaining = await db.claim_outbox(10)
    assert [row["conversation_id"] for row in remaining] == []


async def test_worker_uses_claim_token(monkeypatch):
    db = FakeDatabase()
    await _seed(db)
    seen: list[str] = []

    async def handler(payload):
        seen.append(payload["content"])

    worker = OutboxWorker(db, {"ov.message": handler}, max_attempts=2)
    assert await worker.sweep_once() == 3
    assert seen == ["one", "other", "two"]
