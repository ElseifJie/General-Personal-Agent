"""Web search must be a real Agent tool, not an apology.

Regression: the Personal Agent used to get only task/context tools, so every
realtime question ("今天几月几号" / "深圳天气") ended in "我这边没有联网能力".
A later regression narrowed the rules to realtime data, so an unfamiliar
product name got "我没有可靠信息，不便编造介绍" instead of a search.
"""

from __future__ import annotations

from types import SimpleNamespace

from general_personal_agent.gpa.agent.prompt import build_instruction, today_label
from general_personal_agent.gpa.agent.runtime import build_runtime_factory
from general_personal_agent.gpa.agent.tools import build_search_tools
from general_personal_agent.gpa.agent.transport import AgentSessionRequest


class FakeSearch:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = (
            result
            if result is not None
            else {
                "provider": "fake",
                "query": "深圳天气",
                "summary": "深圳今天多云，25-29℃。",
                "sources": [{"title": "深圳天气", "url": "https://example.com/weather"}],
                "error": "",
            }
        )
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, provider=None, use_cache: bool = True, count=None):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.result


def _settings(**overrides):
    values = {
        "gpa_agent_mode": "veadk",
        "ark_api_key": "ark-test",
        "model_agent_name": "model-test",
        "gpa_context_tools_enabled": True,
        "search_summary_max_chars": 1500,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request() -> AgentSessionRequest:
    return AgentSessionRequest(conversation_id="c1", tenant_id="demo", app_user_id="u1", channel="text")


async def _tool_names(search) -> list[str]:
    factory = build_runtime_factory(_settings(), assembler=None, search=search)
    runtime = await factory(_request())
    return [getattr(tool, "__name__", "") for tool in runtime.tools]


def test_instruction_carries_date_and_forbids_no_internet_answer():
    today = today_label()
    instruction = build_instruction("2026-10-27（周二）")
    assert "2026-10-27（周二）" in instruction
    assert "web_search" in instruction
    assert "禁止回答“我没有联网能力/无法获取实时信息”" in instruction
    assert today  # today_label is renderable without arguments


async def test_runtime_registers_web_search_tool():
    names = await _tool_names(FakeSearch())
    assert "web_search" in names


async def test_runtime_without_search_has_no_web_search():
    names = await _tool_names(None)
    assert "web_search" not in names


async def test_search_tool_returns_summary_and_sources():
    search = FakeSearch()
    tool = build_search_tools(search)[0]

    result = await tool("深圳天气")

    assert search.queries == ["深圳天气"]
    assert result["summary"] == "深圳今天多云，25-29℃。"
    assert result["sources"] == [{"title": "深圳天气", "url": "https://example.com/weather"}]
    assert "无法联网" in result["instruction"]


async def test_search_tool_truncates_long_summary():
    long_text = "热" * 2000
    tool = build_search_tools(
        FakeSearch({"provider": "fake", "query": "q", "summary": long_text, "sources": []}),
        max_summary_chars=100,
    )[0]

    result = await tool("q")

    assert len(result["summary"]) == 101  # 100 chars + ellipsis
    assert result["summary"].endswith("…")


async def test_search_tool_reports_provider_error_to_model():
    tool = build_search_tools(
        FakeSearch({"provider": "fake", "query": "q", "summary": "", "sources": [], "error": "bad key"})
    )[0]

    result = await tool("q")

    assert result["error"] == "search_failed"
    assert "稍后再试" in result["message"]


async def test_search_tool_reports_exception_to_model():
    tool = build_search_tools(FakeSearch(error=RuntimeError("network down")))[0]

    result = await tool("q")

    assert result["error"] == "search_failed"
    assert "network down" not in result["message"]


async def test_search_tool_rejects_empty_query():
    tool = build_search_tools(FakeSearch())[0]

    result = await tool("   ")

    assert result["error"] == "empty_query"


async def test_search_tool_exposes_auth_failure_as_non_retryable():
    tool = build_search_tools(
        FakeSearch(
            {
                "provider": "veadk_web_search",
                "query": "q",
                "summary": "",
                "sources": [],
                "error": "InvalidAccessKey: security token invalid",
                "kind": "auth",
                "attempts": [
                    {
                        "provider": "veadk_web_search",
                        "kind": "auth",
                        "error": "InvalidAccessKey: security token invalid",
                    },
                    {"provider": "ark", "kind": "auth", "error": "HTTP 401: invalid api key"},
                ],
            }
        )
    )[0]

    result = await tool("q")

    assert result["error"] == "search_failed"
    assert result["reason"] == "auth"
    # 凭据失效重试无意义，模型不应该立刻再打两次
    assert result["retryable"] is False
    assert "InvalidAccessKey" in result["detail"]
    assert [row["name"] for row in result["providers"]] == ["veadk_web_search", "ark"]


async def test_search_tool_marks_transient_failure_retryable():
    tool = build_search_tools(
        FakeSearch(
            {
                "provider": "ark",
                "query": "q",
                "summary": "",
                "sources": [],
                "error": "timeout after 15.0s",
                "kind": "transient",
            }
        )
    )[0]

    result = await tool("q")

    assert result["reason"] == "transient"
    assert result["retryable"] is True


def test_instruction_requires_search_for_unknown_entities():
    """An unfamiliar product name needs a search, not a clarification request.

    Regression: the rules used to enumerate only realtime data (weather/news/
    prices), so "请给我介绍一下 X" ended in "我没有可靠信息，不便编造介绍".
    """
    instruction = build_instruction("2026-10-27（周二）")
    assert "产品、公司、组织、人物、术语、事件" in instruction
    assert "先调用 `web_search` 查证再回答" in instruction
    assert "不要直接让用户澄清" in instruction
    assert "我没有可靠信息，不便介绍" in instruction
    # The blanket "ask the user first" guardrail is now scoped to identity/intent.
    assert "不确定用户身份、权限或意图时先问清楚" in instruction
    assert "不确定时先问清楚，不要自行臆测用户身份或权限。" not in instruction


def test_instruction_without_search_does_not_order_a_missing_tool():
    """WEB_SEARCH_RULES must be gated on the tool actually being registered."""
    instruction = build_instruction(tool_names={"search_memory"})
    assert "web_search" not in instruction
    assert "不要直接让用户澄清" not in instruction


def test_voice_instruction_distinguishes_unknown_words_from_bad_audio():
    voice = build_instruction("2026-10-27（周二）", channel="voice")
    assert "只有本轮音频确实听不清" in voice
    assert "不等于听不清" in voice
    assert "应先查证再回答" in voice


def test_voice_addendum_does_not_name_a_tool_that_may_be_absent():
    # VOICE_INSTRUCTION is appended unconditionally, so it must never name a
    # tool: the same instruction can be built without web_search registered.
    voice = build_instruction(channel="voice", tool_names={"search_memory"})
    assert "不等于听不清" in voice
    assert "web_search" not in voice


def test_search_tool_description_covers_entity_lookup():
    doc = build_search_tools(FakeSearch())[0].__doc__ or ""
    assert "产品、公司、组织、人物、术语、事件" in doc
    assert "不认识" in doc
    assert "不要先让用户澄清" in doc
    # No regression to the old "latest info only" framing.
    assert "联网搜索最新信息" not in doc
    assert "天气、新闻、热搜、汇率、股价、比分、发布会等" not in doc
