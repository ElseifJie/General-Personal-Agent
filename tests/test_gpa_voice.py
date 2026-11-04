"""Voice pipeline tests with fake ASR/TTS (no external audio services)."""

from __future__ import annotations

import asyncio
import time

import pytest

from general_personal_agent.gpa.voice.session import GpaVoiceSession

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)

    def types(self) -> list[str]:
        return [message.get("type", "") for message in self.messages]


class FakeASR:
    def __init__(self):
        self.partial = None
        self.closed = False

    async def start(self, on_partial=None):
        self.partial = on_partial
        if on_partial:
            await on_partial("你")

    async def send(self, _chunk: bytes):
        return None

    async def finish(self) -> str:
        return "你好，帮我总结一下"

    async def close(self):
        self.closed = True


class FakeTTSStream:
    def __init__(self):
        self.sentences: list[str] = []
        self.closed = False

    async def warmup(self):
        return None

    async def stream(self, text: str):
        self.sentences.append(text)
        yield b"\x00\x00" * 240

    async def close(self):
        self.closed = True


class FakeTTSHttp:
    def __init__(self):
        self.sentences: list[str] = []

    async def synthesize_pcm(self, text: str) -> bytes:
        self.sentences.append(text)
        return b"\x00\x00" * 240


class FakeRealtimeASR(FakeASR):
    async def finish(self) -> str:
        return "今天深圳天气怎么样"


async def _wait_for_messages(ws: FakeWebSocket, kind: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if kind in ws.types():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"missing WS message {kind!r}; got {ws.types()}")


@pytest.mark.integration
async def test_voice_turn_uses_same_conversation_and_streams_audio(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="语音验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    tts_stream = FakeTTSStream()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=FakeTTSHttp(),
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=lambda _settings: tts_stream,
    )
    await session.start()
    assert "ready" in ws.types()

    await session.audio(b"\x00" * 640)
    await session.audio_end()
    await _wait_for_messages(ws, "turn.metrics")

    assert "partial" in ws.types()
    assert "final" in ws.types()
    assert "text.delta" in ws.types()
    assert "audio" in ws.types()
    metrics = next(m for m in ws.messages if m["type"] == "turn.metrics")
    assert metrics["outcome"] == "completed"
    assert metrics["metrics"]["first_audio_ms"] >= 0
    # Deltas only feed the live transcript; the whole answer is one TTS request.
    assert tts_stream.sentences.count("收到：你好，帮我总结一下") == 1

    detail = await env.conversations.detail(cid, env.tenant, env.user)
    assert len(detail["turns"]) == 1
    assert detail["turns"][0]["channel"] == "voice"
    assert detail["turns"][0]["user_text"] == "你好，帮我总结一下"
    assert detail["turns"][0]["assistant_text"]

    # The same conversation accepts a text turn afterwards.
    accepted = await env.conversations.submit_text(
        cid, env.tenant, env.user, content="再用文字补充一句"
    )
    await asyncio.sleep(0.1)
    detail = await env.conversations.detail(cid, env.tenant, env.user)
    channels = detail["conversation"]["channels"]
    assert "voice" in channels and "text" in channels
    assert len(detail["turns"]) == 2
    assert accepted["turn_id"]
    await session.close()


