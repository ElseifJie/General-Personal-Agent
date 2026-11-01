"""ADR-019: draft visibility, auto title, cross-conversation tasks and D8."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.task.tools import TaskToolState, build_task_tools
from general_personal_agent.gpa.voice.session import GpaVoiceSession

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
async def test_draft_hidden_until_first_turn_and_autotitled(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant,
        app_user_id=env.user,
        title="",
        channel="voice",
        draft=True,
    )
    cid = conversation["conversation_id"]
    assert conversation["is_draft"] is True

    items, _ = await env.conversations.list(
        tenant_id=env.tenant, app_user_id=env.user, limit=50
    )
    assert cid not in [item["conversation_id"] for item in items]

    detail = await env.conversations.detail(cid, env.tenant, env.user)
    assert detail["conversation"]["is_draft"] is True

    accepted = await env.conversations.submit_text(
        cid,
        env.tenant,
        env.user,
        content="语音自动标题\n第二行",
        channel="voice",
    )
    await _wait_turn(env, cid, accepted["turn_id"])

    detail = await env.conversations.detail(cid, env.tenant, env.user)
    assert detail["conversation"]["is_draft"] is False
    assert detail["conversation"]["title"] == "语音自动标题"
    items, _ = await env.conversations.list(
        tenant_id=env.tenant, app_user_id=env.user, limit=50
    )
    assert cid in [item["conversation_id"] for item in items]


@pytest.mark.integration
async def test_rename_locks_title_against_first_turn(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant,
        app_user_id=env.user,
        title="",
        channel="text",
        draft=True,
    )
    cid = conversation["conversation_id"]
    await env.conversations.update(cid, env.tenant, env.user, title="手动命名")

    accepted = await env.conversations.submit_text(
        cid, env.tenant, env.user, content="不应覆盖手写标题"
    )
    await _wait_turn(env, cid, accepted["turn_id"])

    detail = await env.conversations.detail(cid, env.tenant, env.user)
    assert detail["conversation"]["title"] == "手动命名"


@pytest.mark.integration
async def test_purge_drafts_only_removes_old_empty_drafts(gpa_env):
    env = gpa_env
    recent = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="", channel="voice", draft=True
    )
    old = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="", channel="voice", draft=True
    )
    promoted = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="", channel="voice", draft=True
    )
    accepted = await env.conversations.submit_text(
        promoted["conversation_id"], env.tenant, env.user, content="已发言", channel="voice"
    )
    await _wait_turn(env, promoted["conversation_id"], accepted["turn_id"])

    await env.db.pool.execute(
        "UPDATE conversation SET created_at = now() - interval '8 days' WHERE conversation_id = $1",
        old["conversation_id"],
    )
    purged = await env.conversations.purge_drafts(7)
    assert purged == 1
    assert await env.conversations.store.get(
        old["conversation_id"], env.tenant, env.user
    ) is None
    assert await env.conversations.store.get(
        recent["conversation_id"], env.tenant, env.user
    ) is not None
    assert await env.conversations.store.get(
        promoted["conversation_id"], env.tenant, env.user
    ) is not None


@pytest.mark.integration
async def test_has_running_task_filter(gpa_env):
    env = gpa_env
    with_task = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="有任务", channel="text"
    )
    without_task = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="无任务", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=with_task["conversation_id"],
        goal="做一份报告",
        task_type="report",
    )

    items, _ = await env.conversations.list(
        tenant_id=env.tenant, app_user_id=env.user, has_running_task=True, limit=50
    )
    ids = [item["conversation_id"] for item in items]
    assert with_task["conversation_id"] in ids
    assert without_task["conversation_id"] not in ids

    await env.db.pool.execute(
        "UPDATE task_run SET status = 'completed' WHERE task_id = $1", task["task_id"]
    )
    items, _ = await env.conversations.list(
        tenant_id=env.tenant, app_user_id=env.user, has_running_task=True, limit=50
    )
    assert with_task["conversation_id"] not in [
        item["conversation_id"] for item in items
    ]


@pytest.mark.integration
async def test_list_tasks_defaults_to_all_conversations(gpa_env):
    env = gpa_env
    first = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="任务A", channel="text"
    )
    second = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="任务B", channel="text"
    )
    state = TaskToolState(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=first["conversation_id"],
        turn_id="turn_cross",
    )
    tools = {tool.__name__: tool for tool in build_task_tools(state, env.tasks)}
    first_task = await tools["submit_task"]("第一个任务", "report")
    second_task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=second["conversation_id"],
        goal="第二个任务",
        task_type="report",
    )

    all_tasks = await tools["list_tasks"]()
    all_ids = {item["task_id"] for item in all_tasks}
    assert {first_task["task_id"], second_task["task_id"]} <= all_ids
    assert all("conversation_id" in item for item in all_tasks)

    scoped = await tools["list_tasks"](scope="conversation")
    assert {item["task_id"] for item in scoped} == {first_task["task_id"]}


class _FakeTTSStream:
    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_voice_close_does_not_stop_running_turn():
    stopped: list[str] = []

    async def stop(*args, **kwargs):
        stopped.append(args[0] if args else "?")

    gpa = SimpleNamespace(conversations=SimpleNamespace(stop=stop))
    settings = SimpleNamespace(tts_max_sentence_chars=30, gpa_voice_filler_enabled=False)
    conversation = {
        "conversation_id": "c_d8",
        "tenant_id": "demo",
        "app_user_id": "u_d8",
        "active_channel": "voice",
        "channels": ["voice"],
    }
    session = GpaVoiceSession(
        websocket=object(),
        gpa=gpa,
        conversation=conversation,
        settings=settings,
        tts_http=None,
        tts_stream_factory=lambda _settings: _FakeTTSStream(),
    )
    session._turn_id = "turn_running"
    await session.close()
    assert stopped == []


class _CueWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class _CueBus:
    def subscribe(self, conversation_id: str):
        return asyncio.Queue(), lambda: None


class _CueTTSHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize_pcm(self, text: str) -> bytes:
        self.calls.append(text)
        return b"\x00\x00" * 16


class _CueTTSStream:
    async def warmup(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _cue_session(*, resume_cue: bool) -> tuple[GpaVoiceSession, _CueWebSocket, _CueTTSHttp]:
    gpa = SimpleNamespace(bus=_CueBus(), conversations=SimpleNamespace())
    settings = SimpleNamespace(
        tts_max_sentence_chars=30,
        gpa_voice_filler_enabled=False,
        tts_sample_rate=24_000,
        asr_sample_rate=16_000,
    )
    websocket = _CueWebSocket()
    tts_http = _CueTTSHttp()
    session = GpaVoiceSession(
        websocket=websocket,
        gpa=gpa,
        conversation={
            "conversation_id": "c_cue",
            "tenant_id": "demo",
            "app_user_id": "u_cue",
            "active_channel": "voice",
            "channels": ["voice"],
        },
        settings=settings,
        tts_http=tts_http,
        tts_stream_factory=lambda _settings: _CueTTSStream(),
        resume_cue=resume_cue,
    )
    return session, websocket, tts_http


@pytest.mark.integration
async def test_resume_cue_plays_once_for_resumed_session():
    session, websocket, _tts = _cue_session(resume_cue=True)
    try:
        await session.start()
        await asyncio.sleep(0.1)
        types = [message["type"] for message in websocket.messages]
        assert "resume_cue" in types
        assert "audio" in types
        assert types.index("resume_cue") < types.index("audio")
    finally:
        await session.close()


@pytest.mark.integration
async def test_resume_cue_is_skipped_for_new_session_or_talking_user():
    session, websocket, _tts = _cue_session(resume_cue=False)
    try:
        await session.start()
        await asyncio.sleep(0.05)
        assert "resume_cue" not in [m["type"] for m in websocket.messages]
    finally:
        await session.close()

    talking, talking_ws, _tts2 = _cue_session(resume_cue=True)
    talking._user_audio_started = True
    try:
        await talking.start()
        await asyncio.sleep(0.05)
        assert "resume_cue" not in [m["type"] for m in talking_ws.messages]
    finally:
        await talking.close()
