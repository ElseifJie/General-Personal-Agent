"""Context assembly and the ADK plugin that owns it.

The plugin is the only component that turns conversation/task state into the
next model call's context. Adapters never touch OV, KB or MA directly.

Deterministic slices (recent turns, task context, artifacts) come with
deadlines and trace. OV memory + personal/admin KB slices are added through
an injectable retriever; failures degrade instead of blocking the turn.
"""

from __future__ import annotations

import asyncio

from general_personal_agent.gpa.context.packet import ContextPacket, TaskContextItem, TokenBudget
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.schemas import Channel
from general_personal_agent.gpa.util import to_iso

logger = get_logger(__name__)

# State keys carried through ADK's run_async(state_delta=...).
STATE_TURN_ID = "gpa_turn_id"
STATE_CONVERSATION_ID = "gpa_conversation_id"
STATE_CHANNEL = "gpa_channel"
STATE_USER_TEXT = "gpa_user_text"
STATE_TRACE_ID = "gpa_trace_id"
STATE_TENANT_ID = "gpa_tenant_id"
STATE_APP_USER_ID = "gpa_app_user_id"


def estimate_tokens(text: str, ratio: float = 0.75) -> int:
    return max(1, int(len(text or "") * ratio))


def render_context_message(packet: ContextPacket) -> str:
    """Render only slices the model cannot recover from ADK's session history."""
    lines: list[str] = ["[GPA 上下文]"]
    has_content = False
    if packet.session_summary.strip():
        lines.append(f"会话摘要：{packet.session_summary.strip()}")
        has_content = True
    if packet.task_context:
        lines.append("任务上下文：")
        for task in packet.task_context:
            summary = f" - {task.summary}" if task.summary else ""
            lines.append(f"- {task.task_id} [{task.status}] {task.task_type}{summary}")
        lines.append("任务状态是事实数据；不要编造未列出的任务或产物。")
        has_content = True
    if packet.attachments:
        lines.append("可用产物：")
        for item in packet.attachments:
            lines.append(f"- {item.artifact_id} {item.name} ({item.mime or 'unknown'})")
        has_content = True
    if packet.long_term_memory:
        lines.append("长期记忆：")
        for item in packet.long_term_memory[:5]:
            text = str(item.get("text") or item.get("snippet") or "").strip()
            if not text:
                continue
            label = str(item.get("memoryTypeLabel") or item.get("memoryType") or "").strip()
            lines.append(f"- [{label}] {text}" if label else f"- {text}")
        has_content = True
    if packet.personal_kb or packet.shared_kb:
        lines.append("知识库片段：")
        for item in (packet.personal_kb + packet.shared_kb)[:5]:
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if title or snippet:
                scope = "共享" if item.get("scope") == "shared" else "个人"
                lines.append(f"- [{scope}] {title}: {snippet}".strip(": "))
        has_content = True
    if packet.skills:
        lines.append("可用技能：")
        for item in packet.skills[:4]:
            name = str(item.get("name") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if name:
                lines.append(f"- {name}：{snippet}".rstrip("："))
        has_content = True
    if packet.degraded:
        lines.append(f"上下文降级：{', '.join(packet.degraded)}")
    if not has_content:
        return ""
    return "\n".join(lines)


def fit_packet_to_budget(packet: ContextPacket, budget: int, ratio: float = 0.75) -> ContextPacket:
    """Clip the packet until its actually rendered prompt fits the budget.

    ``token_budget.used`` is the estimate of the rendered text, never a clamped
    counter: a trace that says 2000 while the prompt is 4868 is worse than no
    trace at all.
    """
    total = max(1, int(budget))
    fitted = packet.model_copy(deep=True)
    fitted.token_budget = TokenBudget(total=total, used=0)

    def measure() -> int:
        return estimate_tokens(render_context_message(fitted), ratio)

    used = measure()
    if used <= total:
        fitted.token_budget.used = used
        return fitted

    # Shorten every optional snippet first; headings and the task/attachment
    # facts stay intact as long as possible.
    cap = max(80, min(800, total * 2 // 3))
    for item in (
        list(fitted.long_term_memory) + list(fitted.personal_kb)
        + list(fitted.shared_kb) + list(fitted.skills)
    ):
        for key in ("text", "snippet"):
            value = item.get(key)
            if isinstance(value, str) and len(value) > cap:
                item[key] = value[:cap]
    used = measure()
    if used > total:
        for task in fitted.task_context:
            task.summary = task.summary[:240]
        used = measure()

    # Drop optional slices from least to most important.
    for attr in ("skills", "shared_kb", "personal_kb", "long_term_memory", "attachments", "task_context"):
        items = getattr(fitted, attr)
        while items and used > total:
            items.pop()
            used = measure()

    if used > total:
        fitted.session_summary = fitted.session_summary[: max(0, total)]
        used = measure()
    if used > total:
        for task in fitted.task_context:
            task.summary = task.summary[:120]
        used = measure()

    fitted.token_budget.used = used
    return fitted


class ContextAssembler:
    def __init__(self, settings, store, retriever=None):
        self.settings = settings
        self.store = store
        self.retriever = retriever
        self.db = store.db

    async def build(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        channel: Channel,
        user_text: str,
        tenant_id: str,
        app_user_id: str,
        trace_id: str,
        budget_ms: int | None = None,
    ) -> ContextPacket:
        budget_ms = budget_ms or (
            self.settings.gpa_voice_budget_ms if channel == "voice" else self.settings.gpa_text_budget_ms
        )
        degraded: list[str] = []
        started = asyncio.get_running_loop().time()

        # DB slices share the assembly budget, but a Postgres round-trip does
        # not get cheaper because the budget is small: under load even an
        # indexed query can exceed 200 ms, and silently dropping task facts
        # over a tight cap loses the answer the user asked for. Keep a 250 ms
        # floor; the slices run in parallel, so the wall clock stays inside
        # the budget anyway.
        async def slice_recent_turns():
            try:
                turns = await asyncio.wait_for(
                    self.store.list_turns(conversation_id, limit=8), timeout=max(0.25, budget_ms / 2000)
                )
                return turns, None
            except TimeoutError:
                return [], "recent_turns_timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("recent turns slice failed: %s", exc)
                return [], "recent_turns_error"

        async def slice_tasks():
            try:
                tasks = await asyncio.wait_for(
                    self.store.get_task_context(conversation_id, limit=5),
                    timeout=max(0.25, budget_ms / 2000),
                )
                return tasks, None
            except TimeoutError:
                return [], "task_context_timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("task context slice failed: %s", exc)
                return [], "task_context_error"

        async def slice_artifacts():
            try:
                return await asyncio.wait_for(
                    self.store.get_artifacts(conversation_id, limit=5),
                    timeout=max(0.25, budget_ms / 2000),
                ), None
            except TimeoutError:
                return [], "artifact_timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("artifact slice failed: %s", exc)
                return [], "artifact_error"

        async def slice_memory_kb():
            if self.retriever is None:
                return {}, None
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            remaining = max(50, int(budget_ms - elapsed))
            try:
                result = await asyncio.wait_for(
                    self.retriever.retrieve(user_text, app_user_id, tenant_id, timeout_ms=remaining),
                    timeout=remaining / 1000,
                )
                return result, None
            except TimeoutError:
                return {}, "memory_timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory/kb slice failed: %s", exc)
                return {}, "memory_error"

        turns_result, tasks_result, artifacts_result, memory_result = await asyncio.gather(
            slice_recent_turns(), slice_tasks(), slice_artifacts(), slice_memory_kb()
        )
        turns, turn_error = turns_result
        tasks, task_error = tasks_result
        artifacts, artifact_error = artifacts_result
        memory_kb, memory_error = memory_result
        for reason in (turn_error, task_error, artifact_error, memory_error):
            if reason:
                degraded.append(reason)
        memory_kb = memory_kb or {}
        long_term_memory = list(memory_kb.get("long_term_memory") or [])
        personal_kb = list(memory_kb.get("personal_kb") or [])
        shared_kb = list(memory_kb.get("shared_kb") or [])
        skills = list(memory_kb.get("skills") or [])
        for reason in memory_kb.get("degraded") or []:
            if reason not in degraded:
                degraded.append(str(reason))

        # Recent turns are a fallback/debug view; ADK's session already carries
        # the live message history, so they are not re-rendered into the model.
        recent = []
        for turn in turns[-6:]:
            if turn["status"] not in ("completed", "running"):
                continue
            if turn.get("user_text"):
                recent.append(
                    {"role": "user", "text": turn["user_text"][:500], "ts": to_iso(turn["created_at"])}
                )
            if turn.get("assistant_text"):
                recent.append(
                    {
                        "role": "assistant",
                        "text": turn["assistant_text"][:500],
                        "ts": to_iso(turn["ended_at"] or turn["created_at"]),
                    }
                )

        task_items = [
            TaskContextItem(
                task_id=str(item["task_id"]),
                status=str(item["status"]),
                progress=float(item.get("progress") or 0.0),
                summary=str(item.get("summary") or ""),
                task_type=str(item.get("task_type") or ""),
                updated_at=to_iso(item.get("updated_at")),
            )
            for item in tasks
        ]
        from general_personal_agent.gpa.schemas import ArtifactContextItem

        attachments = [
            ArtifactContextItem(
                artifact_id=str(item["artifact_id"]),
                name=str(item["name"]),
                mime=item.get("mime"),
            )
            for item in artifacts
        ]

        total_budget = int(self.settings.context_token_budget)
        ratio = self.settings.context_token_ratio
        used = sum(estimate_tokens(t["text"], ratio) for t in recent)
        used += sum(estimate_tokens(t.summary, ratio) for t in task_items)
        used += sum(
            estimate_tokens(str(item.get("text") or item.get("snippet") or ""), ratio)
            for item in long_term_memory
        )
        used += sum(estimate_tokens(str(item.get("snippet") or ""), ratio) for item in personal_kb)
        used += sum(estimate_tokens(str(item.get("snippet") or ""), ratio) for item in shared_kb)
        used += sum(estimate_tokens(str(item.get("snippet") or ""), ratio) for item in skills)
        sources = [{"kind": "task", "ref": t.task_id, "score": 1.0} for t in task_items]
        sources += [{"kind": "artifact", "ref": a.artifact_id, "score": 1.0} for a in attachments]
        sources += [{"kind": "turn", "ref": t["turn_id"], "score": 1.0} for t in turns[-3:]]
        sources += [
            {"kind": "memory", "ref": item["uri"], "score": item.get("score", 0.0)}
            for item in long_term_memory
        ]
        sources += [
            {"kind": "kb", "ref": item["uri"], "score": item.get("score", 0.0)} for item in personal_kb
        ]
        sources += [{"kind": "kb", "ref": item["uri"], "score": item.get("score", 0.0)} for item in shared_kb]
        sources += [{"kind": "skill", "ref": item["uri"], "score": item.get("score", 0.0)} for item in skills]

        packet = ContextPacket(
            turn_id=turn_id,
            conversation_id=conversation_id,
            channel=channel,
            user_text=user_text,
            recent_turns=recent,
            long_term_memory=long_term_memory,
            personal_kb=personal_kb,
            shared_kb=shared_kb,
            skills=skills,
            task_context=task_items,
            attachments=attachments,
            token_budget=TokenBudget(total=total_budget, used=used),
            sources=sources,
            degraded=degraded,
            trace_id=trace_id,
        )
        packet = fit_packet_to_budget(packet, total_budget, ratio)
        await self._record_trace(packet, tenant_id=tenant_id, app_user_id=app_user_id)
        logger.debug(
            "context assembled conversation=%s turn=%s used=%s degraded=%s elapsed_ms=%s",
            conversation_id,
            turn_id,
            used,
            degraded,
            int((asyncio.get_running_loop().time() - started) * 1000),
        )
        return packet

    async def _record_trace(self, packet: ContextPacket, *, tenant_id: str, app_user_id: str) -> None:
        try:
            budget = packet.token_budget
            # asyncpg's jsonb codec expects Python objects (see db._init_connection).
            await self.db.pool.execute(
                """
                INSERT INTO context_trace
                  (trace_id, turn_id, conversation_id, app_user_id, query, intent,
                   sources, tokens, degraded, budget, channel)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (trace_id) DO NOTHING
                """,
                packet.trace_id,
                packet.turn_id,
                packet.conversation_id,
                app_user_id,
                packet.user_text[:500],
                None,
                [s.model_dump() for s in packet.sources],
                {"total": budget.total, "used": budget.used},
                packet.degraded,
                {"total": budget.total, "used": budget.used},
                packet.channel,
            )
        except Exception as exc:  # noqa: BLE001 - tracing must never block a reply
            logger.warning("context trace write failed: %s", exc)


def build_context_plugin(
    *,
    assembler: ContextAssembler,
    tenant_id: str,
    app_user_id: str,
    budget_ms: int | None = None,
):
    from google.adk.plugins.base_plugin import BasePlugin

    class _Plugin(BasePlugin):
        def __init__(self):
            super().__init__(name="gpa_context_manager")
            self.assembler = assembler
            self.tenant_id = tenant_id
            self.app_user_id = app_user_id
            self.budget_ms = budget_ms
            self._assembled_turns: set[str] = set()
            self.last_packet: ContextPacket | None = None

        async def before_model_callback(self, *, callback_context, llm_request):
            state = getattr(callback_context, "state", None) or {}
            turn_id = state.get(STATE_TURN_ID)
            if not turn_id or turn_id in self._assembled_turns:
                return None
            conversation_id = state.get(STATE_CONVERSATION_ID)
            if not conversation_id:
                return None
            packet = await self.assembler.build(
                conversation_id=conversation_id,
                turn_id=turn_id,
                channel=state.get(STATE_CHANNEL) or "text",
                user_text=state.get(STATE_USER_TEXT) or "",
                tenant_id=state.get(STATE_TENANT_ID) or self.tenant_id,
                app_user_id=state.get(STATE_APP_USER_ID) or self.app_user_id,
                trace_id=state.get(STATE_TRACE_ID) or "",
                budget_ms=self.budget_ms,
            )
            self._assembled_turns.add(turn_id)
            self.last_packet = packet
            rendered = render_context_message(packet)
            if rendered:
                llm_request.append_instructions([rendered])
            return None

        async def after_agent_callback(self, *, agent, callback_context):
            # Write-back to OV is owned by the ContextOutbox worker. Keep
            # this hook as the single future insertion point.
            return None

        async def on_agent_error_callback(self, *, agent, callback_context, error):
            logger.warning(
                "agent error conversation=%s: %s",
                (getattr(callback_context, "state", None) or {}).get(STATE_CONVERSATION_ID),
                error,
            )
            return None

    return _Plugin()
