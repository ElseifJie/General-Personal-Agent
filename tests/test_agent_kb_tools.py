"""Agent KB tool routing tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.agent.tools import build_kb_tools

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeKB:
    def __init__(self):
        self.calls = []

    async def role_for(self, app_user_id, tenant_id):
        return "admin"

    async def promote(self, file_id, **kwargs):
        self.calls.append((file_id, kwargs))
        return {
            "doc": {"doc_id": "doc_1", "index_status": "pending", "scope": kwargs["scope"]},
            "dedupe": False,
        }


async def test_promote_to_kb_rejects_files_outside_current_turn():
    state = SimpleNamespace(tenant_id="demo", app_user_id="admin", attachment_ids=[])
    kb = FakeKB()
    [promote] = build_kb_tools(state, kb)
    result = await promote("f_other")
    assert result["error"] == "NOT_ATTACHED"
    assert kb.calls == []


async def test_promote_to_kb_uses_current_turn_file_and_scope():
    state = SimpleNamespace(
        tenant_id="demo", app_user_id="admin", attachment_ids=["f_1"]
    )
    kb = FakeKB()
    [promote] = build_kb_tools(state, kb)
    result = await promote("f_1", scope="shared")
    assert result["docId"] == "doc_1"
    assert kb.calls[0][0] == "f_1"
    assert kb.calls[0][1]["role"] == "admin"
    assert kb.calls[0][1]["scope"] == "shared"
