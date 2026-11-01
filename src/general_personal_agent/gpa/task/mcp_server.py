"""MCP HTTP server exposing the Task Gateway tools.

Run as a separate process (``scripts/task_mcp_server.py``). Callers authenticate
with a short-lived service JWT and pass tenant/user/conversation headers.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from general_personal_agent.gpa.service_token import verify_service_token_async


async def _resolve_gpa(source):
    return await source() if callable(source) else source


def build_task_mcp(gpa_source, settings) -> FastMCP:
    mcp = FastMCP(
        "gpa-task-gateway",
        instructions="General Personal Agent task gateway (MCP over Streamable HTTP).",
    )

    async def _context(scope: str, app_user_id: str = "", conversation_id: str = "") -> tuple[str, str, str]:
        headers = get_http_headers() or {}
        token = headers.get("x-gpa-service-token", "")
        if not token:
            auth = headers.get("authorization", "")
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
        gpa = await _resolve_gpa(gpa_source)
        claims = await verify_service_token_async(settings, token, gpa.task_store.db)
        if claims is None:
            raise ToolError("invalid or expired service token")
        if scope not in claims.scopes and "*" not in claims.scopes:
            raise ToolError(f"missing scope {scope}")
        tenant = headers.get("x-gpa-tenant-id") or claims.tenant_id
        user = app_user_id or headers.get("x-gpa-app-user-id", "")
        conv = conversation_id or headers.get("x-gpa-conversation-id", "")
        if not user:
            raise ToolError("appUserId is required")
        if claims.allowed_users is not None and user not in claims.allowed_users:
            raise ToolError("service token is not allowed to act for this user")
        return tenant, user, conv

    @mcp.tool
    async def submit_task(
        goal: str,
        task_type: str = "general",
        facts: str = "",
        constraints: str = "",
        idempotency_key: str = "",
        executor: str = "ma",
        app_user_id: str = "",
        conversation_id: str = "",
    ) -> dict[str, Any]:
        """Submit a bounded background task."""
        tenant, user, conv = await _context("task:submit", app_user_id, conversation_id)
        if not conv:
            raise ToolError("conversationId is required")
        gpa = await _resolve_gpa(gpa_source)
        task = await gpa.tasks.submit(
            tenant_id=tenant,
            app_user_id=user,
            conversation_id=conv,
            goal=goal,
            task_type=task_type or "general",
            brief={
                "facts": [line.strip() for line in facts.splitlines() if line.strip()],
                "constraints": [line.strip() for line in constraints.splitlines() if line.strip()],
                "sources": [],
            },
            idempotency_key=idempotency_key or None,
            executor=executor,
        )
        return {
            "taskId": task["task_id"],
            "status": task["status"],
            "goal": task["goal"],
        }

    @mcp.tool
    async def get_task(task_id: str, app_user_id: str = "", tenant_id: str = "") -> dict[str, Any]:
        """Get task status, progress, summary and artifact count."""
        tenant, user, _ = await _context("task:read", app_user_id)
        if tenant_id:
            tenant = tenant_id
        gpa = await _resolve_gpa(gpa_source)
        task = await gpa.tasks.get(task_id, tenant, user)
        return {
            "taskId": task["task_id"],
            "status": task["status"],
            "progress": task["progress"],
            "summary": task["summary"],
            "error": task["error"],
            "artifactCount": len(task["artifacts"]),
        }

    @mcp.tool
    async def cancel_task(task_id: str, reason: str = "", app_user_id: str = "") -> dict[str, Any]:
        """Cancel a queued/running task."""
        tenant, user, _ = await _context("task:cancel", app_user_id)
        gpa = await _resolve_gpa(gpa_source)
        task = await gpa.tasks.cancel(task_id, tenant, user, reason)
        return {"taskId": task["task_id"], "status": task["status"]}

    @mcp.tool
    async def list_tasks(status: str = "", app_user_id: str = "", conversation_id: str = "") -> list[dict]:
        """List tasks for the current conversation or user."""
        tenant, user, conv = await _context("task:read", app_user_id, conversation_id)
        gpa = await _resolve_gpa(gpa_source)
        tasks = await gpa.tasks.list(
            tenant_id=tenant,
            app_user_id=user,
            conversation_id=conv or None,
            status=status or None,
        )
        return [
            {
                "taskId": task["task_id"],
                "status": task["status"],
                "goal": task["goal"],
                "taskType": task["task_type"],
                "summary": task["summary"],
                "artifactCount": len(task["artifacts"]),
            }
            for task in tasks
        ]

    @mcp.tool
    async def list_artifacts(task_id: str, app_user_id: str = "") -> list[dict]:
        """List artifact metadata for a task."""
        tenant, user, _ = await _context("task:read", app_user_id)
        gpa = await _resolve_gpa(gpa_source)
        task = await gpa.tasks.get(task_id, tenant, user)
        return task["artifacts"]

    @mcp.tool
    async def read_artifact(artifact_id: str, app_user_id: str = "") -> dict[str, Any]:
        """Read artifact metadata and authenticated download URL."""
        tenant, user, _ = await _context("task:read", app_user_id)
        gpa = await _resolve_gpa(gpa_source)
        artifact = await gpa.tasks.store.get_artifact(artifact_id)
        if artifact is None or artifact["tenant_id"] != tenant or artifact["app_user_id"] != user:
            raise ToolError("artifact not found")
        return {
            "artifactId": artifact_id,
            "taskId": artifact["task_id"],
            "name": artifact["name"],
            "mime": artifact.get("mime"),
            "bytes": artifact.get("bytes"),
            "expiresAt": artifact.get("expires_at"),
            "downloadUrl": (f"/api/tasks/{artifact['task_id']}/artifacts/{artifact_id}/download"),
        }

    return mcp
