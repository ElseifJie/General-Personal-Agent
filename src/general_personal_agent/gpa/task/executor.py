"""MaExecutor: run one task_run in a fresh MA session and project its events.

MVP is deliberately small: one in-process worker, one attempt per task, no
automatic retry. The control plane (task_run/task_event) is the source of truth;
TaskEventInbox owns conversation projection.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from general_personal_agent.clients.ma import MAError, MAStatusError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.task.store import TaskStore

logger = get_logger(__name__)

ARTIFACT_MIME_PREFIXES = ("image/", "video/")
ARTIFACT_SUFFIXES = (
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".mp4",
    ".docx",
    ".md",
    ".xlsx",
    ".csv",
)


def is_artifact(item: dict[str, Any]) -> bool:
    mime = str(item.get("mime_type") or "")
    name = str(item.get("filename") or "").lower()
    if name.endswith("_meta.json"):
        return False
    # MA stages downloaded/uploaded working files under ``outputs/.staging``.
    # They are inputs (reference photos, fetched pages), not deliverables.
    if "/.staging/" in str(item.get("description") or ""):
        return False
    return mime.startswith(ARTIFACT_MIME_PREFIXES) or name.endswith(ARTIFACT_SUFFIXES)


def _event_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    text = event.get("text")
    return text if isinstance(text, str) else ""


class MaExecutor:
    def __init__(self, settings, store: TaskStore, ma, tos=None, files=None):
        self.settings = settings
        self.store = store
        self.ma = ma
        self.tos = tos
        self.files = files
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._resolved_agent_version: int | None = None
        self.running_task_id: str | None = None
        self.last_error: str | None = None
        self.owner_id = f"ma_{uuid4().hex[:12]}"

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="gpa-ma-executor")

    async def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:  # noqa: BLE001
                pass
            self._task = None

    @property
    def enabled(self) -> bool:
        return bool(self.ma is not None and self.settings.ma_agent_id and self.settings.ma_environment_id)

    async def _run(self) -> None:
        interval = max(0.5, float(self.settings.ma_poll_interval_s))
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - worker must not die
                logger.exception("ma executor sweep failed")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        claimed = await self.store.claim_next_ma(self.owner_id)
        if claimed is None:
            return False
        task = claimed["task"]
        attempt = claimed["attempt"]
        self.running_task_id = task["task_id"]
        try:
            await self._execute(task, attempt["attempt_id"])
            self.last_error = None
        except asyncio.CancelledError:
            await self.store.close_attempt(task["task_id"], "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:300]
            logger.warning("ma task %s failed: %s", task["task_id"], exc)
            await self.store.finish(
                task["task_id"],
                status="failed",
                progress=0.0,
                summary="任务执行失败",
                error={"code": "UPSTREAM_UNAVAILABLE", "message": self.last_error},
                owner_id=self.owner_id,
            )
        finally:
            self.running_task_id = None
            try:
                await self._cleanup_inputs(await self._task_inputs(task["task_id"]))
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask task result
                logger.debug("task input cleanup failed task=%s: %s", task["task_id"], exc)
        return True

    async def _agent_version(self) -> int:
        """Resolve which agent build to run; 0 in settings means "current".

        Older builds silently lack tools added later (image/video generation
        arrived in v7 of the configured agent), and the old client hard-coded
        ``version=1``. The executor resolves an explicit, current version.
        """
        configured = int(getattr(self.settings, "ma_agent_version", 0) or 0)
        if configured > 0:
            return configured
        try:
            agent = await self.ma.get_agent(self.settings.ma_agent_id)
        except Exception as exc:  # noqa: BLE001 - transient control-plane error
            if self._resolved_agent_version is not None:
                logger.warning(
                    "ma agent version lookup failed, reusing v%s: %s",
                    self._resolved_agent_version,
                    exc,
                )
                return self._resolved_agent_version
            raise
        version = int(agent.get("version") or 0)
        if version <= 0:
            raise RuntimeError(f"MA agent {self.settings.ma_agent_id} has no usable version")
        self._resolved_agent_version = version
        return version

    async def _execute(self, task: dict[str, Any], attempt_id: str) -> None:
        task_id = task["task_id"]
        agent_version = await self._agent_version()
        logger.info(
            "ma task %s runs agent %s v%s",
            task_id,
            self.settings.ma_agent_id,
            agent_version,
        )
        inputs = await self._prepare_inputs(task_id, attempt_id)
        resources = [
            {"file_id": item["ma_file_id"], "mount_path": item["mount_path"]}
            for item in inputs if item.get("ma_file_id")
        ]
        session_kwargs = {"agent_version": agent_version}
        if resources:
            session_kwargs["resources"] = resources
        session_id = await self.ma.create_session(
            self.settings.ma_agent_id,
            self.settings.ma_environment_id,
            **session_kwargs,
        )
        await self.store.attach_attempt(task_id, attempt_id, session_id)
        await self.ma.send_message(session_id, self._prompt(task, inputs))
        deadline = time.monotonic() + max(30, int(self.settings.task_timeout_s))
        events: list[dict[str, Any]] = []
        try:
            while True:
                current = await self.store.get(task_id)
                if current is None:
                    return
                if current["status"] == "cancelled":
                    try:
                        await self.ma.submit_event(session_id, {"type": "user.interrupt"})
                    except Exception:  # noqa: BLE001
                        pass
                    await self.store.close_attempt(task_id, "cancelled")
                    await self._cleanup_inputs(await self._task_inputs(task_id))
                    return
                if not await self.store.heartbeat(task_id, self.owner_id):
                    # Another worker recovered/reclaimed this task.
                    logger.warning("ma task %s lost its lease; stopping", task_id)
                    return
                try:
                    events = await self.ma.fetch_events_all(session_id)
                except (MAError, MAStatusError) as exc:
                    logger.debug("ma event poll failed task=%s: %s", task_id, exc)
                    events = []
                statuses = {str(event.get("type") or "") for event in events}
                if statuses & {"session.status_failed", "session.error"}:
                    raise RuntimeError("MA session reported failure")
                if statuses & {"session.status_idle", "session.status_completed"}:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("MA session timed out")
                await asyncio.sleep(max(0.5, float(self.settings.ma_poll_interval_s)))
        except BaseException:
            # A local timeout/error must not leave remote work running (and
            # billing) after GPA has given up on the task.
            try:
                await self.ma.submit_event(session_id, {"type": "user.interrupt"})
            except Exception:  # noqa: BLE001
                pass
            raise

        summary = ""
        for event in events:
            if event.get("type") == "agent.message":
                text = _event_text(event).strip()
                if text:
                    summary = text
        if not summary:
            summary = "任务完成"
        current = await self.store.get(task_id)
        if current is not None and current["status"] == "cancelled":
            await self.store.close_attempt(task_id, "cancelled")
            return
        artifacts = await self._artifacts(task_id, session_id, inputs)
        completed = await self.store.finish(
            task_id,
            status="completed",
            progress=1.0,
            summary=summary[:2_000],
            artifacts=artifacts,
            owner_id=self.owner_id,
        )
        if completed:
            await self._cleanup_inputs(inputs)

    async def _task_inputs(self, task_id: str) -> list[dict[str, Any]]:
        if self.files is None:
            return []
        return await self.files.store.list_task_inputs(task_id)

    async def _prepare_inputs(self, task_id: str, attempt_id: str) -> list[dict[str, Any]]:
        inputs = await self._task_inputs(task_id)
        if not inputs:
            return []
        for item in inputs:
            if item.get("ma_file_id") and item.get("ma_file_status") in {"uploaded", "ready"}:
                continue
            file = await self.files.store.get_file(item["file_id"])
            if file is None or not file.get("storage_ref") or self.tos is None:
                raise RuntimeError(f"attachment unavailable: {item['file_id']}")
            data, content_type = await self.tos.get_bytes(file["storage_ref"])
            uploaded = await self.ma.upload_file(
                file["name"], data, content_type=file.get("mime") or content_type or "application/octet-stream"
            )
            ma_file_id = uploaded.get("id") or uploaded.get("file_id")
            await self.files.store.set_task_input_ma(
                item["task_input_id"], ma_file_id=ma_file_id,
                status="ready", attempt_id=attempt_id,
            )
            item["ma_file_id"] = ma_file_id
            item["ma_file_status"] = "ready"
        return inputs

    async def _cleanup_inputs(self, inputs: list[dict[str, Any]]) -> None:
        for item in inputs:
            ma_id = item.get("ma_file_id")
            if not ma_id:
                continue
            try:
                await self.ma.delete_file(ma_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("ma input cleanup failed %s: %s", ma_id, exc)
        if self.files is not None:
            await self.files.store.mark_task_inputs_deleted(inputs[0]["task_id"]) if inputs else None

    async def _artifacts(self, task_id: str, session_id: str, inputs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        try:
            files = await self.ma.list_files_all(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ma list files failed task=%s: %s", task_id, exc)
            return []
        artifacts: list[dict[str, Any]] = []
        input_ids = {str(item.get("ma_file_id")) for item in (inputs or []) if item.get("ma_file_id")}
        for item in files:
            if str(item.get("id") or item.get("file_id") or "") in input_ids:
                continue
            if not is_artifact(item):
                continue
            tos_info = item.get("tos") or {}
            source_ref = tos_info.get("object_key") or item.get("tos_key") or ""
            source_bucket = (
                tos_info.get("bucket")
                or item.get("bucket")
                or getattr(self.settings, "ma_tos_bucket", "")
                or None
            )
            name = item.get("filename") or "artifact"
            target_ref = None
            if self.tos is not None and source_ref:
                safe_name = name.replace("/", "_").replace("\\", "_")[:120]
                target_ref = self.tos.object_key("tasks", task_id, f"{uuid4().hex[:8]}_{safe_name}")
                try:
                    await self.tos.copy_object(source_ref, target_ref, source_bucket)
                except Exception as exc:  # noqa: BLE001 - keep MA ref as fallback
                    logger.warning("artifact copy failed task=%s key=%s: %s", task_id, source_ref, exc)
                    target_ref = None
            artifacts.append(
                {
                    "name": name,
                    "mime": item.get("mime_type"),
                    "bytes": item.get("bytes"),
                    "storage_ref": target_ref or source_ref or None,
                    "source_ref": source_ref or None,
                    "ttl_days": int(self.settings.task_artifact_ttl_days),
                }
            )
        return artifacts

    @staticmethod
    def _prompt(task: dict[str, Any], inputs: list[dict[str, Any]] | None = None) -> str:
        brief = task.get("brief") or {}
        facts = brief.get("facts") or []
        constraints = brief.get("constraints") or []
        sources = brief.get("sources") or []
        lines = [
            "你是 General Personal Agent 的后台任务执行器。",
            f"任务目标：{task['goal']}",
            f"任务类型：{task['task_type']}",
        ]
        if facts:
            lines.append("已确认事实：")
            lines.extend(f"- {fact}" for fact in facts)
        if constraints:
            lines.append("约束：")
            lines.extend(f"- {item}" for item in constraints)
        if sources:
            lines.append("来源：")
            lines.extend(f"- {item}" for item in sources)
        if inputs:
            lines.append("输入文件（已挂载，可直接读取）：")
            lines.extend(f"- {item['mount_path']}" for item in inputs if item.get("ma_file_id"))
            lines.append("不要把输入文件当作产物返回。")
        lines.append("请在当前工作区完成目标，产出可下载的文件，并用一段话总结结果；不要编造未生成的文件。")
        return "\n".join(lines)
