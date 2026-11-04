"""SearchService resilience: credential refresh, retry, fallback, diagnostics.

Regression: the process held an expired STS credential snapshot, so
every Torchlight call failed with ``InvalidAccessKey`` while a fresh process
worked.  A single provider error must never end up as "联网搜索服务返回错误".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from general_personal_agent.clients.search import SearchResult, SearchService
from general_personal_agent.settings import Settings


class ScriptedProvider:
    """Replays a script of results/exceptions; the last item repeats."""

    def __init__(self, name: str, script: list):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def search(self, query: str, count: int = 5) -> SearchResult:
        self.calls += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return SearchResult(
            provider=self.name,
            query=query,
            summary=item.summary,
            hits=list(item.hits),
            error=item.error,
            kind=item.kind,
        )


def ok(provider: str = "p", summary: str = "深圳明天多云，24-31℃。") -> SearchResult:
    return SearchResult(provider=provider, query="q", summary=summary)


def provider_error(message: str) -> SearchResult:
    return SearchResult(provider="p", query="q", error=message)


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "search_provider": "primary",
        "search_count": 5,
        "search_timeout_s": 30.0,
        "search_cache_ttl_s": 300.0,
        "search_summary_max_chars": 1500,
        "search_fallback_providers": "fallback",
        "search_attempt_timeout_s": 5.0,
        "search_retry_attempts": 2,
        "search_retry_backoff_s": 0.01,
        "search_budget_s": 10.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FixtureService(SearchService):
    """Keeps injected providers even when credentials are reloaded."""

    def __init__(self, settings, providers, **kwargs):
        self._fixtures = providers
        super().__init__(settings, providers=providers, **kwargs)

    def _build_providers(self, settings):  # noqa: ARG002 - fixtures are scripted
        return self._fixtures


def _settings_snapshot(**overrides) -> Settings:
    values = {
        "search_provider": "primary",
        "search_count": 5,
        "search_timeout_s": 30.0,
        "search_cache_ttl_s": 300.0,
        "search_fallback_providers": "fallback",
        "search_attempt_timeout_s": 5.0,
        "search_retry_attempts": 2,
        "search_retry_backoff_s": 0.01,
        "search_budget_s": 10.0,
        "volcengine_access_key": "AKTP0Uold",
        "volcengine_secret_key": "old-secret",
        "volcengine_session_token": "old-token",
        "ark_api_key": "ark-old",
    }
    values.update(overrides)
    return Settings.model_construct(**values)


async def test_auth_error_falls_back_to_next_provider():
    primary = ScriptedProvider("primary", [provider_error("InvalidAccessKey: security token invalid")])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = SearchService(
        _settings(), providers={"primary": primary, "fallback": fallback}, settings_loader=None
    )

    result = await service.search("深圳明天天气")

    assert result["error"] == ""
    assert result["provider"] == "fallback"
    assert result["summary"] == "深圳明天多云，24-31℃。"
    assert [(a["provider"], a["kind"]) for a in result["attempts"]] == [
        ("primary", "auth"),
        ("fallback", "ok"),
    ]
    assert primary.calls == 1  # auth errors are not retried blindly
    assert service.state()["ok_at"]


async def test_transient_error_is_retried_on_the_same_provider():
    primary = ScriptedProvider("primary", [provider_error("timeout after 1s"), ok("primary")])
    service = SearchService(_settings(), providers={"primary": primary}, settings_loader=None)

    result = await service.search("深圳天气")

    assert result["provider"] == "primary"
    assert primary.calls == 2
    assert result["attempts"][0]["kind"] == "transient"


async def test_all_providers_failing_reports_every_attempt():
    primary = ScriptedProvider("primary", [provider_error("InvalidAccessKey")])
    fallback = ScriptedProvider("fallback", [provider_error("HTTP 503: upstream unavailable")])
    service = SearchService(
        _settings(), providers={"primary": primary, "fallback": fallback}, settings_loader=None
    )

    result = await service.search("深圳天气")

    assert result["error"]
    assert "primary" in result["error"] and "fallback" in result["error"]
    assert result["kind"] == "auth"
    # auth 不重试；transient 会重试一次
    assert [a["kind"] for a in result["attempts"]] == ["auth", "transient", "transient"]
    assert service.state()["failed_at"]
    assert service.state()["error"]


async def test_auth_error_reloads_credentials_and_retries_without_restart():
    old = _settings_snapshot()
    new = _settings_snapshot(volcengine_access_key="AKTP0Unew", volcengine_session_token="new-token")
    primary = ScriptedProvider("primary", [provider_error("InvalidSecretToken"), ok("primary")])
    service = FixtureService(old, {"primary": primary}, settings_loader=lambda: new)

    result = await service.search("深圳天气")

    assert result["provider"] == "primary"
    assert primary.calls == 2
    assert service.settings is new
    assert service.settings.volcengine_access_key == "AKTP0Unew"


async def test_credential_reload_only_happens_once():
    old = _settings_snapshot()
    loaded: list[int] = []

    def loader() -> Settings:
        loaded.append(1)
        return old  # nothing changed on disk

    primary = ScriptedProvider("primary", [provider_error("InvalidAccessKey")])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = FixtureService(old, {"primary": primary, "fallback": fallback}, settings_loader=loader)

    result = await service.search("深圳天气")

    assert result["provider"] == "fallback"
    assert len(loaded) == 1
    assert primary.calls == 1


async def test_hung_provider_times_out_and_falls_back():
    async def slow() -> SearchResult:
        await asyncio.sleep(0.5)
        return ok("primary")

    primary = ScriptedProvider("primary", [slow])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = SearchService(
        _settings(search_attempt_timeout_s=0.05),
        providers={"primary": primary, "fallback": fallback},
        settings_loader=None,
    )

    result = await service.search("深圳天气")

    assert result["provider"] == "fallback"
    assert result["attempts"][0]["error"].startswith("timeout after")


async def test_empty_result_falls_back():
    primary = ScriptedProvider("primary", [SearchResult(provider="primary", query="q")])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = SearchService(
        _settings(), providers={"primary": primary, "fallback": fallback}, settings_loader=None
    )

    result = await service.search("深圳天气")

    assert result["provider"] == "fallback"
    assert result["attempts"][0]["error"] == "empty result"


async def test_fallback_can_be_disabled():
    primary = ScriptedProvider("primary", [provider_error("InvalidAccessKey")])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = SearchService(
        _settings(search_fallback_providers=""),
        providers={"primary": primary, "fallback": fallback},
        settings_loader=None,
    )

    result = await service.search("深圳天气")

    assert result["error"]
    assert fallback.calls == 0


async def test_successful_results_are_cached_but_failures_are_not():
    primary = ScriptedProvider("primary", [ok("primary")])
    service = SearchService(_settings(), providers={"primary": primary}, settings_loader=None)

    await service.search("深圳天气")
    await service.search("深圳天气")

    assert primary.calls == 1

    failing = ScriptedProvider("primary", [provider_error("timeout")])
    service = SearchService(
        _settings(search_fallback_providers="", search_retry_attempts=1),
        providers={"primary": failing},
        settings_loader=None,
    )
    await service.search("深圳天气")
    await service.search("深圳天气")

    assert failing.calls == 2


async def test_credential_fingerprint_masks_secrets():
    service = FixtureService(_settings_snapshot(), {"primary": ScriptedProvider("primary", [ok()])})

    fingerprint = service.credential_fingerprint()

    assert fingerprint["volcengine_ak"].startswith("AKTP0U")
    assert "old-secret" not in str(fingerprint)
    assert fingerprint["volcengine_session_token"]


async def test_slow_primary_does_not_starve_the_fallback():
    """主 provider 卡到时间片用完时，兜底仍必须拿到自己的预算。"""

    async def slow() -> SearchResult:
        await asyncio.sleep(5)
        return ok("primary")

    primary = ScriptedProvider("primary", [slow])
    fallback = ScriptedProvider("fallback", [ok("fallback")])
    service = SearchService(
        _settings(search_budget_s=0.4, search_attempt_timeout_s=0.2),
        providers={"primary": primary, "fallback": fallback},
        settings_loader=None,
    )

    result = await service.search("深圳天气")

    assert result["provider"] == "fallback"
    assert result["attempts"][0]["error"].startswith("timeout after")
    assert primary.calls == 1
