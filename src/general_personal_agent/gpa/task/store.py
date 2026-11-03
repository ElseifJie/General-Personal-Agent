"""Task control-plane persistence (task_run / task_event / task_artifact)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from general_personal_agent.gpa.files.store import FileUnavailableError
from general_personal_agent.gpa.ids import new_artifact_id, new_task_id


def payload_fingerprint(
    *,
    goal: str,
    task_type: str,
    brief: dict[str, Any] | None,
    executor: str,
    attachment_ids: list[str],
) -> str:
    """Stable fingerprint of every execution-relevant submit parameter.

    Idempotency must never collapse two different requests just because they
    share a key.  Attachments are represented by their file ids (the caller
    validates ownership and existence before this runs).
    """
    material = json.dumps(
        {
            "goal": goal.strip(),
            "task_type": task_type,
            "brief": brief or {},
            "executor": executor,
            "attachments": sorted(attachment_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


class TaskNotFound(LookupError):
    pass


class TaskStore:
    def __init__(self, db):
        self.db = db

    async def create_task(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        conversation_id: str,
        goal: str,
        task_type: str,
        brief: dict[str, Any] | None = None,
        origin_turn_id: str | None = None,
        executor: str = "ma",
        idempotency_key: str | None = None,
        payload_fingerprint: str | None = None,
        input_files: list[dict[str, Any]] | None = None,
    ) -> tuple[dict, bool]:
        """Create a queued task and its input rows atomically.

        The worker must never observe a queued task before its attachments are
        attached: task_run, task_input_file and the created event share one
        transaction.
        """
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                if idempotency_key:
                    existing = await conn.fetchrow(
                        """
                        SELECT * FROM task_run
                         WHERE tenant_id = $1 AND app_user_id = $2 AND idempotency_key = $3
                        """,
                        tenant_id,
                        app_user_id,
                        idempotency_key,
                    )
                    if existing is not None:
                        return dict(existing), False
                task_id = new_task_id()
                row = await conn.fetchrow(
                    """
                    INSERT INTO task_run
                      (task_id, tenant_id, app_user_id, conversation_id, origin_turn_id,
                       goal, task_type, brief, executor, status, idempotency_key,
                       payload_fingerprint)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'queued', $10, $11)
                    RETURNING *
                    """,
                    task_id,
                    tenant_id,
                    app_user_id,
                    conversation_id,
                    origin_turn_id,
                    goal,
                    task_type,
                    brief or {},
                    executor,
                    idempotency_key,
                    payload_fingerprint,
                )
                for item in input_files or []:
                    locked = await conn.fetchrow(
                        """
                        SELECT file_id FROM file_object
                         WHERE file_id=$1 AND deleted_at IS NULL
                           AND gc_state NOT IN ('deleting','deleted')
                         FOR UPDATE
                        """,
                        item["file_id"],
                    )
                    if locked is None:
                        raise FileUnavailableError("file is being deleted")
                    await conn.execute(
                        """
                        INSERT INTO task_input_file
                          (task_input_id, task_id, attempt_id, file_id, mount_path)
                        VALUES ($1, $2, NULL, $3, $4)
                        ON CONFLICT (task_id, file_id) DO UPDATE
                          SET mount_path = EXCLUDED.mount_path
                        """,
                        item["task_input_id"],
                        task_id,
                        item["file_id"],
                        item["mount_path"],
                    )
                    await conn.execute(
                        """
                        UPDATE file_object
                           SET gc_state='active', gc_candidate_at=NULL, updated_at=now()
                         WHERE file_id=$1 AND gc_state='candidate'
                        """,
                        item["file_id"],
                    )
                await self._append_event_conn(
                    conn,
                    task_id=task_id,
                    type="created",
                    payload={
                        "taskId": task_id,
                        "status": "queued",
                        "goal": goal,
                        "taskType": task_type,
                        "executor": executor,
                    },
                )
                return dict(row), True

    async def get(self, task_id: str) -> dict | None:
        row = await self.db.pool.fetchrow("SELECT * FROM task_run WHERE task_id = $1", task_id)
        return dict(row) if row else None

    async def get_for_owner(self, task_id: str, tenant_id: str, app_user_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            "SELECT * FROM task_run WHERE task_id = $1 AND tenant_id = $2 AND app_user_id = $3",
            task_id,
            tenant_id,
            app_user_id,
        )
        return dict(row) if row else None

    async def list(
        self,
        *,
        conversation_id: str | None = None,
        status: str | None = None,
        tenant_id: str | None = None,
        app_user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM task_run
             WHERE ($1::text IS NULL OR conversation_id = $1)
               AND ($2::text IS NULL OR status = $2)
               AND ($3::text IS NULL OR tenant_id = $3)
               AND ($4::text IS NULL OR app_user_id = $4)
             ORDER BY created_at DESC
             LIMIT $5
            """,
            conversation_id,
            status,
            tenant_id,
            app_user_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        summary: str | None = None,
        error: dict[str, Any] | None = None,
        executor_ref: str | None = None,
        executor_meta: dict[str, Any] | None = None,
    ) -> dict | None:
        row = await self.db.pool.fetchrow(
            """
            UPDATE task_run
               SET status = COALESCE($2, status),
                   progress = COALESCE($3, progress),
                   summary = COALESCE($4, summary),
                   error = COALESCE($5, error),
                   executor_ref = COALESCE($6, executor_ref),
                   executor_meta = COALESCE($7, executor_meta)
             WHERE task_id = $1
             RETURNING *
            """,
            task_id,
            status,
            progress,
            summary,
            error,
            executor_ref,
            executor_meta,
        )
        return dict(row) if row else None

    async def append_event(self, task_id: str, type: str, payload: dict[str, Any] | None = None) -> dict:
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                return await self._append_event_conn(conn, task_id=task_id, type=type, payload=payload or {})

    async def _append_event_conn(self, conn, *, task_id: str, type: str, payload: dict[str, Any]) -> dict:
        # Lock the task row so concurrent appends get one ordered sequence.
        await conn.fetchrow("SELECT task_id FROM task_run WHERE task_id = $1 FOR UPDATE", task_id)
        seq = await conn.fetchval(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_event WHERE task_id = $1", task_id
        )
        row = await conn.fetchrow(
            """
            INSERT INTO task_event (event_id, task_id, seq, type, payload)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            f"te_{task_id}_{int(seq)}",
            task_id,
            int(seq),
            type,
            payload,
        )
        await conn.execute("UPDATE task_run SET updated_at = now() WHERE task_id = $1", task_id)
        return dict(row)

    async def cancel(self, task_id: str, reason: str = "") -> dict | None:
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT * FROM task_run WHERE task_id = $1 FOR UPDATE", task_id)
                if row is None:
                    return None
                if row["status"] in ("completed", "partial", "failed", "cancelled"):
                    return dict(row)
                updated = await conn.fetchrow(
                    """
                    UPDATE task_run SET status = 'cancelled',
                           summary = CASE WHEN $2 = '' THEN summary ELSE $2 END,
                           error = $3
                     WHERE task_id = $1 RETURNING *
                    """,
                    task_id,
                    reason,
                    {"code": "CANCELLED", "message": reason or "cancelled by user"},
                )
                await self._append_event_conn(
                    conn,
                    task_id=task_id,
                    type="cancelled",
                    payload={"taskId": task_id, "status": "cancelled", "reason": reason},
                )
                return dict(updated)

    async def list_artifacts(self, task_id: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT * FROM task_artifact WHERE task_id = $1 ORDER BY created_at", task_id
        )
        return [dict(r) for r in rows]

    async def get_artifact(self, artifact_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            "SELECT a.*, t.tenant_id, t.app_user_id, t.conversation_id"
            " FROM task_artifact a JOIN task_run t ON t.task_id = a.task_id"
            " WHERE a.artifact_id = $1",
            artifact_id,
        )
        return dict(row) if row else None

    async def add_artifact(
        self,
        *,
        task_id: str,
        name: str,
        mime: str | None = None,
        bytes: int | None = None,
        storage_ref: str | None = None,
        artifact_id: str | None = None,
        ttl_days: int = 7,
        source_ref: str | None = None,
        checksum: str | None = None,
    ) -> dict:
        artifact_id = artifact_id or new_artifact_id()
        row = await self.db.pool.fetchrow(
            """
            INSERT INTO task_artifact
              (artifact_id, task_id, name, mime, bytes, storage_ref, expires_at, source_ref, checksum)
            VALUES ($1, $2, $3, $4, $5, $6, now() + ($7 || ' days')::interval, $8, $9)
            ON CONFLICT (artifact_id) DO NOTHING
            RETURNING *
            """,
            artifact_id,
            task_id,
            name,
            mime,
            bytes,
            storage_ref,
            str(max(1, ttl_days)),
            source_ref,
            checksum,
        )
        if row is not None:
            return dict(row)
        return await self.get_artifact(artifact_id) or {}

    async def claim_next_ma(self, owner_id: str | None = None) -> dict | None:
        return await self.claim_next("ma", owner_id=owner_id)

    async def claim_next(self, executor: str = "ma", owner_id: str | None = None) -> dict | None:
        """Atomically claim one queued task for an executor and create its first attempt."""
        owner_id = owner_id or f"worker_{uuid4().hex[:12]}"

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM task_run
                     WHERE status = 'queued' AND executor = $1
                       AND (next_retry_at IS NULL OR next_retry_at <= now())
                     ORDER BY created_at
                     LIMIT 1
                     FOR UPDATE SKIP LOCKED
                    """,
                    executor,
                )
                if row is None:
                    return None
                task_id = row["task_id"]
                updated = await conn.fetchrow(
                    """
                    UPDATE task_run
                       SET status = 'running', progress = 0.02, summary = '执行中',
                           owner_id = $2, heartbeat_at = now(), next_retry_at = NULL
                     WHERE task_id = $1
                     RETURNING *
                    """,
                    task_id,
                    owner_id,
                )
                attempt = await conn.fetchrow(
                    """
                    INSERT INTO task_attempt (attempt_id, task_id, seq, status)
                    VALUES ($1, $2,
                            (SELECT COALESCE(MAX(seq), 0) + 1
                               FROM task_attempt WHERE task_id = $2),
                            'running')
                    RETURNING *
                    """,
                    uuid4(),
                    task_id,
                )
                event = await self._append_event_conn(
                    conn,
                    task_id=task_id,
                    type="status",
                    payload={
                        "taskId": task_id,
                        "status": "running",
                        "progress": 0.02,
                        "summary": "执行中",
                    },
                )
                return {
                    "task": dict(updated),
                    "attempt": dict(attempt),
                    "event": event,
                }

    async def attach_attempt(self, task_id: str, attempt_id: str, executor_ref: str) -> None:
        await self.db.pool.execute(
            "UPDATE task_attempt SET executor_ref = $2 WHERE attempt_id = $1",
            attempt_id,
            executor_ref,
        )
        await self.db.pool.execute(
            "UPDATE task_run SET executor_ref = $2 WHERE task_id = $1",
            task_id,
            executor_ref,
        )

    async def heartbeat(self, task_id: str, owner_id: str) -> bool:
        """Refresh the lease of a running task owned by this worker."""
        row = await self.db.pool.fetchrow(
            """
            UPDATE task_run
               SET heartbeat_at = now(), updated_at = now()
             WHERE task_id = $1 AND owner_id = $2 AND status = 'running'
             RETURNING task_id
            """,
            task_id,
            owner_id,
        )
        return row is not None

    async def attempt_count(self, task_id: str) -> int:
        """How many attempts this task already used (including the running one)."""
        value = await self.db.pool.fetchval(
            "SELECT COUNT(*) FROM task_attempt WHERE task_id = $1", task_id
        )
        return int(value or 0)

    async def requeue(
        self,
        task_id: str,
        *,
        owner_id: str,
        error: dict[str, Any],
        summary: str = "任务重试排队中",
        delay_s: float = 0.0,
    ) -> bool:
        """Put a running task back in the queue for another attempt.

        Only the worker holding the lease may requeue, and only while the task
        is still running: a cancelled or finished task is never resurrected.
        The just-failed attempt is closed so ``claim_next`` opens ``seq + 1``.

        ``executor_ref`` is cleared here: the session it names belongs to the
        attempt that just failed, and a retry must not salvage files from it.
        ``delay_s`` pushes ``next_retry_at`` into the future so the same worker
        cannot immediately claim the task back and burn every retry at once.
        """
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE task_run
                       SET status = 'queued', progress = 0, summary = $3,
                           error = $4, owner_id = NULL, heartbeat_at = NULL,
                           executor_ref = NULL,
                           next_retry_at = CASE
                               WHEN $5::double precision > 0
                               THEN now() + make_interval(secs => $5::double precision)
                               ELSE NULL
                           END
                     WHERE task_id = $1 AND status = 'running' AND owner_id = $2
                     RETURNING task_id
                    """,
                    task_id,
                    owner_id,
                    summary,
                    error,
                    delay_s,
                )
                if row is None:
                    return False
                await conn.execute(
                    """
                    UPDATE task_attempt
                       SET status = 'failed', error = $2, ended_at = now()
                     WHERE task_id = $1 AND status = 'running'
                    """,
                    task_id,
                    error,
                )
                await self._append_event_conn(
                    conn,
                    task_id=task_id,
                    type="status",
                    payload={
                        "taskId": task_id,
                        "status": "queued",
                        "progress": 0.0,
                        "summary": summary,
                        "error": error,
                    },
                )
                return True

    async def finish(
        self,
        task_id: str,
        *,
        status: str,
        progress: float,
        summary: str,
        error: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        owner_id: str | None = None,
    ) -> bool:
        """Finish a task only if it is still in a non-terminal state.

        Cancellation is authoritative: once ``status='cancelled'`` the executor
        must not resurrect the task or append artifacts.  The conditional UPDATE
        is the serialization point; artifacts and the terminal event are written
        only after it succeeds.
        """
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchrow(
                    """
                    UPDATE task_run
                       SET status = $2, progress = $3, summary = $4, error = $5,
                           heartbeat_at = now()
                     WHERE task_id = $1
                       AND status IN ('queued', 'running')
                       AND ($6::text IS NULL OR owner_id = $6)
                     RETURNING task_id
                    """,
                    task_id,
                    status,
                    progress,
                    summary,
                    error,
                    owner_id,
                )
                if updated is None:
                    return False
                await conn.execute(
                    """
                    UPDATE task_attempt
                       SET status = $2,
                           error = $3,
                           ended_at = now()
                     WHERE task_id = $1 AND status = 'running'
                    """,
                    task_id,
                    # task_attempt 不承载“部分交付”：partial 的执行尝试按失败收尾。
                    {"completed": "completed", "partial": "failed"}.get(status, status),
                    error,
                )
                for artifact in artifacts or []:
                    artifact_id = artifact.get("artifact_id") or f"art_{uuid4().hex}"
                    await conn.execute(
                        """
                        INSERT INTO task_artifact
                          (artifact_id, task_id, name, mime, bytes, storage_ref,
                           expires_at, source_ref, checksum)
                        VALUES ($1, $2, $3, $4, $5, $6,
                                now() + ($7 || ' days')::interval, $8, $9)
                        ON CONFLICT (artifact_id) DO NOTHING
                        """,
                        artifact_id,
                        task_id,
                        artifact.get("name") or "artifact",
                        artifact.get("mime"),
                        artifact.get("bytes"),
                        artifact.get("storage_ref"),
                        str(max(1, int(artifact.get("ttl_days") or 7))),
                        artifact.get("source_ref"),
                        artifact.get("checksum"),
                    )
                    await self._append_event_conn(
                        conn,
                        task_id=task_id,
                        type="artifact",
                        payload={
                            "taskId": task_id,
                            "artifactId": artifact_id,
                            "name": artifact.get("name") or "artifact",
                            "mime": artifact.get("mime"),
                            "bytes": artifact.get("bytes"),
                        },
                    )
                await self._append_event_conn(
                    conn,
                    task_id=task_id,
                    type=status,
                    payload={
                        "taskId": task_id,
                        "status": status,
                        "progress": progress,
                        "summary": summary,
                        "error": error,
                    },
                )
                return True

    async def close_attempt(self, task_id: str, status: str, error: dict[str, Any] | None = None) -> None:
        await self.db.pool.execute(
            """
            UPDATE task_attempt
               SET status = $2, error = $3, ended_at = now()
             WHERE task_id = $1 AND status = 'running'
            """,
            task_id,
            status,
            error,
        )

    async def recover_orphan_running(self, stale_seconds: int = 120) -> int:
        """Fail running tasks whose owner has stopped heartbeating.

        A live worker refreshes ``heartbeat_at`` every poll.  Startup recovery
        therefore only reclaims rows whose lease is stale; a second process
        that is still working on its own tasks is left alone.
        """
        rows = await self.db.pool.fetch(
            """
            SELECT task_id FROM task_run
             WHERE status = 'running'
               AND COALESCE(heartbeat_at, updated_at) < now() - ($1::int * interval '1 second')
            """,
            max(1, int(stale_seconds)),
        )
        count = 0
        for row in rows:
            if await self.finish(
                row["task_id"],
                status="failed",
                progress=0.0,
                summary="进程重启，任务中断",
                error={"code": "WORKER_LOST", "message": "worker heartbeat expired"},
            ):
                count += 1
        return count

    async def expired_artifacts(self, limit: int = 50) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM task_artifact
             WHERE expires_at IS NOT NULL AND expires_at <= now()
             ORDER BY expires_at
             LIMIT $1
            """,
            max(1, min(limit, 200)),
        )
        return [dict(row) for row in rows]

    async def delete_artifact(self, artifact_id: str) -> None:
        await self.db.pool.execute("DELETE FROM task_artifact WHERE artifact_id = $1", artifact_id)
