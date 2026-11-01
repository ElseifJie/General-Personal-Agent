"""GPA core service container, built once per process."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from general_personal_agent.gpa.agent.runtime import build_runtime_factory
from general_personal_agent.gpa.agent.transport import InProcessTransport
from general_personal_agent.gpa.context.assembler import ContextAssembler
from general_personal_agent.gpa.context.retriever import GpaContextRetriever
from general_personal_agent.gpa.context.writer import GpaContextWriter
from general_personal_agent.gpa.conversation.bus import ConversationBus
from general_personal_agent.gpa.conversation.service import ConversationService
from general_personal_agent.gpa.conversation.store import ConversationStore
from general_personal_agent.gpa.files.jobs import ExtractHandler, FileJobQueue, FileJobWorker
from general_personal_agent.gpa.files.kb import KbService
from general_personal_agent.gpa.files.service import FileService
from general_personal_agent.gpa.files.store import FileStore
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.ratelimit import GpaRateLimiter
from general_personal_agent.gpa.task.a2a import A2aExecutor
from general_personal_agent.gpa.task.artifacts import ArtifactJanitor
from general_personal_agent.gpa.task.executor import MaExecutor
from general_personal_agent.gpa.task.gateway import TaskGateway
from general_personal_agent.gpa.task.inbox import TaskEventInbox
from general_personal_agent.gpa.task.store import TaskStore
from general_personal_agent.gpa.voice.registry import VoiceSessionRegistry

logger = get_logger(__name__)


@dataclass
class GpaServices:
    settings: Any
    db: Any
    store: ConversationStore
    bus: ConversationBus
    assembler: ContextAssembler
    transport: InProcessTransport
    conversations: ConversationService
    task_store: TaskStore
    tasks: TaskGateway
    inbox: TaskEventInbox
    ma_executor: MaExecutor | None = None
    artifact_janitor: ArtifactJanitor | None = None
    a2a_executor: A2aExecutor | None = None
    context_retriever: GpaContextRetriever | None = None
    context_writer: GpaContextWriter | None = None
    files: FileService | None = None
    kb: KbService | None = None
    file_jobs: FileJobWorker | None = None
    search_service: Any | None = None
    rate_limiter: GpaRateLimiter = field(default_factory=GpaRateLimiter)
    voice_sessions: VoiceSessionRegistry = field(default_factory=VoiceSessionRegistry)
    agent_mode: str = "stub"
    recovered_turns: int = 0
    recovered_tasks: int = 0
    _started: bool = field(default=False, repr=False)
    _warmup_task: Any = field(default=None, repr=False)
    _draft_purge_task: Any = field(default=None, repr=False)
    _task_reaper_task: Any = field(default=None, repr=False)

    async def start(self) -> None:
        if self._started:
            return
        self.recovered_turns = await self.conversations.recover_stale_turns()
        self.recovered_tasks = await self.task_store.recover_orphan_running(stale_seconds=120)
        if self.ma_executor is not None:
            self.ma_executor.start()
        if self.artifact_janitor is not None:
            self.artifact_janitor.start()
        if self.a2a_executor is not None:
            self.a2a_executor.start()
        if self.file_jobs is not None:
            self.file_jobs.start()
        self.inbox.start()
        await self._purge_drafts()
        self._draft_purge_task = asyncio.create_task(self._draft_purge_loop(), name="gpa-draft-purge")
        self._task_reaper_task = asyncio.create_task(self._task_reaper_loop(), name="gpa-task-reaper")
        if self.agent_mode == "veadk" and self.settings.gpa_warmup_enabled:
            self._warmup_task = asyncio.create_task(self._warmup_agent(), name="gpa-agent-warmup")
        self._started = True
        logger.info(
            "gpa ready (agent_mode=%s, recovered_turns=%s)",
            self.agent_mode,
            self.recovered_turns,
        )

    async def _purge_drafts(self) -> int:
        try:
            purged = await self.conversations.purge_drafts(int(self.settings.gpa_draft_ttl_days))
        except Exception as exc:  # noqa: BLE001 - cleanup must not block startup
            logger.warning("draft purge failed: %s", exc)
            return 0
        if purged:
            logger.info("purged %s abandoned draft conversations", purged)
        return purged

    async def _draft_purge_loop(self) -> None:
        interval = max(3600, int(getattr(self.settings, "gpa_draft_purge_interval_s", 21600)))
        while True:
            await asyncio.sleep(interval)
            await self._purge_drafts()

    async def _task_reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.task_store.recover_orphan_running(stale_seconds=120)
            except Exception as exc:  # noqa: BLE001 - reaper must not die
                logger.warning("task reaper failed: %s", exc)

    async def close(self) -> None:
        if self._task_reaper_task is not None and not self._task_reaper_task.done():
            self._task_reaper_task.cancel()
            try:
                await self._task_reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._task_reaper_task = None
        if self._draft_purge_task is not None and not self._draft_purge_task.done():
            self._draft_purge_task.cancel()
            try:
                await self._draft_purge_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except (asyncio.CancelledError, Exception):
                pass
            self._warmup_task = None
        if self.ma_executor is not None:
            await self.ma_executor.stop()
        if self.artifact_janitor is not None:
            await self.artifact_janitor.stop()
        if self.a2a_executor is not None:
            await self.a2a_executor.stop()
        if self._started:
            await self.inbox.stop()
        if self.file_jobs is not None:
            await self.file_jobs.stop()
        if self.kb is not None:
            await self.kb.close()
        if self.context_writer is not None:
            await self.context_writer.close()
        if self.context_retriever is not None:
            await self.context_retriever.close()
        await self.rate_limiter.close()
        await self.transport.close_all()
        self._started = False

    async def _warmup_agent(self) -> None:
        """Warm LiteLLM/ARK once per process so the first user turn is not cold."""
        from general_personal_agent.gpa.agent.runtime import VeADKRuntime
        from general_personal_agent.gpa.agent.transport import AgentSessionRequest, AgentTurnRequest

        request = AgentSessionRequest(
            conversation_id="gpa_warmup",
            tenant_id=self.settings.ov_tenant_id or "demo",
            app_user_id="__gpa_warmup__",
            channel="text",
        )
        runtime = VeADKRuntime(self.settings, request, None)
        try:
            await runtime.start()
            async for _event in runtime.stream(
                AgentTurnRequest(
                    turn_id="gpa_warmup",
                    conversation_id="gpa_warmup",
                    user_text="只回复 ok，不要调用任何工具。",
                    channel="text",
                    trace_id="gpa_warmup",
                )
            ):
                pass
            logger.info("gpa agent warmup done")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("gpa agent warmup failed (first turn may be slower): %s", exc)
        finally:
            await runtime.cleanup_session()
            await runtime.close()

    async def health(self) -> dict[str, Any]:
        return {
            "agent": await self.transport.health(),
            "inbox_running": self.inbox._task is not None and not self.inbox._task.done(),
            "recovered_turns": self.recovered_turns,
            "recovered_tasks": self.recovered_tasks,
            "executor": (
                {
                    "enabled": self.ma_executor.enabled,
                    "running_task_id": self.ma_executor.running_task_id,
                    "last_error": self.ma_executor.last_error,
                }
                if self.ma_executor is not None
                else None
            ),
            "memory": {
                "retrieval": self.context_retriever is not None,
                "writeback": self.context_writer is not None,
            },
            "web_search": {
                "enabled": self.search_service is not None and self.agent_mode == "veadk",
                "provider": getattr(self.settings, "search_provider", ""),
                "providers": (self.search_service.provider_list() if self.search_service is not None else []),
                # 最近一次搜索的结果/失败原因与进程实际持有的凭据代次，
                # 用于定位「新进程能搜、老进程搜不到」这类密钥轮换问题。
                "last": self.search_service.state() if self.search_service is not None else None,
                "credentials": (
                    self.search_service.credential_fingerprint() if self.search_service is not None else None
                ),
            },
            "artifact_janitor": (
                {
                    "enabled": self.artifact_janitor.enabled,
                    "last_removed": self.artifact_janitor.last_removed,
                }
                if self.artifact_janitor is not None
                else None
            ),
            "a2a": (
                {"enabled": self.a2a_executor.enabled, "last_error": self.a2a_executor.last_error}
                if self.a2a_executor is not None
                else None
            ),
        }


def _generation(payload: dict) -> int | None:
    value = payload.get("generation")
    return int(value) if value is not None else None


def _job_handler(call):
    async def handler(job):
        payload = job.get("payload") or {}
        payload.setdefault("tenant_id", job.get("tenant_id"))
        return await call(payload)
    return handler


def build_gpa_services(settings, db, ma=None, tos=None, identity_service=None, search=None) -> GpaServices:
    store = ConversationStore(db)
    bus = ConversationBus()
    retriever = GpaContextRetriever(settings, identity_service) if identity_service is not None else None
    assembler = ContextAssembler(settings, store, retriever=retriever)
    task_store = TaskStore(db)
    # The gateway needs conversation ownership for tool calls; bind it after the
    # conversation service is constructed (the Agent tools only hold the gateway).
    tasks = TaskGateway(settings, task_store, None)
    factory = build_runtime_factory(settings, assembler, tasks, retriever, search=search)
    mode = getattr(factory, "mode", "stub")
    transport = InProcessTransport(
        factory,
        mode=mode,
        ttl_s=float(getattr(settings, "gpa_runtime_ttl_s", 1800)),
        max_sessions=int(getattr(settings, "gpa_runtime_max_sessions", 200)),
    )
    writer = (
        GpaContextWriter(settings, db, store, identity_service)
        if identity_service is not None and getattr(settings, "memory_enabled", True)
        else None
    )
    file_store = FileStore(db)
    file_queue = FileJobQueue(file_store)
    file_service = FileService(settings, db, file_store, tos, jobs=file_queue)
    kb_service = KbService(settings, file_store, file_service, tos, identity_service, jobs=file_queue)
    if retriever is not None:
        retriever.guard = kb_service.guard
    conversations = ConversationService(
        settings, store, bus, transport, assembler,
        context_writer=writer, tos=tos, files=file_service, kb=kb_service,
    )
    file_service.conversations = conversations
    tasks.conversations = conversations
    tasks.files = file_service
    tasks.kb = kb_service
    executor = MaExecutor(settings, task_store, ma, tos, files=file_service)
    file_worker = FileJobWorker(settings, file_store, tos)
    file_worker.register("extract", ExtractHandler(file_store, tos, settings=settings))
    file_worker.register(
        "kb_index",
        _job_handler(lambda payload: kb_service.index_document(
            str(payload.get("doc_id") or ""), _generation(payload)
        )),
    )
    file_worker.register(
        "kb_reconcile",
        _job_handler(lambda payload: kb_service.reconcile_document(
            str(payload.get("doc_id") or ""), _generation(payload)
        )),
    )
    async def _kb_delete(payload: dict) -> None:
        doc_id = str(payload.get("doc_id") or "")
        generation = _generation(payload)
        if payload.get("verify"):
            await kb_service.verify_deleted(
                doc_id, int(generation or 0), int(payload.get("attempt") or 0)
            )
            return
        await kb_service.delete_remote(doc_id, generation)

    file_worker.register("kb_delete", _job_handler(_kb_delete))
    file_worker.register("kb_dir_refresh", _job_handler(lambda payload: kb_service.refresh_dir(str(payload.get("tenant_id") or ""), str(payload.get("scope") or "private"), str(payload.get("dir_uri") or ""), str(payload.get("app_user_id") or ""))))
    file_worker.register("file_gc", _job_handler(lambda _payload: file_service.gc_once()))
    janitor = ArtifactJanitor(settings, task_store, tos)
    a2a = A2aExecutor(settings, task_store, tos)
    inbox = TaskEventInbox(
        settings=settings,
        task_store=task_store,
        conversation_store=store,
        bus=bus,
    )
    return GpaServices(
        settings=settings,
        db=db,
        store=store,
        bus=bus,
        assembler=assembler,
        transport=transport,
        conversations=conversations,
        task_store=task_store,
        tasks=tasks,
        inbox=inbox,
        ma_executor=executor,
        artifact_janitor=janitor,
        a2a_executor=a2a,
        context_retriever=retriever,
        context_writer=writer,
        files=file_service,
        kb=kb_service,
        file_jobs=file_worker,
        search_service=search,
        rate_limiter=GpaRateLimiter(getattr(settings, "gpa_redis_url", "")),
        agent_mode=mode,
    )
