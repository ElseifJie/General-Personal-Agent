"""MaExecutor: run one task_run in a fresh MA session and project its events.

One in-process worker. A task gets its first attempt plus up to
``TASK_MAX_RETRY`` extra attempts for retryable upstream errors; timeouts and
deterministic errors are not retried. The control plane (task_run/task_event)
is the source of truth; TaskEventInbox owns conversation projection.

Wall-clock time alone never kills a live session: every new MA event extends
the idle budget, so only a long silence (``TASK_IDLE_TIMEOUT_S``) or the hard
cap (``TASK_TIMEOUT_S``) stops a run. When a run is stopped after it already
wrote files, those files are salvaged and the task finishes as ``partial``
instead of losing the deliverables.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import httpx

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
# 连续这么多次事件轮询失败才认为上游真的不可用（2s 轮询约 10s）。
MAX_POLL_FAILURES = 5


class MaTimeout(RuntimeError):
    """本地预算耗尽；``reason`` 区分“卡死”与“总时长护栏”。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class MaSessionFailed(RuntimeError):
    """MA 自己报告会话失败（不是本地超时）。"""


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


def _event_key(event: dict[str, Any]) -> str:
    """Stable identity for activity tracking; MA events normally carry ``id``."""
    event_id = event.get("id")
    if event_id:
        return str(event_id)
    stamp = event.get("processed_at") or event.get("created_at") or ""
    return f"{event.get('type')}@{stamp}"


def _progress_snapshot(events: list[dict[str, Any]]) -> tuple[int, str]:
    steps = 0
    last_tool = ""
    for event in events:
        if event.get("type") == "agent.tool_use":
            steps += 1
            last_tool = str(event.get("name") or last_tool)
    return steps, last_tool


