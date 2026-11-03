"""TaskEventInbox: project async task events into the conversation timeline.

MVP is passive (ADR-007): the inbox writes conversation_event + marks
task_event.projected_at atomically; it never generates an Agent turn. UI sees
task cards via SSE and the next context assembly includes task_context.
"""

from __future__ import annotations

import asyncio

from general_personal_agent.gpa.conversation.store import ConversationStore
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.task.store import TaskStore

logger = get_logger(__name__)

_STATUS_TYPES = {"status", "progress", "completed", "partial", "failed", "cancelled"}


class TaskEventInbox:
    def __init__(
        self,
        *,
        settings,
        task_store: TaskStore,
        conversation_store: ConversationStore,
        bus=None,
        interval_s: float | None = None,
        batch_size: int | None = None,
    ):
        self.settings = settings
        self.task_store = task_store
        self.conversation_store = conversation_store
        self.bus = bus
        self.interval_s = interval_s or settings.gpa_inbox_interval_s
        self.batch_size = batch_size or settings.gpa_inbox_batch_size
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self.last_run_at = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="gpa-task-event-inbox")

    async def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except TimeoutError:
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:  # noqa: BLE001 - worker must never die
                logger.exception("task event inbox sweep failed")
                processed = 0
            if processed == 0:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.interval_s)
                except TimeoutError:
                    pass

    async def run_once(self) -> int:
        rows = await self.task_store.db.pool.fetch(
            "SELECT event_id FROM task_event WHERE projected_at IS NULL ORDER BY occurred_at, seq LIMIT $1",
            self.batch_size,
        )
        processed = 0
        for row in rows:
            try:
                if await self._project(row["event_id"]):
                    processed += 1
            except Exception:  # noqa: BLE001 - one poison event must not block the queue
                logger.exception("task event projection failed event=%s", row["event_id"])
        self.last_run_at = asyncio.get_running_loop().time()
        return processed

    async def _project(self, event_id: str) -> bool:
        async with self.task_store.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT e.*, t.conversation_id, t.tenant_id, t.app_user_id,
                           t.status AS task_status, t.progress AS task_progress,
                           t.summary AS task_summary, t.executor, t.task_type, t.goal
                      FROM task_event e
                      JOIN task_run t ON t.task_id = e.task_id
                     WHERE e.event_id = $1 AND e.projected_at IS NULL
                     FOR UPDATE OF e SKIP LOCKED
                    """,
                    event_id,
                )
                if row is None:
                    return False
                payload = dict(row["payload"] or {})
                event_type = self._conversation_type(row["type"])
                merged = {
                    "taskId": row["task_id"],
                    "conversationId": row["conversation_id"],
                    "status": payload.get("status") or row["task_status"],
                    "progress": payload.get("progress", row["task_progress"]),
                    "summary": payload.get("summary", row["task_summary"]),
                    "taskType": row["task_type"],
                    "executor": row["executor"],
                    "goal": row["goal"],
                    **payload,
                }
                stored = await self.conversation_store.append_event(
                    conn,
                    conversation_id=row["conversation_id"],
                    type=event_type,
                    payload=merged,
                    task_id=row["task_id"],
                    event_id=f"te_{event_id}",
                )
                await conn.execute("UPDATE task_event SET projected_at = now() WHERE event_id = $1", event_id)
        if stored and self.bus is not None:
            self.bus.publish(row["conversation_id"], stored)
        return bool(stored)

    @staticmethod
    def _conversation_type(task_event_type: str) -> str:
        if task_event_type == "created":
            return "task.created"
        if task_event_type == "artifact":
            return "artifact"
        if task_event_type in _STATUS_TYPES:
            return "task.status"
        return "task.status"
