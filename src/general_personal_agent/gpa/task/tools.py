"""Task Gateway tools exposed to the Personal Agent.

These are the in-process MCP-contract tools: the same names/payloads that an
external MCP server would expose. They never touch MA directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.task.gateway import TaskGateway

logger = get_logger(__name__)


@dataclass
class TaskToolState:
    tenant_id: str
    app_user_id: str
    conversation_id: str
    turn_id: str = ""
    attachment_ids: list[str] = field(default_factory=list)


def _lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _idempotency_key(state: TaskToolState, goal: str) -> str:
    raw = f"{state.conversation_id}:{state.turn_id}:{goal}".encode()
    return f"turn_{hashlib.sha1(raw).hexdigest()}"


def build_task_tools(state: TaskToolState, gateway: TaskGateway) -> list:
    async def submit_task(
        goal: str,
        task_type: str = "general",
        facts: str = "",
        constraints: str = "",
        idempotency_key: str = "",
        attachment_ids: str = "",
    ) -> dict[str, Any]:
        """提交一个有界后台任务，返回 task_id 与初始状态。

        只在用户明确需要执行任务时调用；不要在任务创建前声称已完成。
        goal 必须忠实保留用户目标；facts 为用户确认过的事实，一行一条；
        constraints 为用户约束，一行一条；idempotency_key 可留空。
        """
        try:
            result = await gateway.submit(
                tenant_id=state.tenant_id,
                app_user_id=state.app_user_id,
                conversation_id=state.conversation_id,
                goal=goal,
                task_type=task_type or "general",
                brief={
                    "facts": _lines(facts),
                    "constraints": _lines(constraints),
                    "sources": [],
                },
                origin_turn_id=state.turn_id or None,
                idempotency_key=idempotency_key or _idempotency_key(state, goal),
                attachment_ids=(
                    [item.strip() for item in attachment_ids.split(",") if item.strip()]
                    if attachment_ids
                    else list(state.attachment_ids)
                ),
            )
        except GpaError as exc:
            return {"error": exc.code, "message": exc.message}
        return {
            "task_id": result["task_id"],
            "status": result["status"],
            "eta": "约 1-2 分钟",
            "instruction": "任务已创建；用一句话告诉用户已开始，不要编造产物。",
        }

    async def get_task(task_id: str) -> dict[str, Any]:
        """查询任务状态、进度与摘要。"""
        try:
            task = await gateway.get(task_id, state.tenant_id, state.app_user_id)
        except GpaError as exc:
            return {"error": exc.code, "message": exc.message}
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "progress": task["progress"],
            "summary": task["summary"],
            "error": task["error"],
            "artifacts": len(task["artifacts"]),
        }

    async def cancel_task(task_id: str, reason: str = "") -> dict[str, Any]:
        """取消一个尚未结束的任务；不可逆副作用由调用方说明。"""
        try:
            task = await gateway.cancel(task_id, state.tenant_id, state.app_user_id, reason)
        except GpaError as exc:
            return {"error": exc.code, "message": exc.message}
        return {"task_id": task["task_id"], "status": task["status"]}

    async def list_tasks(
        status: str = "",
        scope: str = "all",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """列出任务。scope=all 默认跨会话查最近任务；scope=conversation 只看当前会话。"""
        conversation_id = state.conversation_id if (scope or "").strip().lower() == "conversation" else None
        tasks = await gateway.list(
            tenant_id=state.tenant_id,
            app_user_id=state.app_user_id,
            conversation_id=conversation_id,
            status=status or None,
            limit=limit,
        )
        result: list[dict[str, Any]] = []
        for item in tasks:
            created = item.get("created_at")
            result.append(
                {
                    "task_id": item["task_id"],
                    "conversation_id": item["conversation_id"],
                    "status": item["status"],
                    "goal": item["goal"],
                    "task_type": item["task_type"],
                    "summary": item["summary"],
                    "artifacts": len(item["artifacts"]),
                    "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
                }
            )
        return result

    async def list_artifacts(task_id: str) -> list[dict[str, Any]]:
        """列出任务的产物元数据与下载 URL。"""
        try:
            task = await gateway.get(task_id, state.tenant_id, state.app_user_id)
        except GpaError as exc:
            return [{"error": exc.code, "message": exc.message}]
        return task["artifacts"]

    async def read_artifact(artifact_id: str) -> dict[str, Any]:
        """读取一个产物的元数据与受鉴权保护的下载 URL（不返回 TOS key）。"""
        artifact = await gateway.store.get_artifact(artifact_id)
        if (
            artifact is None
            or artifact["tenant_id"] != state.tenant_id
            or artifact["app_user_id"] != state.app_user_id
        ):
            return {"error": "NOT_FOUND", "message": "artifact not found"}
        task_id = artifact["task_id"]
        return {
            "artifact_id": artifact_id,
            "task_id": task_id,
            "name": artifact["name"],
            "mime": artifact.get("mime"),
            "bytes": artifact.get("bytes"),
            "download_url": f"/api/tasks/{task_id}/artifacts/{artifact_id}/download",
        }

    return [
        submit_task,
        get_task,
        cancel_task,
        list_tasks,
        list_artifacts,
        read_artifact,
    ]
