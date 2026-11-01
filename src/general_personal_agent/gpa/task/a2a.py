"""Outbound A2A executor: delegate a task to a remote A2A agent."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any
from uuid import uuid4

import httpx

from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.task.store import TaskStore

logger = get_logger(__name__)

TERMINAL = {"completed", "failed", "canceled", "cancelled", "rejected"}


def _status(task: dict[str, Any]) -> str:
    value = task.get("status")
    if isinstance(value, dict):
        return str(value.get("state") or "unknown")
    return str(value or "unknown")


def _summary(task: dict[str, Any]) -> str:
    status = task.get("status")
    if isinstance(status, dict):
        message = status.get("message") or {}
        if isinstance(message, dict):
            parts = message.get("parts") or []
            texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
            if any(texts):
                return "".join(texts)[:2000]
    artifacts = task.get("artifacts") or []
    for artifact in artifacts:
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"])[:2000]
    return f"A2A {_status(task)}"


class A2aExecutor:
    def __init__(self, settings, store: TaskStore, tos=None, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.store = store
        self.tos = tos
        self._http = http
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.running_task_id: str | None = None
        self.last_error: str | None = None
        self.owner_id = f"a2a_{uuid4().hex[:12]}"

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "a2a_enabled", False))

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="gpa-a2a-executor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        interval = max(1.0, float(getattr(self.settings, "a2a_poll_interval_s", 2.0)))
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("a2a executor sweep failed")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        claimed = await self.store.claim_next("a2a", owner_id=self.owner_id)
        if claimed is None:
            return False
        task = claimed["task"]
        self.running_task_id = task["task_id"]
        try:
            await self._execute(task, claimed["attempt"]["attempt_id"])
            self.last_error = None
        except asyncio.CancelledError:
            await self.store.close_attempt(task["task_id"], "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:300]
            logger.warning("a2a task %s failed: %s", task["task_id"], exc)
            await self.store.finish(
                task["task_id"],
                status="failed",
                progress=0.0,
                summary="A2A 任务失败",
                error={"code": "UPSTREAM_UNAVAILABLE", "message": self.last_error},
                owner_id=self.owner_id,
            )
        finally:
            self.running_task_id = None
        return True

    def _prompt(self, task: dict[str, Any]) -> str:
        brief = task.get("brief") or {}
        lines = [f"任务目标：{task['goal']}", f"任务类型：{task['task_type']}"]
        for fact in brief.get("facts") or []:
            lines.append(f"- 事实：{fact}")
        for constraint in brief.get("constraints") or []:
            lines.append(f"- 约束：{constraint}")
        return "\n".join(lines)

    async def _execute(self, task: dict[str, Any], attempt_id: str) -> None:
        brief = task.get("brief") or {}
        url = brief.get("a2a_url") or getattr(self.settings, "a2a_agent_url", "")
        token = brief.get("a2a_token") or getattr(self.settings, "a2a_token", "")
        if not url:
            raise RuntimeError("A2A agent URL is not configured")
        headers = {"content-type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = float(getattr(self.settings, "a2a_timeout_s", 300))
        deadline = time.monotonic() + timeout
        client = self._http or httpx.AsyncClient(timeout=30)
        try:
            sent = await self._rpc(
                client,
                url,
                headers,
                "tasks/send",
                {
                    "id": task["task_id"],
                    "message": {"role": "user", "parts": [{"type": "text", "text": self._prompt(task)}]},
                },
            )
            remote_id = str((sent.get("result") or sent).get("id") or task["task_id"])
            await self.store.attach_attempt(task["task_id"], attempt_id, f"a2a:{remote_id}")
            while True:
                current = await self.store.get(task["task_id"])
                if current is None:
                    return
                if current["status"] == "cancelled":
                    try:
                        await self._rpc(client, url, headers, "tasks/cancel", {"id": remote_id})
                    except Exception:  # noqa: BLE001
                        pass
                    await self.store.close_attempt(task["task_id"], "cancelled")
                    return
                if not await self.store.heartbeat(task["task_id"], self.owner_id):
                    return
                result = await self._rpc(client, url, headers, "tasks/get", {"id": remote_id})
                remote = result.get("result") or result
                state = _status(remote)
                if state in TERMINAL:
                    if state in {"failed", "rejected"}:
                        raise RuntimeError(_summary(remote))
                    if state in {"canceled", "cancelled"}:
                        await self.store.finish(
                            task["task_id"],
                            status="cancelled",
                            progress=0.0,
                            summary="A2A 远端取消",
                            owner_id=self.owner_id,
                        )
                        await self.store.close_attempt(task["task_id"], "cancelled")
                        return
                    artifacts = await self._artifacts(task["task_id"], remote.get("artifacts") or [])
                    await self.store.finish(
                        task["task_id"],
                        status="completed",
                        progress=1.0,
                        summary=_summary(remote),
                        artifacts=artifacts,
                        owner_id=self.owner_id,
                    )
                    return
                await asyncio.sleep(max(0.5, float(getattr(self.settings, "a2a_poll_interval_s", 2.0))))
                if time.monotonic() >= deadline:
                    raise TimeoutError("A2A task timed out")
        finally:
            if self._http is None:
                await client.aclose()

    async def _rpc(self, client, url: str, headers: dict, method: str, params: dict) -> dict:
        response = await client.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload["error"])[:300])
        return payload

    async def _artifacts(self, task_id: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for artifact in artifacts:
            for part in artifact.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                file_part = part.get("file") if part.get("type") == "file" else None
                if not file_part:
                    continue
                name = file_part.get("name") or artifact.get("name") or "artifact"
                mime = file_part.get("mimeType") or file_part.get("mime_type") or "application/octet-stream"
                data = b""
                source_ref = str(file_part.get("uri") or "")
                if file_part.get("bytes"):
                    data = base64.b64decode(file_part["bytes"])
                elif source_ref.startswith(("http://", "https://")):
                    async with httpx.AsyncClient(timeout=60) as http:
                        response = await http.get(source_ref)
                        response.raise_for_status()
                        data = response.content
                storage_ref = None
                if data and self.tos is not None:
                    safe = name.replace("/", "_")[:100]
                    target = self.tos.object_key("tasks", task_id, f"{uuid4().hex[:8]}_{safe}")
                    await self.tos.put_bytes(target, data, content_type=mime)
                    storage_ref = target
                output.append(
                    {
                        "name": name,
                        "mime": mime,
                        "bytes": len(data) or None,
                        "storage_ref": storage_ref,
                        "source_ref": source_ref or None,
                        "ttl_days": int(getattr(self.settings, "task_artifact_ttl_days", 7)),
                    }
                )
        return output
