"""VeADK runtime stream edge cases (empty/filtered model responses)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.agent.runtime import VeADKRuntime, _fallback_reply
from general_personal_agent.gpa.agent.transport import AgentSessionRequest, AgentTurnRequest

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FilteredRunner:
    """Runner double that returns a SAFETY-filtered response with no parts."""

    agent = SimpleNamespace(instruction="")

    async def run_async(self, **_kwargs):
        yield SimpleNamespace(
            content=None,
            error_code="SAFETY",
            usage_metadata=None,
            partial=False,
            turn_complete=True,
        )


def _runtime() -> VeADKRuntime:
    runtime = VeADKRuntime(
        SimpleNamespace(),
        AgentSessionRequest(
            conversation_id="c1", tenant_id="demo", app_user_id="u1", channel="text"
        ),
        None,
    )
    runtime._started = True
    runtime._runner = _FilteredRunner()
    return runtime


async def test_filtered_response_still_emits_a_user_visible_reply():
    runtime = _runtime()
    turn = AgentTurnRequest(turn_id="t1", conversation_id="c1", user_text="今天有哪些热点新闻")
    events = [event async for event in runtime.stream(turn)]

    finals = [event for event in events if event.type == "message.final"]
    assert len(finals) == 1
    assert "内容安全" in finals[0].payload["text"]
    assert runtime.last_metrics["error_code"] == "SAFETY"


async def test_empty_response_without_error_code_gets_a_generic_reply():
    class _EmptyRunner:
        agent = SimpleNamespace(instruction="")

        async def run_async(self, **_kwargs):
            yield SimpleNamespace(
                content=None,
                error_code=None,
                usage_metadata=None,
                partial=False,
                turn_complete=True,
            )

    runtime = _runtime()
    runtime._runner = _EmptyRunner()
    turn = AgentTurnRequest(turn_id="t1", conversation_id="c1", user_text="你好")
    events = [event async for event in runtime.stream(turn)]
    finals = [event for event in events if event.type == "message.final"]
    assert finals and finals[0].payload["text"] == _fallback_reply(None)


async def test_finish_reason_safety_without_error_code_also_gets_a_reply():
    class _Runner:
        agent = SimpleNamespace(instruction="")

        async def run_async(self, **_kwargs):
            yield SimpleNamespace(
                content=None,
                error_code=None,
                finish_reason="SAFETY",
                usage_metadata=None,
                partial=False,
                turn_complete=True,
            )

    runtime = _runtime()
    runtime._runner = _Runner()
    turn = AgentTurnRequest(turn_id="t1", conversation_id="c1", user_text="今天有哪些热点新闻")
    events = [event async for event in runtime.stream(turn)]
    finals = [event for event in events if event.type == "message.final"]
    assert finals and "内容安全" in finals[0].payload["text"]
    assert runtime.last_metrics["error_code"] == "SAFETY"
