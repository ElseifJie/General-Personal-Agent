"""ConversationService: conversation lifecycle, turns, timeline and streaming.

The service owns the text turn lifecycle. Agent deltas are transient; every
durable event is written by ConversationStore and fanned out through
ConversationBus for SSE subscribers.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import UTC, datetime
from typing import Any

from general_personal_agent.gpa.agent.transport import (
    AgentSessionRequest,
    AgentTurnRequest,
)
from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.gpa.conversation.bus import ConversationBus
from general_personal_agent.gpa.conversation.store import ConversationStore
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.ids import new_trace_id
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.util import to_iso

logger = get_logger(__name__)

PERSISTED_EVENT_TYPES = {
    "message.final",
    "tool.call",
    "tool.result",
    "task.created",
    "task.status",
    "artifact",
    "error",
}


_QUOTED = re.compile(r"""["'“”‘’][^"'“”‘’]{0,120}["'“”‘’]""")
_NEGATION = re.compile(
    r"(不要|不用|不需要|不必|无需|禁止|切勿|不能|暂不|先不|取消|撤销|"
    r"别再|别把|别保存|别存|别放|别上传|没有要求)"
)
_CONDITIONAL = re.compile(r"(如果|假如|要是|除非|是否).{0,24}(知识库|共享库)")
_QUESTION = re.compile(r"[？?]|(吗|呢)\s*$")


def kb_intent_scope(text: str) -> str | None:
    """Return the explicit save scope, or None when the clause is not a clear request.

    This is deliberately conservative: negation, quoted examples, questions and
    conditionals never trigger a side effect.  The agent's ``promote_to_kb``
    tool remains the authoritative path for ambiguous phrasing.
    """
    if not text:
        return None
    cleaned = _QUOTED.sub(" ", text)
    for clause in re.split(r"[。！？!?；;\n]", cleaned):
        clause = clause.strip()
        if not clause:
            continue
        if _NEGATION.search(clause) or _CONDITIONAL.search(clause) or _QUESTION.search(clause):
            continue
        if not ConversationService._KB_INTENT.search(clause):
            continue
        return "shared" if ("共享" in clause or "团队" in clause) else "private"
    return None


class ConversationService:
    def __init__(
        self,
        settings,
        store: ConversationStore,
        bus: ConversationBus,
        transport,
        assembler,
        context_writer=None,
        tos=None,
        files=None,
        kb=None,
    ):
        self.settings = settings
        self.store = store
        self.bus = bus
        self.transport = transport
        self.assembler = assembler
        self.context_writer = context_writer
        self.tos = tos
        self.files = files
        self.kb = kb
        self._running: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ lifecycle
    async def create(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        title: str,
        channel: str,
        draft: bool = False,
    ) -> dict:
        conversation = await self.store.create_conversation(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            title=title,
            channel=channel,
            draft=draft,
        )
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="conversation.create",
            resource_id=conversation["conversation_id"],
        )
        return conversation

    async def require_conversation(self, conversation_id: str, tenant_id: str, app_user_id: str) -> dict:
        conversation = await self.store.get(conversation_id, tenant_id, app_user_id)
        if conversation is None:
            raise GpaError("NOT_FOUND", "conversation not found")
        return conversation

    async def list(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        channel: str | None = None,
        query: str | None = None,
        archived: bool = False,
        has_running_task: bool = False,
        cursor: str | None = None,
        limit: int = 30,
    ) -> tuple[list[dict], str | None]:
        return await self.store.list_conversations(
            tenant_id,
            app_user_id,
            channel=channel,
            query=query,
            archived=archived,
            has_running_task=has_running_task,
            cursor=cursor,
            limit=max(1, min(limit, 100)),
        )

    async def purge_drafts(self, ttl_days: int = 7) -> int:
        return await self.store.purge_drafts(ttl_days)

    async def detail(self, conversation_id: str, tenant_id: str, app_user_id: str) -> dict:
        conversation = await self.require_conversation(conversation_id, tenant_id, app_user_id)
        turns = await self.store.list_turns(conversation_id, limit=200)
        if self.files is not None and turns:
            payloads = await self.files.attachment_payloads([t["turn_id"] for t in turns])
            for turn in turns:
                turn["attachments"] = payloads.get(turn["turn_id"], [])
        return {"conversation": conversation, "turns": turns}

    async def update(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict:
        await self.require_conversation(conversation_id, tenant_id, app_user_id)
        if title is not None:
            cleaned = title.strip()
            if not cleaned:
                raise GpaError("VALIDATION_ERROR", "会话名称不能为空")
            await self.store.rename_conversation(
                conversation_id, tenant_id, app_user_id, cleaned
            )
            await record_audit(
                self.store.db,
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                action="conversation.rename",
                resource_id=conversation_id,
            )
        if pinned is not None:
            await self.store.set_pinned(conversation_id, tenant_id, app_user_id, pinned)
            await record_audit(
                self.store.db,
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                action="conversation.pin" if pinned else "conversation.unpin",
                resource_id=conversation_id,
            )
        if archived is not None:
            await self.store.set_archived(conversation_id, tenant_id, app_user_id, archived)
            await record_audit(
                self.store.db,
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                action="conversation.archive" if archived else "conversation.unarchive",
                resource_id=conversation_id,
            )
        return await self.require_conversation(conversation_id, tenant_id, app_user_id)

    async def delete(self, conversation_id: str, tenant_id: str, app_user_id: str) -> None:
        await self.require_conversation(conversation_id, tenant_id, app_user_id)
        if await self.store.has_running_work(conversation_id):
            raise GpaError(
                "CONFLICT",
                "会话仍在运行，请先停止任务或等待完成后再删除",
            )
        if self.tos is not None:
            for artifact in await self.store.conversation_artifacts(conversation_id):
                storage_ref = artifact.get("storage_ref") or ""
                source_ref = artifact.get("source_ref") or ""
                prefix = getattr(self.tos, "prefix", "")
                if (
                    storage_ref
                    and storage_ref != source_ref
                    and prefix
                    and storage_ref.startswith(f"{prefix}/")
                ):
                    try:
                        await self.tos.delete(storage_ref)
                    except Exception as exc:  # noqa: BLE001 - row deletion still wins
                        logger.warning(
                            "artifact cleanup failed conversation=%s key=%s: %s",
                            conversation_id,
                            storage_ref,
                            exc,
                        )
        if not await self.store.delete_conversation(conversation_id, tenant_id, app_user_id):
            raise GpaError("NOT_FOUND", "conversation not found")
        closer = getattr(self.transport, "close_conversation", None)
        if closer is not None:
            await closer(conversation_id)
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="conversation.delete",
            resource_id=conversation_id,
        )

    async def commit_memory(
        self, conversation_id: str, tenant_id: str, app_user_id: str
    ) -> dict:
        await self.require_conversation(conversation_id, tenant_id, app_user_id)
        if self.context_writer is None:
            raise GpaError("CONFLICT", "记忆写入未启用")
        state = await self.store.get_ov_state(conversation_id)
        if state is None or not state.get("ov_session_id"):
            raise GpaError("CONFLICT", "当前会话还没有可保存的对话内容")
        if int(state.get("ov_message_count") or 0) <= 0:
            raise GpaError("CONFLICT", "当前没有新的对话内容需要保存")
        if not await self.context_writer.commit_now(conversation_id):
            raise GpaError("UPSTREAM_UNAVAILABLE", "记忆提交失败，请稍后重试")
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="conversation.memory_commit",
            resource_id=conversation_id,
        )
        return {"status": "queued", "message": "已提交，OpenViking 正在整理记忆"}

    async def timeline(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        *,
        after_seq: int = 0,
        limit: int = 50,
    ) -> dict:
        await self.require_conversation(conversation_id, tenant_id, app_user_id)
        items, next_cursor, has_more = await self.store.list_timeline(
            conversation_id, after_seq=after_seq, limit=max(1, min(limit, 200))
        )
        if self.files is not None:
            turn_ids = [item["turn_id"] for item in items if item.get("kind") == "turn" and item.get("turn_id")]
            payloads = await self.files.attachment_payloads(turn_ids)
            for item in items:
                if item.get("kind") == "turn" and item.get("turn_id"):
                    item["attachments"] = payloads.get(item["turn_id"], [])
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    # ------------------------------------------------------------------ text turn
    async def submit_text(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        *,
        content: str,
        client_submission_id: str | None = None,
        channel: str = "text",
        attachment_ids: list[str] | None = None,
    ) -> dict:
        attachment_ids = list(dict.fromkeys(attachment_ids or []))
        if len(attachment_ids) > int(getattr(self.settings, "gpa_file_max_per_message", 10)):
            raise GpaError("VALIDATION_ERROR", "too many attachments")
        trace_id = new_trace_id()
        turn: dict | None = None
        started_event: dict | None = None
        async with self.store.db.pool.acquire() as conn:
            async with conn.transaction():
                conversation = await self.store.get_for_update(conn, conversation_id, tenant_id, app_user_id)
                if conversation is None:
                    raise GpaError("NOT_FOUND", "conversation not found")
                attachment_rows: list[dict] = []
                for attachment_id in (attachment_ids or []):
                    if self.files is None:
                        raise GpaError("VALIDATION_ERROR", "attachments are not supported")
                    row = await self.files.store.get_conversation_file(
                        attachment_id, conversation_id, tenant_id, app_user_id,
                        conn=conn,
                    )
                    if row is None:
                        raise GpaError("VALIDATION_ERROR", "attachment not found in conversation")
                    attachment_rows.append(row)
                fingerprint = None
                if attachment_rows:
                    material = "|".join(
                        f"{row['file_id']}:{row.get('sha256') or ''}"
                        for row in sorted(attachment_rows, key=lambda r: r["file_id"])
                    )
                    fingerprint = hashlib.sha256(material.encode()).hexdigest()
                if client_submission_id:
                    existing = await self.store.get_turn_by_submission_conn(
                        conn, conversation_id, client_submission_id
                    )
                    if existing is not None:
                        if existing["user_text"] != content or existing.get("attachment_fingerprint") != fingerprint:
                            raise GpaError(
                                "IDEMPOTENCY_MISMATCH",
                                "clientSubmissionId reused with different content or attachments",
                            )
                        return self._accepted(existing)
                running = await self.store.get_running_turn(conn, conversation_id)
                if running is not None:
                    raise GpaError(
                        "CONFLICT",
                        "a turn is already running in this conversation",
                        details={"turnId": running["turn_id"]},
                    )
                turn, started_event = await self.store.create_turn(
                    conn,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    app_user_id=app_user_id,
                    channel=channel,
                    user_text=content,
                    client_submission_id=client_submission_id,
                    trace_id=trace_id,
                    attachment_fingerprint=fingerprint,
                )
                if attachment_rows:
                    await self.files.store.insert_turn_attachments_conn(
                        conn, turn["turn_id"], attachment_rows
                    )
        assert turn is not None and started_event is not None
        self.bus.publish(conversation_id, started_event)
        task = asyncio.create_task(
            self._run_turn(
                conversation=dict(conversation),
                turn=turn,
                trace_id=trace_id,
            ),
            name=f"gpa-turn-{turn['turn_id']}",
        )
        self._running[turn["turn_id"]] = task
        task.add_done_callback(lambda _t, tid=turn["turn_id"]: self._running.pop(tid, None))
        await record_audit(
            self.store.db,
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            action="message.submit",
            resource_id=turn["turn_id"],
            trace_id=trace_id,
        )
        return self._accepted(turn)

    def _accepted(self, turn: dict) -> dict:
        return {
            "turn_id": turn["turn_id"],
            "status": turn["status"],
            "events_url": f"/api/conversations/{turn['conversation_id']}/events",
        }

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Wait for in-flight turn tasks to settle, then stop the stragglers.

        Closing the pool (or deleting a test user's rows) while a turn is
        still settling makes ``_settle``'s transaction race the shutdown:
        either it deadlocks with a cleanup DELETE or it later raises
        ``pool is closing`` as an unretrieved task exception. Callers that
        own the lifecycle must drain first.
        """
        tasks = [t for t in self._running.values() if not t.done()]
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    _KB_INTENT = re.compile(
        r"(保存|存|加入|归档|沉淀|放到|放进|上传|同步).{0,16}(知识库|共享库)"
        r"|(知识库|共享库).{0,16}(保存|存|加入|归档|沉淀|上传)"
    )

    async def _auto_promote_for_kb(
        self, *, turn: dict, attachments: list[dict], scope: str
    ) -> str:
        """Persist explicitly requested attachments before the model answers.

        This is deliberately deterministic: if the user says "save to the KB"
        in the same turn as uploaded files, the KB write must not depend on the
        model choosing a tool.  The model still gets a note with the resulting
        document state.
        """
        if self.kb is None or not attachments:
            return ""
        try:
            role = await self.kb.role_for(turn["app_user_id"], turn["tenant_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb promote role lookup failed: %s", exc)
            return ""
        if scope == "shared" and role != "admin":
            return "用户要求保存到共享知识库，但当前账号不是管理员；不要声称已保存。"
        notes: list[str] = []
        seen: set[str] = set()
        for item in attachments:
            file_id = str(item.get("fileId") or "")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            try:
                result = await self.kb.promote(
                    file_id,
                    tenant_id=turn["tenant_id"],
                    app_user_id=turn["app_user_id"],
                    role=role,
                    scope=scope,
                    category_id=None,
                )
                doc = result.get("doc") or {}
                notes.append(
                    f"{item.get('name') or file_id} -> {doc.get('doc_id') or '?'}"
                    f"（{doc.get('index_status') or 'pending'}，scope={scope}）"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto promote failed file=%s: %s", file_id, exc)
                notes.append(f"{item.get('name') or file_id} 保存失败")
        return "；".join(notes)

    async def _run_turn(self, *, conversation: dict, turn: dict, trace_id: str) -> None:
        conversation_id = turn["conversation_id"]
        turn_id = turn["turn_id"]
        channel = turn["channel"]
        text_parts: list[str] = []
        final_text = ""
        error: dict | None = None
        metrics: dict[str, Any] = {}
        try:
            session = await self.transport.open(
                AgentSessionRequest(
                    conversation_id=conversation_id,
                    tenant_id=conversation["tenant_id"],
                    app_user_id=conversation["app_user_id"],
                    channel=channel,
                )
            )
            attachment_payloads = []
            if self.files is not None:
                payloads = await self.files.attachment_payloads([turn_id])
                attachment_payloads = payloads.get(turn_id, [])
            kb_turn = {
                **turn,
                "tenant_id": conversation["tenant_id"],
                "app_user_id": conversation["app_user_id"],
            }
            kb_scope = kb_intent_scope(turn["user_text"]) if attachment_payloads else None
            promote_note = (
                await self._auto_promote_for_kb(
                    turn=kb_turn,
                    attachments=attachment_payloads,
                    scope=kb_scope,
                )
                if kb_scope
                else ""
            )
            runtime_text = turn["user_text"]
            if promote_note:
                runtime_text = (
                    f"{runtime_text}\n\n[系统执行结果]\n"
                    f"附件知识库写入：{promote_note}\n"
                    "如果状态是 pending/indexing，只能说明已提交索引，不能声称已被检索到。"
                )
            request = AgentTurnRequest(
                turn_id=turn_id,
                conversation_id=conversation_id,
                user_text=runtime_text,
                channel=channel,
                trace_id=trace_id,
                attachments=attachment_payloads,
            )
            async with asyncio.timeout(max(5, int(self.settings.gpa_turn_timeout_s))):
                async for event in self.transport.send(session, request):
                    kind = event.type
                    payload = event.payload or {}
                    if kind == "message.delta":
                        text_parts.append(str(payload.get("text") or ""))
                        self.bus.publish(conversation_id, _live_delta(event))
                        continue
                    if kind == "message.final":
                        final_text = str(payload.get("text") or "".join(text_parts))
                    if kind not in PERSISTED_EVENT_TYPES:
                        continue
                    task_id = payload.get("taskId") or payload.get("task_id")
                    async with self.store.db.pool.acquire() as conn:
                        async with conn.transaction():
                            if kind == "message.final":
                                await self.store.update_turn(conn, turn_id, assistant_text=final_text)
                            stored = await self.store.append_event(
                                conn,
                                conversation_id=conversation_id,
                                type=kind,
                                payload=payload,
                                turn_id=turn_id,
                                task_id=task_id,
                                trace_id=trace_id,
                                event_id=event.event_id,
                            )
                    if kind == "error":
                        error = payload
                    self.bus.publish(conversation_id, stored)
            metrics = dict(getattr(session.runtime, "last_metrics", {}) or {})
            status = "failed" if error else "completed"
            await self._settle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                status=status,
                assistant_text=final_text or "".join(text_parts),
                metrics=metrics,
                error=error,
            )
        except asyncio.CancelledError:
            await self._settle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                status="cancelled",
                assistant_text=final_text or "".join(text_parts),
                metrics=metrics,
                error={"code": "CANCELLED", "message": "turn cancelled by user"},
            )
        except TimeoutError:
            await self._settle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                status="failed",
                assistant_text=final_text or "".join(text_parts),
                metrics=metrics,
                error={"code": "TIMEOUT", "message": "turn exceeded its time budget"},
            )
        except Exception as exc:  # noqa: BLE001 - turn failures must be surfaced, not lost
            logger.exception("gpa turn failed conversation=%s turn=%s", conversation_id, turn_id)
            await self._settle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                status="failed",
                assistant_text=final_text or "".join(text_parts),
                metrics=metrics,
                error={"code": "UPSTREAM_UNAVAILABLE", "message": str(exc)[:300]},
            )
        finally:
            self._running.pop(turn_id, None)

    async def _settle(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        status: str,
        assistant_text: str,
        metrics: dict[str, Any],
        error: dict | None,
    ) -> dict | None:
        async with self.store.db.pool.acquire() as conn:
            async with conn.transaction():
                current = await self.store.get_turn_for_update(conn, turn_id)
                if current is None or current["status"] != "running":
                    return None
                await self.store.update_turn(
                    conn,
                    turn_id,
                    status=status,
                    assistant_text=assistant_text,
                    metrics=metrics,
                    ended_at=datetime.now(UTC),
                )
                stored = await self.store.append_event(
                    conn,
                    conversation_id=conversation_id,
                    type="turn.settled",
                    payload={
                        "outcome": status,
                        "status": status,
                        "assistantText": assistant_text,
                        "metrics": metrics,
                        "error": error,
                    },
                    turn_id=turn_id,
                    trace_id=current.get("trace_id"),
                )
        self.bus.publish(conversation_id, stored)
        if status == "completed" and self.context_writer is not None:
            self.context_writer.schedule_turn(conversation_id, turn_id)
        return stored

    async def stop(
        self, conversation_id: str, tenant_id: str, app_user_id: str, reason: str = "user"
    ) -> dict:
        await self.require_conversation(conversation_id, tenant_id, app_user_id)
        turn = await self.store.current_running_turn(conversation_id)
        if turn is None:
            return {"turn_id": "", "status": "idle"}
        started = time.monotonic()
        session = getattr(self.transport, "_sessions", {}).get(conversation_id)
        if session is not None:
            try:
                await asyncio.wait_for(self.transport.cancel(session, turn["turn_id"], reason), timeout=0.8)
            except Exception as exc:  # noqa: BLE001 - cancellation must still progress
                logger.warning("transport cancel failed: %s", exc)
        task = self._running.get(turn["turn_id"])
        if task is not None:
            task.cancel()
        else:
            # Process restart or a turn owned by another worker: settle it here so
            # clients never see a permanently running turn.
            await self._settle(
                conversation_id=conversation_id,
                turn_id=turn["turn_id"],
                status="cancelled",
                assistant_text=turn.get("assistant_text") or "",
                metrics=turn.get("metrics") or {},
                error={"code": "CANCELLED", "message": "turn cancelled by user"},
            )
        logger.info(
            "cancel ack conversation=%s turn=%s latency_ms=%s",
            conversation_id,
            turn["turn_id"],
            int((time.monotonic() - started) * 1000),
        )
        return {"turn_id": turn["turn_id"], "status": "cancelling"}

    async def recover_stale_turns(self) -> int:
        turns = await self.store.stale_running_turns()
        recovered = 0
        for turn in turns:
            settled = await self._settle(
                conversation_id=turn["conversation_id"],
                turn_id=turn["turn_id"],
                status="failed",
                assistant_text=turn.get("assistant_text") or "",
                metrics={"recovered": "process_restart"},
                error={"code": "UPSTREAM_UNAVAILABLE", "message": "process restarted mid-turn"},
            )
            if settled is not None:
                recovered += 1
        return recovered


def _live_delta(event) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "event_id": event.event_id,
        "seq": event.seq,
        "type": "message.delta",
        "conversation_id": event.conversation_id,
        "turn_id": event.turn_id,
        "trace_id": event.trace_id,
        "payload": event.payload,
        "occurred_at": to_iso(event.occurred_at),
    }