class _PollBudget:
    """Hard wall-clock cap plus an inactivity window that activity extends."""

    def __init__(
        self,
        hard_timeout_s: float,
        idle_timeout_s: float,
        *,
        clock=time.monotonic,
    ):
        self._clock = clock
        now = clock()
        self.hard_timeout_s = max(30.0, float(hard_timeout_s))
        self.idle_timeout_s = max(0.0, float(idle_timeout_s))
        self._hard_deadline = now + self.hard_timeout_s
        self._last_activity = now

    def touch(self) -> None:
        self._last_activity = self._clock()

    def check(self) -> MaTimeout | None:
        now = self._clock()
        if now >= self._hard_deadline:
            return MaTimeout(
                "deadline",
                f"MA session exceeded the hard timeout ({self.hard_timeout_s:.0f}s)",
            )
        if self.idle_timeout_s and now - self._last_activity >= self.idle_timeout_s:
            return MaTimeout(
                "idle",
                f"MA session stalled: no new events for {int(now - self._last_activity)}s",
            )
        return None


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
        self._progress_at = 0.0
        self._progress_steps = -1

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
        requeued = False
        try:
            await self._execute(task, attempt["attempt_id"])
            self.last_error = None
        except asyncio.CancelledError:
            await self.store.close_attempt(task["task_id"], "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            requeued = await self._fail(task, exc)
        finally:
            self.running_task_id = None
            if not requeued:
                # A retry re-mounts the same inputs, so only a finished task
                # releases the uploaded copies.
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
        budget = _PollBudget(
            hard_timeout_s=float(self.settings.task_timeout_s),
            idle_timeout_s=float(getattr(self.settings, "task_idle_timeout_s", 0) or 0),
        )
        self._progress_at = 0.0
        self._progress_steps = -1
        seen: set[str] = set()
        poll_failures = 0
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
                    poll_failures = 0
                except (MAError, MAStatusError) as exc:
                    poll_failures += 1
                    logger.debug(
                        "ma event poll failed task=%s (%s/%s): %s",
                        task_id,
                        poll_failures,
                        MAX_POLL_FAILURES,
                        exc,
                    )
                    if poll_failures >= MAX_POLL_FAILURES:
                        raise
                    events = []
                new = [event for event in events if _event_key(event) not in seen]
                if new:
                    seen.update(_event_key(event) for event in new)
                    budget.touch()
                    await self._emit_progress(task_id, events)
                statuses = {str(event.get("type") or "") for event in events}
                if statuses & {"session.status_failed", "session.error"}:
                    raise MaSessionFailed("MA session reported failure")
                if statuses & {"session.status_idle", "session.status_completed"}:
                    break
                exhausted = budget.check()
                if exhausted is not None:
                    raise exhausted
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
        artifacts = await self._artifacts_with_grace(task_id, session_id, inputs)
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

    async def _emit_progress(self, task_id: str, events: list[dict[str, Any]]) -> None:
        """Project a throttled progress event so the task card stays alive."""
        steps, last_tool = _progress_snapshot(events)
        if steps <= 0 or steps <= self._progress_steps:
            return
        interval = max(0.0, float(getattr(self.settings, "task_progress_interval_s", 0) or 0))
        now = time.monotonic()
        if self._progress_steps >= 0 and now - self._progress_at < interval:
            return
        self._progress_steps = steps
        self._progress_at = now
        summary = f"执行中 · 第 {steps} 步"
        if last_tool:
            summary = f"{summary} · {last_tool}"
        try:
            await self.store.append_event(
                task_id,
                "progress",
                {
                    "taskId": task_id,
                    "status": "running",
                    "progress": min(0.9, 0.05 + 0.05 * steps),
                    "summary": summary,
                },
            )
        except Exception as exc:  # noqa: BLE001 - progress must never fail a task
            logger.debug("progress projection failed task=%s: %s", task_id, exc)

    async def _fail(self, task: dict[str, Any], exc: BaseException) -> bool:
        """Record a failed run, salvaging whatever the session already produced.

        Returns ``True`` when the task was requeued for another attempt.
        """
        task_id = task["task_id"]
        code, message, retryable = self._classify_failure(exc)
        self.last_error = message[:300]
        logger.warning("ma task %s stopped (%s): %s", task_id, code, message)
        error = {"code": code, "message": message}
        current = await self.store.get(task_id) or {}
        session_id = str(current.get("executor_ref") or "")
        inputs = await self._task_inputs(task_id)
        artifacts: list[dict[str, Any]] = []
        if session_id:
            artifacts = await self._artifacts_with_grace(task_id, session_id, inputs)
        if artifacts:
            await self._finish_partial(task, artifacts, error)
            return False
        if retryable:
            attempts = await self.store.attempt_count(task_id)
            max_retry = int(getattr(self.settings, "task_max_retry", 0) or 0)
            if attempts <= max_retry:
                delay = self._retry_delay(attempts)
                requeued = await self.store.requeue(
                    task_id,
                    owner_id=self.owner_id,
                    error=error,
                    summary=f"第 {attempts + 1} 次尝试排队中",
                    delay_s=delay,
                )
                if requeued:
                    logger.info(
                        "ma task %s requeued after %s (attempt %s/%s, retry in %.0fs)",
                        task_id,
                        code,
                        attempts,
                        max_retry,
                        delay,
                    )
                    return True
        await self.store.finish(
            task_id,
            status="failed",
            progress=0.0,
            summary=self._failure_summary(code),
            error=error,
            owner_id=self.owner_id,
        )
        return False

    async def _finish_partial(
        self,
        task: dict[str, Any],
        artifacts: list[dict[str, Any]],
        error: dict[str, Any],
    ) -> None:
        task_id = task["task_id"]
        summary = (
            f"任务中断（{self._failure_reason(error['code'])}），"
            f"已回收 {len(artifacts)} 个已完成的产物；完整总结未生成。"
        )
        logger.info(
            "ma task %s salvaged %s artifacts after %s",
            task_id,
            len(artifacts),
            error["code"],
        )
        await self.store.finish(
            task_id,
            status="partial",
            progress=0.9,
            summary=summary,
            error=error,
            artifacts=artifacts,
            owner_id=self.owner_id,
        )

    def _retry_delay(self, attempts: int) -> float:
        """Exponential backoff for the next attempt; 0 disables it."""
        base = max(0.0, float(getattr(self.settings, "task_retry_backoff_s", 0) or 0))
        if base <= 0:
            return 0.0
        cap = max(base, float(getattr(self.settings, "task_retry_backoff_max_s", base) or base))
        return min(cap, base * (2 ** max(0, attempts - 1)))

    def _classify_failure(self, exc: BaseException) -> tuple[str, str, bool]:
        """Map an exception to ``(code, message, retryable)``.

        Only genuinely transient upstream failures are retried; a timeout has
        already consumed its budget and a deterministic error would repeat.
        """
        if isinstance(exc, MaTimeout):
            return "TASK_TIMEOUT", str(exc), False
        if isinstance(exc, MaSessionFailed):
            return "MA_SESSION_FAILED", "MA 会话报告失败", True
        if isinstance(exc, MAStatusError):
            retryable = exc.status_code == 429 or exc.status_code >= 500
            return "UPSTREAM_UNAVAILABLE", f"MA HTTP {exc.status_code}", retryable
        if isinstance(exc, (MAError, httpx.HTTPError)):
            return "UPSTREAM_UNAVAILABLE", str(exc)[:300] or "MA 请求失败", True
        if isinstance(exc, TimeoutError):
            return "TASK_TIMEOUT", str(exc) or "任务超时", False
        return "TASK_EXECUTION_ERROR", str(exc)[:300] or "任务执行失败", False

    @staticmethod
    def _failure_summary(code: str) -> str:
        if code == "TASK_TIMEOUT":
            return "任务超时，执行失败"
        if code == "MA_SESSION_FAILED":
            return "MA 会话执行失败"
        if code == "UPSTREAM_UNAVAILABLE":
            return "上游服务不可用，任务执行失败"
        return "任务执行失败"

    @staticmethod
    def _failure_reason(code: str) -> str:
        return {
            "TASK_TIMEOUT": "超时",
            "MA_SESSION_FAILED": "会话失败",
            "UPSTREAM_UNAVAILABLE": "上游不可用",
        }.get(code, "执行异常")

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

    async def _artifacts_with_grace(
        self,
        task_id: str,
        session_id: str,
        inputs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """List outputs, waiting out MA's post-idle registration delay.

        A session that reports idle can still register its files a few seconds
        later; without the grace window a fast finish (or a timeout right after
        the last write) would report zero artifacts.
        """
        artifacts = await self._artifacts(task_id, session_id, inputs)
        grace = max(0, int(getattr(self.settings, "task_artifact_grace_s", 0) or 0))
        deadline = time.monotonic() + grace
        while not artifacts and time.monotonic() < deadline:
            await asyncio.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
            artifacts = await self._artifacts(task_id, session_id, inputs)
        return artifacts

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
