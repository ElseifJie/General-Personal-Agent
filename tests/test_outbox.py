"""T5/T6 outbox: idempotency, ordering, retry with backoff, dead-letter, crash resume."""

import asyncio

from tests.fakes import FakeDatabase

from general_personal_agent.outbox import OutboxWorker, enqueue


async def enqueue_turn(db, turn_id, user="你好", assistant="你好呀"):
    ok_user = await enqueue(
        db, "demo", "alice", "c1", "ov.message",
        {"session_id": "s1", "role": "user", "content": user},
        idempotency_key=f"{turn_id}:user",
    )
    ok_asst = await enqueue(
        db, "demo", "alice", "c1", "ov.message",
        {"session_id": "s1", "role": "assistant", "content": assistant},
        idempotency_key=f"{turn_id}:assistant",
    )
    return ok_user, ok_asst


async def test_enqueue_is_idempotent_and_ordered():
    db = FakeDatabase()
    assert (await enqueue_turn(db, "t1")) == (True, True)
    assert (await enqueue_turn(db, "t1")) == (False, False), "replay must not duplicate"
    await enqueue_turn(db, "t2")
    rows = await db.list_outbox("c1")
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs), "per-conversation seq must be monotonic"
    assert [r["idempotency_key"] for r in rows][-1] == "t2:assistant"
    roles = [r["payload"]["role"] for r in rows]
    assert roles == ["user", "assistant"] * 2, "user precedes assistant within a turn"


async def test_worker_preserves_order_and_delivers_once():
    db = FakeDatabase()
    for i in range(3):
        await enqueue_turn(db, f"t{i}", user=f"q{i}", assistant=f"a{i}")
    delivered = []

    async def handler(payload):
        delivered.append((payload["role"], payload["content"]))

    worker = OutboxWorker(db, {"ov.message": handler}, max_attempts=3)
    assert await worker.sweep_once() == 6
    assert delivered == [
        ("user", "q0"), ("assistant", "a0"),
        ("user", "q1"), ("assistant", "a1"),
        ("user", "q2"), ("assistant", "a2"),
    ], "user/assistant order must be preserved (T6)"
    # replay sweep delivers nothing new
    assert await worker.sweep_once() == 0


async def test_retry_with_backoff_then_success():
    db = FakeDatabase()
    await enqueue(db, "demo", "alice", "c1", "ov.message", {"content": "x"},
                  idempotency_key="t1:user")
    attempts = {"n": 0}

    async def flaky(payload):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("ov unavailable")

    worker = OutboxWorker(db, {"ov.message": flaky}, max_attempts=8)
    await worker.sweep_once()
    row = next(iter(db.outbox.values()))
    assert row["status"] == "pending" and row["attempts"] == 1
    row["next_at"] = 0.0  # simulate backoff elapsed
    await worker.sweep_once()
    assert row["attempts"] == 2 and row["status"] == "pending"
    row["next_at"] = 0.0
    await worker.sweep_once()
    assert row["status"] == "done", "third attempt succeeds"


async def test_dead_letter_after_max_attempts():
    db = FakeDatabase()
    await enqueue_turn(db, "t1")

    async def always_fails(payload):
        raise RuntimeError("permanent")

    worker = OutboxWorker(db, {"ov.message": always_fails}, max_attempts=2)
    await worker.sweep_once()
    row = next(iter(db.outbox.values()))
    assert row["status"] == "pending" and row["attempts"] == 1
    row["next_at"] = 0.0
    await worker.sweep_once()
    assert row["status"] == "dead" and row["attempts"] == 2
    assert "permanent" in (row["last_error"] or "")


async def test_crash_resume_new_worker_drains_backlog():
    """T5: enqueue -> 'kill' (never swept) -> a fresh worker processes everything."""
    db = FakeDatabase()
    for i in range(4):
        await enqueue_turn(db, f"t{i}")
    delivered = []

    async def handler(payload):
        delivered.append(payload["content"])

    fresh = OutboxWorker(db, {"ov.message": handler})
    await fresh.sweep_once()
    assert len(delivered) == 8
    assert await db.pending_outbox_count() == 0


async def test_unknown_kind_fails_and_retries_not_crashes():
    db = FakeDatabase()
    await enqueue(db, "demo", "alice", "c1", "ov.unknown", {}, idempotency_key="x1")
    worker = OutboxWorker(db, {})
    await worker.sweep_once()  # must not raise
    row = next(iter(db.outbox.values()))
    assert row["status"] == "pending" and row["attempts"] == 1


async def test_worker_lifecycle_start_stop():
    db = FakeDatabase()
    await enqueue_turn(db, "t1")
    delivered = []

    async def handler(payload):
        delivered.append(payload["role"])

    worker = OutboxWorker(db, {"ov.message": handler}, interval_s=0.01)
    worker.start()
    for _ in range(50):
        if delivered:
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    assert len(delivered) == 2
