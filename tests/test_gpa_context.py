"""Context: OV retrieval slices, write-back outbox and Agent tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.context.assembler import ContextAssembler, render_context_message
from general_personal_agent.gpa.context.tools import build_context_tools
from general_personal_agent.gpa.context.writer import GpaContextWriter
from general_personal_agent.gpa.task.tools import TaskToolState

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeRetriever:
    async def retrieve(self, query, app_user_id, tenant_id, timeout_ms=500):
        return {
            "long_term_memory": [
                {
                    "text": "用户喜欢蓝色",
                    "memoryType": "preferences",
                    "memoryTypeLabel": "偏好",
                    "scope": "self",
                    "score": 0.9,
                    "uri": "viking://user/u/memories/preferences/user/color.md",
                }
            ],
            "personal_kb": [
                {
                    "uri": "viking://user/u/resources/uploads/b.md",
                    "title": "b",
                    "snippet": "品牌规范",
                    "scope": "personal",
                    "score": 0.8,
                }
            ],
            "shared_kb": [
                {
                    "uri": "viking://resources/admin/c.md",
                    "title": "c",
                    "snippet": "公司流程",
                    "scope": "shared",
                    "score": 0.7,
                }
            ],
            "skills": [
                {
                    "name": "weekly-report",
                    "uri": "viking://agent/skills/weekly-report",
                    "snippet": "生成周报",
                    "scope": "agent",
                    "score": 0.6,
                }
            ],
            "degraded": ["shared_unavailable"],
        }

    async def search_memory(self, query, app_user_id, tenant_id, limit=5):
        return [{"title": "a", "snippet": "喜欢蓝色", "uri": "viking://user/u/memories/a.md", "score": 0.9}]

    async def search_knowledge(self, query, app_user_id, tenant_id, scope="all"):
        return [{"title": "b", "snippet": "品牌规范", "uri": "viking://user/u/resources/b.md", "score": 0.8}]


async def test_assembler_includes_memory_and_kb(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="C2", channel="text"
    )
    cid = conversation["conversation_id"]
    assembler = ContextAssembler(env.settings, env.store, retriever=FakeRetriever())
    packet = await assembler.build(
        conversation_id=cid,
        turn_id="turn_c2",
        channel="text",
        user_text="帮我做一张海报",
        tenant_id=env.tenant,
        app_user_id=env.user,
        trace_id="trace_c2",
    )
    assert packet.long_term_memory[0]["memoryType"] == "preferences"
    assert packet.personal_kb[0]["snippet"] == "品牌规范"
    assert packet.shared_kb[0]["snippet"] == "公司流程"
    assert packet.skills[0]["name"] == "weekly-report"
    assert "shared_unavailable" in packet.degraded
    kinds = {source.kind for source in packet.sources}
    assert {"memory", "kb", "skill"} <= kinds

    rendered = render_context_message(packet)
    assert "[偏好] 用户喜欢蓝色" in rendered
    assert "[共享] c: 公司流程" in rendered
    assert "可用技能" in rendered
    assert "weekly-report" in rendered


async def test_context_agent_tools(gpa_env):
    state = TaskToolState(tenant_id=gpa_env.tenant, app_user_id=gpa_env.user, conversation_id="c_test")
    tools = {tool.__name__: tool for tool in build_context_tools(state, FakeRetriever())}
    assert (await tools["search_memory"]("偏好"))[0]["snippet"] == "喜欢蓝色"
    assert (await tools["search_knowledge"]("品牌"))[0]["snippet"] == "品牌规范"


class FakeIdentity:
    async def resolve(self, app_user_id, tenant_id=None):
        return SimpleNamespace(api_key="test", ov_peer="peer", ov_user="test_user")


class FakeOV:
    def __init__(self):
        self.sessions: list[str] = []
        self.closed = False

    async def create_session(self, peer):
        self.sessions.append(peer or "")
        return "ov_test_session"

    async def close(self):
        self.closed = True


async def test_writer_enqueues_messages_summary_and_session(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="写回", channel="text"
    )
    cid = conversation["conversation_id"]
    async with env.db.pool.acquire() as conn:
        seq = await env.store.next_seq(conn, cid, "turn")
        turn = await conn.fetchrow(
            """
            INSERT INTO conversation_turn
              (turn_id, conversation_id, seq, channel, user_text, assistant_text, status)
            VALUES ('turn_write', $1, $2, 'text', '你好', '你好，我是 GPA', 'completed')
            RETURNING *
            """,
            cid,
            seq,
        )
    fake_ov = FakeOV()
    settings = env.settings.model_copy(update={"memory_commit_every": 1})
    writer = GpaContextWriter(settings, env.db, env.store, FakeIdentity())

    async def fake_client(app_user_id, identity):
        return fake_ov

    writer._client = fake_client
    writer._clients[env.user] = fake_ov
    await writer._record(cid, turn["turn_id"])

    state = await env.store.get_ov_state(cid)
    assert state["ov_session_id"] == "ov_test_session"
    rows = await env.db.pool.fetch(
        "SELECT kind, payload, seq FROM context_outbox WHERE conversation_id = $1 ORDER BY seq",
        cid,
    )
    kinds = [row["kind"] for row in rows]
    assert kinds.count("ov.message") == 2
    assert "ov.commit" in kinds
    assert [row["payload"]["role"] for row in rows if row["kind"] == "ov.message"] == [
        "user",
        "assistant",
    ]
    assert state["ov_message_count"] == 0

    # Explicit save: queue another commit and reset the per-turn counter.
    await env.db.pool.execute("UPDATE conversation SET ov_message_count = 2 WHERE conversation_id = $1", cid)
    assert await writer.commit_now(cid) is True
    state = await env.store.get_ov_state(cid)
    assert state["ov_message_count"] == 0
    manual = await env.db.pool.fetch(
        "SELECT idempotency_key FROM context_outbox"
        " WHERE conversation_id = $1 AND kind = 'ov.commit' ORDER BY id DESC LIMIT 1",
        cid,
    )
    assert manual[0]["idempotency_key"].startswith("gpa.ov.commit:manual:")

    await writer.close()
    assert fake_ov.closed is True
