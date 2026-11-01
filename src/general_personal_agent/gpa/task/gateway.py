"""Task Gateway control plane (MCP tools will wrap this service in P4).

submit_task only writes the control plane and returns quickly; execution is an
executor concern (MaExecutor in P4). Every event is appended to task_event;
TaskEventInbox projects it into the conversation timeline.
"""

from __future__ import annotations

from typing import Any

from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.store import FileUnavailableError, task_mount_path
from general_personal_agent.gpa.ids import new_request_id, new_task_input_id
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.task.store import TaskStore, payload_fingerprint

logger = get_logger(__name__)


class TaskGateway:
    def __init__(self, settings, store: TaskStore, conversations):
        self.settings = settings
        self.store = store
        self.conversations = conversations
        self.files = None
        self.kb = None

    async def submit(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        conversation_id: str,
        goal: str,
        task_type: str,
        brief: dict[str, Any] | None = None,
        origin_turn_id: str | None = None,
        idempotency_key: str | None = None,
        executor: str = "ma",
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        await self.conversations.require_conversation(conversation_id, tenant_id, app_user_id)
        if not goal.strip():
            raise GpaError("VALIDATION_ERROR", "goal is required")
        if executor not in {"ma", "a2a"}:
            raise GpaError("VALIDATION_ERROR", f"unsupported executor: {executor}")
        if executor == "a2a" and not getattr(self.settings, "a2a_enabled", False):
            raise GpaError("UPSTREAM_UNAVAILABLE", "A2A executor is disabled")
        if executor == "ma" and not (
            getattr(self.settings, "ma_agent_id", "") and getattr(self.settings, "ma_environment_id", "")
        ):
            raise GpaError("UPSTREAM_UNAVAILABLE", "MA executor is not configured")
        attachment_ids = list(dict.fromkeys(attachment_ids or []))
        if executor == "a2a" and attachment_ids:
            raise GpaError("VALIDATION_ERROR", "A2A executor does not support attachments")
        max_files = int(getattr(self.settings, "gpa_file_max_per_message", 10))
        if len(attachment_ids) > max_files:
            raise GpaError("VALIDATION_ERROR", "too many attachments")
        input_files: list[dict] = []
        for file_id in attachment_ids:
            if self.files is None:
                raise GpaError("VALIDATION_ERROR", "attachments are not supported")
            file = await self.files.store.get_file(file_id)
            if (
                file is None
                or file["tenant_id"] != tenant_id
                or file["app_user_id"] != app_user_id
                or file.get("gc_state") in {"deleting", "deleted"}
            ):
                raise GpaError("VALIDATION_ERROR", "attachment not found")
            input_files.append(file)
        key = idempotency_key or new_request_id()
        fingerprint = payload_fingerprint(
            goal=goal,
            task_type=task_type,
            brief=brief,
            executor=executor,
            attachment_ids=[file["file_id"] for file in input_files],
        )
        input_rows = [
            {
                "task_input_id": new_task_input_id(),
                "file_id": file["file_id"],
                "mount_path": task_mount_path(file["file_id"], file.get("name") or ""),
            }
            for file in input_files
        ]
        try:
            task, created = await self.store.create_task(
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                conversation_id=conversation_id,
                goal=goal.strip(),
                task_type=task_type,
                brief=brief,
                origin_turn_id=origin_turn_id,
                idempotency_key=key,
                executor=executor,
                payload_fingerprint=fingerprint,
                input_files=input_rows,
            )
        except FileUnavailableError as exc:
            raise GpaError("CONFLICT", "attachment is being deleted", retryable=True) from exc
        if not created:
            # Owner/conversation are part of the idempotency identity even
            # when the payload fingerprint matches: the same key must never
            # return a task that belongs to another conversation or user.
            mismatch = (
                task.get("tenant_id") != tenant_id
                or task.get("app_user_id") != app_user_id
                or task.get("conversation_id") != conversation_id
            )
            stored_fingerprint = task.get("payload_fingerprint")
            if stored_fingerprint:
                mismatch = mismatch or stored_fingerprint != fingerprint
            else:
                # Rows created before 015 have no fingerprint; keep the legacy
                # comparison for them, including goal/type/executor.
                mismatch = mismatch or (
                    task.get("goal") != goal.strip()
                    or (task.get("task_type") or "") != task_type
                    or (task.get("executor") or "ma") != executor
                )
            if mismatch:
                raise GpaError(
                    "IDEMPOTENCY_MISMATCH",
                    "idempotencyKey reused with different owner or payload",
                    details={"taskId": task.get("task_id", "")},
                )
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="task.submit",
            resource_id=task["task_id"],
            trace_id=origin_turn_id,
        )
        return await self.view(task)

    async def get(self, task_id: str, tenant_id: str, app_user_id: str) -> dict[str, Any]:
        task = await self.store.get_for_owner(task_id, tenant_id, app_user_id)
        if task is None:
            raise GpaError("NOT_FOUND", "task not found")
        return await self.view(task)

    async def cancel(self, task_id: str, tenant_id: str, app_user_id: str, reason: str = "") -> dict:
        task = await self.store.get_for_owner(task_id, tenant_id, app_user_id)
        if task is None:
            raise GpaError("NOT_FOUND", "task not found")
        updated = await self.store.cancel(task_id, reason)
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="task.cancel",
            resource_id=task_id,
        )
        return await self.view(updated or task)

    async def list(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if conversation_id:
            await self.conversations.require_conversation(conversation_id, tenant_id, app_user_id)
        tasks = await self.store.list(
            conversation_id=conversation_id,
            status=status,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            limit=max(1, min(limit, 100)),
        )
        return [await self.view(task) for task in tasks]

    async def view(self, task: dict[str, Any]) -> dict[str, Any]:
        artifacts = await self.store.list_artifacts(task["task_id"])
        return {
            "task_id": task["task_id"],
            "conversation_id": task["conversation_id"],
            "status": task["status"],
            "progress": float(task.get("progress") or 0.0),
            "summary": task.get("summary") or "",
            "executor": task.get("executor") or "ma",
            "goal": task.get("goal") or "",
            "task_type": task.get("task_type") or "",
            "error": task.get("error"),
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "artifacts": [
                {
                    "artifact_id": a["artifact_id"],
                    "task_id": a["task_id"],
                    "name": a["name"],
                    "mime": a.get("mime"),
                    "bytes": a.get("bytes"),
                    "created_at": a["created_at"],
                    "expires_at": a.get("expires_at"),
                    "download_url": (f"/api/tasks/{task['task_id']}/artifacts/{a['artifact_id']}/download"),
                }
                for a in artifacts
            ],
        }