@pytest.mark.integration
async def test_voice_interrupt_acks_quickly(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="打断验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    created: list[FakeTTSStream] = []

    def factory(_settings):
        stream = FakeTTSStream()
        created.append(stream)
        return stream

    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=FakeTTSHttp(),
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=factory,
    )
    await session.start()
    started = time.perf_counter()
    await asyncio.wait_for(session.interrupt(), timeout=1.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert "audio_stop" in ws.types()
    assert "interrupted" in ws.types()
    # Idle barge-in must not churn the TTS connection.
    assert len(created) == 1
    await session.close()


@pytest.mark.integration
async def test_voice_realtime_turn_speaks_filler(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="filler 验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    tts_http = FakeTTSHttp()
    tts_stream = FakeTTSStream()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=tts_http,
        asr_factory=lambda _settings: FakeRealtimeASR(),
        tts_stream_factory=lambda _settings: tts_stream,
    )
    await session.start()
    await session.audio(b"\x00" * 640)
    await session.audio_end()
    await _wait_for_messages(ws, "turn.metrics")

    assert "filler" in ws.types()
    filler = next(message for message in ws.messages if message["type"] == "filler")
    assert "查一下" in filler["text"]
    # The filler now uses the same streaming voice as the answer.
    assert any("查一下" in text for text in tts_stream.sentences)
    metrics = next(m for m in ws.messages if m["type"] == "turn.metrics")
    assert metrics["metrics"]["filler_ms"] >= 0
    await session.close()


@pytest.mark.integration
async def test_voice_forwards_tool_progress_and_keeps_task_events_on_barge_in(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="工具事件验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=FakeTTSHttp(),
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=lambda _settings: FakeTTSStream(),
    )
    await session.start()
    session._turn_id = "t1"  # simulate an active turn for tool progress

    env.gpa.bus.publish(
        cid,
        {"type": "tool.call", "turn_id": "t1", "payload": {"name": "web_search"}},
    )
    await _wait_for_messages(ws, "tool_progress")
    progress = next(message for message in ws.messages if message["type"] == "tool_progress")
    assert progress["name"] == "web_search"
    assert progress["label"] == "正在查资料"

    env.gpa.bus.publish(
        cid,
        {
            "type": "task.created",
            "turn_id": None,
            "task_id": "t1",
            "payload": {"taskId": "t1", "status": "queued", "goal": "写一份周报"},
        },
    )
    await _wait_for_messages(ws, "task")
    await session.interrupt()
    env.gpa.bus.publish(
        cid,
        {
            "type": "task.status",
            "turn_id": None,
            "task_id": "t1",
            "payload": {"taskId": "t1", "status": "running", "summary": "进行中"},
        },
    )
    await _wait_for_messages(ws, "task")
    tasks = [message for message in ws.messages if message["type"] == "task"]
    assert any(message["task"]["taskId"] == "t1" for message in tasks)
    await session.close()


class PartialFailTTSStream(FakeTTSStream):
    """Yields one audio chunk, then drops the socket mid-chunk."""

    async def stream(self, text: str):
        self.sentences.append(text)
        yield b"\x00\x00" * 240
        raise RuntimeError("tts socket closed")


@pytest.mark.integration
async def test_voice_final_only_answer_uses_one_continuous_tts_request(gpa_env):
    """A whole answer must not be cut into fragments that restart the voice."""
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="整句合成验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    tts_stream = FakeTTSStream()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=FakeTTSHttp(),
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=lambda _settings: tts_stream,
    )
    await session.start()
    session._turn_id = "t1"
    answer = "我叫 GPA，是 VeADK 团队开发的个人助理。有什么需要我帮忙的？"

    env.gpa.bus.publish(
        cid, {"type": "message.final", "turn_id": "t1", "payload": {"text": answer}}
    )
    env.gpa.bus.publish(
        cid, {"type": "turn.settled", "turn_id": "t1", "payload": {"status": "completed"}}
    )
    await _wait_for_messages(ws, "turn.metrics")

    assert tts_stream.sentences == [answer]
    assert "audio" in ws.types()
    await session.close()


@pytest.mark.integration
async def test_voice_partial_stream_failure_does_not_replay_the_fragment(gpa_env):
    """After half a fragment is already audible, HTTP must not repeat it."""
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="半句失败验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    tts_http = FakeTTSHttp()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=tts_http,
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=lambda _settings: PartialFailTTSStream(),
    )
    await session.start()
    session._turn_id = "t1"
    answer = "这是一句会被中途打断的回答。"

    env.gpa.bus.publish(
        cid, {"type": "message.final", "turn_id": "t1", "payload": {"text": answer}}
    )
    env.gpa.bus.publish(
        cid, {"type": "turn.settled", "turn_id": "t1", "payload": {"status": "completed"}}
    )
    await _wait_for_messages(ws, "turn.metrics")

    assert "audio" in ws.types()
    assert answer not in tts_http.sentences
    await session.close()


@pytest.mark.integration
async def test_voice_delta_only_turn_speaks_once_on_settle(gpa_env):
    """A runtime that streams deltas but no final still speaks one utterance."""
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="delta 语音验收", channel="voice"
    )
    cid = conversation["conversation_id"]
    ws = FakeWebSocket()
    tts_stream = FakeTTSStream()
    session = GpaVoiceSession(
        websocket=ws,
        gpa=env.gpa,
        conversation=await env.store.get(cid, env.tenant, env.user),
        settings=env.settings,
        tts_http=FakeTTSHttp(),
        asr_factory=lambda _settings: FakeASR(),
        tts_stream_factory=lambda _settings: tts_stream,
    )
    await session.start()
    session._turn_id = "t1"

    env.gpa.bus.publish(
        cid,
        {
            "type": "message.delta",
            "turn_id": "t1",
            "payload": {"text": "第一句话。第二句话。"},
        },
    )
    await _wait_for_messages(ws, "text.delta")
    await asyncio.sleep(0.1)
    assert tts_stream.sentences == []

    env.gpa.bus.publish(
        cid, {"type": "turn.settled", "turn_id": "t1", "payload": {"status": "completed"}}
    )
    await _wait_for_messages(ws, "turn.metrics")
    assert tts_stream.sentences == ["第一句话。第二句话。"]
    await session.close()
