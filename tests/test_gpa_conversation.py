"""P1 acceptance tests: conversation/turn/SSE core, context, task inbox."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest

from general_personal_agent.gpa.agent.transport import (
    AgentTurnRequest,
    EventStream,
    InProcessTransport,
)
from general_personal_agent.gpa.context.assembler import (
    STATE_CHANNEL,
    STATE_CONVERSATION_ID,
    STATE_TRACE_ID,
    STATE_TURN_ID,
    STATE_USER_TEXT,
    build_context_plugin,
)
from general_personal_agent.gpa.conversation.service import ConversationService
from general_personal_agent.gpa.errors import GpaError

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _wait_turn(env, conversation_id: str, turn_id: str, timeout: float = 8.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        detail = await env.conversations.detail(conversation_id, env.tenant, env.user)
        for turn in detail["turns"]:
            if turn["turn_id"] == turn_id and turn["status"] != "running":
                return turn
        await asyncio.sleep(0.02)
    raise AssertionError(f"turn {turn_id} did not settle")


@pytest.mark.integration
async def test_text_turn_completes_and_reuses_conversation(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="文本验收", channel="text"
    )
    cid = conversation["conversation_id"]
    accepted = await env.conversations.submit_text(
        cid,
        env.tenant,
        env.user,
        content="你好，介绍一下你自己",
        client_submission_id="t1",
    )
    turn = await _wait_turn(env, cid, accepted["turn_id"])
    assert turn["status"] == "completed"
    assert "收到" in turn["assistant_text"]

    events = await env.store.list_events(cid)
    types = [event["type"] for event in events]
    assert types[0] == "turn.started"
    assert "message.final" in types
    assert types[-1] == "turn.settled"

    # clientSubmissionId replay returns the same turn, no duplicated work.
    again = await env.conversations.submit_text(
        cid,
        env.tenant,
        env.user,
        content="你好，介绍一下你自己",
        client_submission_id="t1",
    )
    assert again["turn_id"] == accepted["turn_id"]
    assert len(await env.store.list_events(cid)) == len(events)

    # Text and voice share the same conversation and merge channels.
    voice = await env.conversations.submit_text(
        cid, env.tenant, env.user, content="用语音继续", channel="voice"
    )
    await _wait_turn(env, cid, voice["turn_id"])
    detail = await env.conversations.detail(cid, env.tenant, env.user)
    assert len(detail["turns"]) == 2
    assert set(detail["conversation"]["channels"]) == {"text", "voice"}
    assert detail["conversation"]["active_channel"] == "voice"

    trace = await env.db.pool.fetchrow("SELECT * FROM context_trace WHERE turn_id = $1", accepted["turn_id"])
    assert trace is not None
    assert trace["channel"] == "text"

    timeline = await env.conversations.timeline(cid, env.tenant, env.user)
    assert [item["kind"] for item in timeline["items"]].count("turn") == 2


class _SlowRuntime:
    def __init__(self):
        self.cancelled: list[str] = []
        self.last_metrics: dict = {}

    async def start(self):
        return None

    async def stream(self, turn: AgentTurnRequest):
        stream = EventStream(
            conversation_id=turn.conversation_id, turn_id=turn.turn_id, trace_id=turn.trace_id
        )
        yield stream.emit("message.delta", {"text": "处理中"})
        await asyncio.sleep(30)
        yield stream.emit("message.final", {"text": "处理中完成"})

    async def cancel(self, turn_id: str, reason: str):
        self.cancelled.append(turn_id)

    async def close(self):
        return None


async def _slow_service(env):
    runtime = _SlowRuntime()

    async def factory(_request):
        return runtime

    transport = InProcessTransport(factory, mode="test")
    service = ConversationService(env.settings, env.store, env.gpa.bus, transport, env.gpa.assembler)
    return service, runtime


@pytest.mark.integration
async def test_running_turn_conflicts_and_stop_really_cancels(gpa_env):
    env = gpa_env
    service, runtime = await _slow_service(env)
    conversation = await service.create(
        tenant_id=env.tenant, app_user_id=env.user, title="cancel", channel="text"
    )
    cid = conversation["conversation_id"]
    first = await service.submit_text(cid, env.tenant, env.user, content="开始")
    await asyncio.sleep(0.1)
    with pytest.raises(GpaError) as exc:
        await service.submit_text(cid, env.tenant, env.user, content="插队")
    assert exc.value.code == "CONFLICT"

    stopped = await service.stop(cid, env.tenant, env.user)
    assert stopped["status"] == "cancelling"
    turn = await _wait_turn(env, cid, first["turn_id"])
    assert turn["status"] == "cancelled"
    assert first["turn_id"] in runtime.cancelled
    assert service._running == {}

    events = await env.store.list_events(cid)
    assert events[-1]["type"] == "turn.settled"
    assert events[-1]["payload"]["outcome"] == "cancelled"


@pytest.mark.integration
async def test_cross_user_access_is_not_found(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="private", channel="text"
    )
    with pytest.raises(GpaError) as exc:
        await env.conversations.detail(conversation["conversation_id"], env.tenant, "someone_else")
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(GpaError) as exc:
        await env.conversations.submit_text(
            conversation["conversation_id"], env.tenant, "someone_else", content="越权"
        )
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.integration
async def test_task_inbox_projects_timeline_and_context(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="任务", channel="text"
    )
    cid = conversation["conversation_id"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="做一张海报",
        task_type="poster",
        idempotency_key="task-idem-1",
    )
    assert task["status"] == "queued"
    same = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="做一张海报",
        task_type="poster",
        idempotency_key="task-idem-1",
    )
    assert same["task_id"] == task["task_id"]

    # A dev server running against the same database may project this event
    # first; the durable assertion is the projected timeline below. Keep the
    # call so this test still exercises the inbox when it is the only consumer.
    queue, unsubscribe = env.gpa.bus.subscribe(cid)
    try:
        await env.inbox.run_once()
        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert message["type"] == "task.created"
    finally:
        unsubscribe()
    events = await env.store.list_events(cid)
    assert "task.created" in [event["type"] for event in events]

    packet = await env.gpa.assembler.build(
        conversation_id=cid,
        turn_id="turn_ctx",
        channel="text",
        user_text="任务怎么样了",
        tenant_id=env.tenant,
        app_user_id=env.user,
        trace_id="trace_ctx",
    )
    assert packet.task_context and packet.task_context[0].task_id == task["task_id"]

    artifact = await env.task_store.add_artifact(
        task_id=task["task_id"],
        name="poster.png",
        mime="image/png",
        bytes=10,
        storage_ref="general-personal-agent/poster.png",
    )
    await env.task_store.append_event(
        task["task_id"], "artifact", {"artifactId": artifact["artifact_id"], "name": "poster.png"}
    )
    await env.inbox.run_once()
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["artifacts"][0]["download_url"].endswith("/download")

    await env.tasks.cancel(task["task_id"], env.tenant, env.user, reason="用户取消")
    await env.inbox.run_once()
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "cancelled"
    timeline = await env.conversations.timeline(cid, env.tenant, env.user, limit=100)
    types = {item.get("type") for item in timeline["items"] if item["kind"] == "event"}
    assert {"task.created", "task.status", "artifact"} <= types


@pytest.mark.integration
async def test_context_plugin_renders_task_context(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="plugin", channel="text"
    )
    cid = conversation["conversation_id"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="整理会议纪要",
        task_type="summary",
    )
    plugin = build_context_plugin(assembler=env.gpa.assembler, tenant_id=env.tenant, app_user_id=env.user)
    request = LlmRequest(model="test")
    context = SimpleNamespace(
        state={
            STATE_TURN_ID: "turn_plugin",
            STATE_CONVERSATION_ID: cid,
            STATE_CHANNEL: "text",
            STATE_USER_TEXT: "任务怎么样了",
            STATE_TRACE_ID: "trace_plugin",
        }
    )
    result = await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert result is None
    instruction = request.config.system_instruction or ""
    assert "任务上下文" in instruction
    assert task["task_id"] in instruction
    assert plugin.last_packet is not None


@pytest.mark.integration
async def test_startup_recovery_fails_orphan_turn(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="orphan", channel="text"
    )
    cid = conversation["conversation_id"]
    async with env.db.pool.acquire() as conn:
        turn, _ = await env.store.create_turn(
            conn,
            conversation_id=cid,
            tenant_id=env.tenant,
            app_user_id=env.user,
            channel="text",
            user_text="orphan",
            client_submission_id=None,
            trace_id="trace_orphan",
        )
    assert await env.conversations.recover_stale_turns() >= 1
    recovered = await env.store.get_turn(turn["turn_id"])
    assert recovered["status"] == "failed"
