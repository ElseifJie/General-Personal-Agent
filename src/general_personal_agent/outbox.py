"""A4 Outbox: reliable delivery to OpenViking with idempotency, ordering,
retry with exponential backoff, and dead-lettering.

Deployment (plan §4.3): V0.5 runs an in-process asyncio sweeper that claims
pending rows via FOR UPDATE SKIP LOCKED and processes them sequentially (which
preserves per-conversation order). T5 validates with kill -9 + restart: state
lives in PG, so a fresh process resumes the backlog.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from general_personal_agent.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[[dict], Awaitable[None]]
BACKOFF_SCHEDULE = (1.0, 5.0, 30.0, 120.0, 600.0)


class OutboxWorker:
    def __init__(
        self,
        db,
        handlers: dict[str, Handler],
        *,
        interval_s: float = 1.0,
        batch_size: int = 50,
        max_attempts: int = 8,
    ):
        self.db = db
        self.handlers = handlers
        self.interval_s = interval_s
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="outbox-worker")

    async def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except TimeoutError:
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.sweep_once()
            except Exception:  # noqa: BLE001 - the worker must never die
                logger.exception("outbox sweep failed")
                processed = 0
            if processed == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
                except TimeoutError:
                    pass

    # ---------------------------------------------------------------- sweep

    async def sweep_once(self) -> int:
        """Drain available rows while preserving per-conversation order.

        ``claim_outbox`` returns at most one row per conversation; a failed
        message blocks its conversation's later rows.  Looping here lets a
        single sweep drain the other conversations and the next eligible row
        without letting a later message overtake a retrying one.
        """
        total = 0
        for _ in range(1000):
            items = await self.db.claim_outbox(self.batch_size)
            if not items:
                break
            for item in items:
                await self._process(item)
            total += len(items)
        return total

    async def _process(self, item: dict[str, Any]) -> None:
        handler = self.handlers.get(item["kind"])
        attempts = int(item.get("attempts") or 0) + 1
        try:
            if handler is None:
                raise RuntimeError(f"no handler for outbox kind {item['kind']}")
            await handler(item["payload"])
        except asyncio.CancelledError:
            # Crash/stop mid-flight: row stays inflight; a restart re-queues it.
            raise
        except Exception as exc:  # noqa: BLE001 - any handler failure retries
            dead = attempts >= self.max_attempts
            delay = BACKOFF_SCHEDULE[min(attempts - 1, len(BACKOFF_SCHEDULE) - 1)]
            await self.db.fail_outbox(
                item["id"], attempts, delay, str(exc), dead,
                claim_token=item.get("claim_token"),
            )
            if dead:
                logger.error("outbox dead letter id=%s kind=%s: %s", item["id"], item["kind"], exc)
            else:
                logger.warning(
                    "outbox retry id=%s kind=%s attempt=%s in %ss: %s",
                    item["id"], item["kind"], attempts, delay, exc,
                )
            return
        await self.db.complete_outbox(item["id"], item.get("claim_token"))


async def enqueue(
    db,
    tenant_id: str,
    app_user_id: str,
    conversation_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> bool:
    return await db.enqueue_outbox(
        {
            "tenant_id": tenant_id,
            "app_user_id": app_user_id,
            "conversation_id": conversation_id,
            "kind": kind,
            "payload": payload,
            "idempotency_key": idempotency_key,
        }
    )
