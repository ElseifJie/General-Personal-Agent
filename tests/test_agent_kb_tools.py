"""Agent KB tool routing tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.agent.prompt import build_instruction
from general_personal_agent.gpa.agent.tools import build_kb_tools
from general_personal_agent.gpa.errors import GpaError

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECTION_URI = "viking://resources/deck.pdf/deck/chapter_1.md"


class FakeKB:
    def __init__(self):
        self.calls = []
        self.reads = []

    async def role_for(self, app_user_id, tenant_id):
        return "admin"

    async def promote(self, file_id, **kwargs):
        self.calls.append((file_id, kwargs))
        return {
            "doc": {"doc_id": "doc_1", "index_status": "pending", "scope": kwargs["scope"]},
            "dedupe": False,
        }

    async def scope_for_uri(self, *, tenant_id, app_user_id, uri):
        if "memories" in uri:
            raise GpaError("NOT_FOUND", "knowledge content not found")
        return "shared"

    async def read_uri(self, *, tenant_id, app_user_id, scope, uri, offset=0, limit=4000):
        self.reads.append((scope, uri, offset, limit))
        return {"uri": uri, "kind": "section", "contentStatus": "thin", "text": "标题"}


async def test_promote_to_kb_rejects_files_outside_current_turn():
    state = SimpleNamespace(tenant_id="demo", app_user_id="admin", attachment_ids=[])
    kb = FakeKB()
    promote, _read = build_kb_tools(state, kb)
    result = await promote("f_other")
    assert result["error"] == "NOT_ATTACHED"
    assert kb.calls == []


async def test_promote_to_kb_uses_current_turn_file_and_scope():
    state = SimpleNamespace(
        tenant_id="demo", app_user_id="admin", attachment_ids=["f_1"]
    )
    kb = FakeKB()
    promote, _read = build_kb_tools(state, kb)
    result = await promote("f_1", scope="shared")
    assert result["docId"] == "doc_1"
    assert kb.calls[0][0] == "f_1"
    assert kb.calls[0][1]["role"] == "admin"
    assert kb.calls[0][1]["scope"] == "shared"


async def test_read_knowledge_infers_scope_and_returns_body():
    state = SimpleNamespace(tenant_id="demo", app_user_id="admin", attachment_ids=[])
    kb = FakeKB()
    _promote, read = build_kb_tools(state, kb)

    result = await read(f"  {SECTION_URI}  ", offset=120, limit=99999)

    assert result["contentStatus"] == "thin"
    assert kb.reads == [("shared", SECTION_URI, 120, 8000)]


async def test_read_knowledge_returns_tool_errors_instead_of_raising():
    state = SimpleNamespace(tenant_id="demo", app_user_id="admin", attachment_ids=[])
    _promote, read = build_kb_tools(state, FakeKB())

    outside = await read("viking://user/admin/memories/profile/a.md")
    assert outside["error"] == "NOT_FOUND"

    empty = await read("   ")
    assert empty["error"] == "VALIDATION_ERROR"


async def test_kb_tools_expose_the_body_reader():
    tools = build_kb_tools(
        SimpleNamespace(tenant_id="demo", app_user_id="admin", attachment_ids=[]), FakeKB()
    )
    assert [tool.__name__ for tool in tools] == ["promote_to_kb", "read_knowledge"]


async def test_instruction_tells_the_model_snippets_are_not_body_text():
    tools = {"search_knowledge", "search_memory", "promote_to_kb", "read_knowledge"}
    instruction = build_instruction("2026-11-03（周二）", tool_names=tools)
    assert "`search_knowledge` 返回的 snippet 只是摘要" in instruction
    assert "contentStatus=thin" in instruction
    assert "不要说“知识库只索引到标题和作者”" in instruction

    # Without the tool the model must not be told to call it.
    without = build_instruction("2026-11-03（周二）", tool_names={"search_knowledge"})
    assert "read_knowledge" not in without
