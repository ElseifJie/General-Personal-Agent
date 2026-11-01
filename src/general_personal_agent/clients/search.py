"""Search providers: Ark Responses web_search and VeADK web_search (Torchlight).

Resilience notes (2026-10-29 incident)
-------------------------------------
``VOLCENGINE_*`` are short-lived STS credentials.  The process reads them once at
startup, so after an expiry/rotation every Torchlight call failed fast with
``InvalidAccessKey`` while a freshly started process searched fine — the
assistant then apologised to the user.  The service therefore now:

* reloads settings and retries once when a provider rejects the credentials;
* falls back to the next configured provider (Ark ``web_search`` by default)
  instead of surfacing a single provider's error;
* retries transient failures with a small backoff, inside a total time budget;
* records what failed (provider, kind, detail) for ``/health`` and the tool
  result, so an incident is diagnosable without reading process memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.settings import Settings

logger = get_logger(__name__)

# Provider payloads are plain dicts / JSON, so do it by text: the providers
# themselves return ``{"Error": {...}}`` blobs rather than typed exceptions.
AUTH_ERROR_MARKERS = (
    "invalidaccesskey",
    "invalidsecrettoken",
    "signaturedoesnotmatch",
    "expiredtoken",
    "invalidcredential",
    "accessdenied",
    "unauthorized",
    "http 401",
    "http 403",
    "100009",
    "100026",
)
TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporarily",
    "unavailable",
    "throttl",
    "ratelimit",
    "rate limit",
    "connection",
    "internalerror",
    "overloaded",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


class ProviderUnavailable(RuntimeError):
    pass


def classify_provider_error(text: str) -> tuple[str, bool]:
    """Return ``(kind, retryable)`` for a provider error string.

    ``kind`` is one of ``auth`` / ``transient`` / ``provider``.  Auth errors are
    never retried as-is: the credentials have to be reloaded first.
    """
    low = (text or "").lower()
    if any(marker in low for marker in AUTH_ERROR_MARKERS):
        return "auth", False
    if any(marker in low for marker in TRANSIENT_ERROR_MARKERS):
        return "transient", True
    return "provider", False


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fingerprint(value: str) -> str:
    """Masked, non-reversible identifier for a credential (which generation?)."""
    if not value:
        return ""
    return f"{value[:6]}…{hashlib.sha256(value.encode()).hexdigest()[:8]}"


@dataclass
class SearchHit:
    title: str = ""
    url: str = ""
    summary: str = ""


@dataclass
class SearchResult:
    provider: str
    query: str
    summary: str = ""
    hits: list[SearchHit] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    kind: str = ""
    attempts: list[dict] = field(default_factory=list)
    raw: Any = None

    @property
    def sources(self) -> list[dict]:
        return [{"title": h.title, "url": h.url} for h in self.hits if h.url]

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "query": self.query,
            "summary": self.summary,
            "sources": self.sources,
            "hits": [h.__dict__ for h in self.hits],
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "kind": self.kind,
            "attempts": [dict(a) for a in self.attempts],
        }


class ArkResponsesSearch:
    """Ark Responses API built-in ``web_search`` tool (model + citations)."""

    name = "ark"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = settings.search_timeout_s

    def available(self) -> tuple[bool, str]:
        return bool(self.settings.ark_api_key), "ARK_API_KEY"

    async def search(self, query: str, count: int = 5) -> SearchResult:
        body = {
            "model": self.settings.model_agent_name,
            "input": query,
            "instructions": (
                "你是联网搜索助手。用中文给出不超过 120 字的结论，"
                "并在最后列出 1-3 条来源链接。只输出结论和来源，不要过程。"
            ),
            "tools": [{"type": "web_search"}],
            "thinking": {"type": "disabled"},
            "max_output_tokens": 400,
        }
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.settings.ark_base_url.rstrip('/')}/responses",
                headers={"Authorization": f"Bearer {self.settings.ark_api_key}"},
                json=body,
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            return SearchResult(
                self.name, query, elapsed_ms=elapsed, error=f"HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        text, hits = "", []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") != "output_text":
                    continue
                text = content.get("text", "")
                for ann in content.get("annotations", []):
                    if ann.get("url"):
                        hits.append(
                            SearchHit(
                                title=ann.get("title", ""),
                                url=ann["url"],
                                summary=ann.get("summary", ""),
                            )
                        )
        return SearchResult(
            provider=self.name,
            query=query,
            summary=text.strip(),
            hits=hits[:count],
            elapsed_ms=elapsed,
            raw=data,
        )


class VeADKWebSearch:
    """VeADK ``web_search`` tool: Volcengine Torchlight WebSearch."""

    name = "veadk_web_search"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = settings.search_timeout_s

    def available(self) -> tuple[bool, str]:
        if self.settings.volcengine_access_key and self.settings.volcengine_secret_key:
            return True, "VOLCENGINE_ACCESS_KEY/SECRET_KEY"
        return False, "set VOLCENGINE_ACCESS_KEY/SECRET_KEY"

    async def _volcengine(self, query: str, count: int) -> SearchResult:
        from veadk.utils.volcengine_sign import ve_request

        ak = self.settings.volcengine_access_key
        sk = self.settings.volcengine_secret_key
        token = self.settings.volcengine_session_token
        started = time.perf_counter()
        response = await asyncio.to_thread(
            ve_request,
            request_body={
                "Query": query,
                "SearchType": "web",
                "Count": count,
                "NeedSummary": True,
            },
            action="WebSearch",
            ak=ak,
            sk=sk,
            service="volc_torchlight_api",
            version="2025-01-01",
            region="cn-beijing",
            host="mercury.volcengineapi.com",
            header={"X-Security-Token": token} if token else {},
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        error = (response.get("ResponseMetadata") or {}).get("Error") if isinstance(response, dict) else None
        if error:
            return SearchResult(self.name, query, elapsed_ms=elapsed, error=str(error), raw=response)
        results = ((response or {}).get("Result") or {}).get("WebResults") or []
        hits = [
            SearchHit(
                title=item.get("Title", ""),
                url=item.get("Url", ""),
                summary=item.get("Summary", "") or item.get("Snippet", ""),
            )
            for item in results
        ]
        summary = "\n".join(h.summary for h in hits if h.summary)
        return SearchResult(
            provider=self.name,
            query=query,
            summary=summary,
            hits=hits,
            elapsed_ms=elapsed,
            raw=response,
        )

    async def search(self, query: str, count: int = 5) -> SearchResult:
        return await self._volcengine(query, count)


PROVIDERS = {
    ArkResponsesSearch.name: ArkResponsesSearch,
    VeADKWebSearch.name: VeADKWebSearch,
}


class SearchService:
    """Facade used by the app; keeps the historical ``search() -> dict`` shape."""

    def __init__(
        self,
        settings: Settings,
        *,
        providers: dict[str, Any] | None = None,
        settings_loader: Callable[[], Any] | None = Settings,
    ):
        self.settings = settings
        self._settings_loader = settings_loader
        self.providers = providers if providers is not None else self._build_providers(settings)
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self.ttl = float(getattr(settings, "search_cache_ttl_s", 300.0))
        self._last: dict[str, Any] = {
            "provider": "",
            "error": "",
            "kind": "",
            "attempts": [],
            "failed_at": None,
            "ok_at": None,
        }

    # ------------------------------------------------------------------ helpers
    def _build_providers(self, settings: Any) -> dict[str, Any]:
        return {name: cls(settings) for name, cls in PROVIDERS.items()}

    def _reload_credentials(self) -> bool:
        """Re-read settings so rotated STS credentials take effect without a restart.

        Only meaningful when the service owns a real ``Settings`` snapshot: tests
        pass lightweight namespaces and injected providers, which must survive.
        """
        if self._settings_loader is None or not isinstance(self.settings, Settings):
            return False
        try:
            fresh = self._settings_loader()
        except Exception as exc:  # noqa: BLE001 - keep serving with the old snapshot
            logger.warning("search credential reload failed: %s", exc)
            return False
        if not isinstance(fresh, Settings):
            return False
        before = (
            self.settings.volcengine_access_key,
            self.settings.volcengine_secret_key,
            self.settings.volcengine_session_token,
            self.settings.ark_api_key,
        )
        after = (
            fresh.volcengine_access_key,
            fresh.volcengine_secret_key,
            fresh.volcengine_session_token,
            fresh.ark_api_key,
        )
        self.settings = fresh
        self.ttl = float(getattr(fresh, "search_cache_ttl_s", self.ttl))
        self.providers = self._build_providers(fresh)
        return before != after

    def _chain(self, primary: str) -> list[str]:
        chain = [primary]
        raw = str(getattr(self.settings, "search_fallback_providers", "") or "")
        for name in (part.strip() for part in raw.split(",")):
            if not name or name in chain or name not in self.providers:
                continue
            try:
                ok, _ = self.providers[name].available()
            except Exception:  # noqa: BLE001 - a broken fallback must not break search
                ok = False
            if ok:
                chain.append(name)
        return chain

    def _cache_get(self, key: tuple[str, str], now: float) -> dict | None:
        hit = self._cache.get(key)
        if hit is None or now - hit[0] >= self.ttl:
            return None
        return self._copy(hit[1])

    @staticmethod
    def _copy(payload: dict) -> dict:
        copied = dict(payload)
        copied["hits"] = [dict(h) for h in payload.get("hits", [])]
        copied["sources"] = [dict(s) for s in payload.get("sources", [])]
        copied["attempts"] = [dict(a) for a in payload.get("attempts", [])]
        return copied

    def _record_failure(self, provider: str, error: str, kind: str, attempts: list[dict]) -> None:
        self._last = {
            "provider": provider,
            "error": error,
            "kind": kind,
            "attempts": [dict(a) for a in attempts],
            "failed_at": _now_iso(),
            "ok_at": self._last.get("ok_at"),
        }

    def _record_success(self, provider: str, attempts: list[dict]) -> None:
        self._last = {
            "provider": provider,
            "error": "",
            "kind": "",
            "attempts": [dict(a) for a in attempts],
            "failed_at": self._last.get("failed_at"),
            "ok_at": _now_iso(),
        }

    # ------------------------------------------------------------------- public
    def provider_list(self) -> list[dict]:
        rows = []
        for name, provider in self.providers.items():
            ok, hint = provider.available()
            rows.append({"name": name, "available": ok, "hint": hint})
        return rows

    def state(self) -> dict:
        """Last search outcome, for ``/health`` (diagnostics after an incident)."""
        return {**self._last, "attempts": [dict(a) for a in self._last.get("attempts", [])]}

    def credential_fingerprint(self) -> dict:
        """Which credential generation this process holds (never the secret itself)."""
        return {
            "volcengine_ak": _fingerprint(getattr(self.settings, "volcengine_access_key", "")),
            "volcengine_session_token": _fingerprint(getattr(self.settings, "volcengine_session_token", "")),
            "ark_api_key": _fingerprint(getattr(self.settings, "ark_api_key", "")),
        }

    async def _run_attempt(
        self, provider: Any, query: str, count: int, timeout: float
    ) -> tuple[SearchResult | None, str]:
        try:
            result = await asyncio.wait_for(provider.search(query, count), timeout=timeout)
        except TimeoutError:
            return None, f"timeout after {timeout:.1f}s"
        except Exception as exc:  # noqa: BLE001 - tool errors must reach the model
            return None, f"{type(exc).__name__}: {exc}"
        if result.error:
            return None, str(result.error)
        if not result.summary and not result.hits:
            return None, "empty result"
        return result, ""

    async def search(
        self,
        query: str,
        provider: str | None = None,
        use_cache: bool = True,
        count: int | None = None,
    ) -> dict:
        primary = provider or self.settings.search_provider
        if primary not in self.providers:
            raise ProviderUnavailable(f"unknown search provider: {primary}")

        key = (primary, query)
        now = time.monotonic()
        if use_cache:
            cached = self._cache_get(key, now)
            if cached is not None:
                return cached

        count = count or int(getattr(self.settings, "search_count", 5))
        attempt_timeout = float(getattr(self.settings, "search_attempt_timeout_s", 15.0))
        max_attempts = max(1, int(getattr(self.settings, "search_retry_attempts", 2)))
        backoff = max(0.0, float(getattr(self.settings, "search_retry_backoff_s", 0.4)))
        budget = float(getattr(self.settings, "search_budget_s", 30.0))
        started = time.monotonic()
        deadline = started + budget

        attempts: list[dict] = []
        auth_kind = ""
        chain = self._chain(primary)
        # 每个 provider 一份时间片：主 provider 卡死/重试不能把兜底 provider 的预算吃光。
        slice_s = budget / max(1, len(chain))
        for name in chain:
            attempts_left = max_attempts
            credentials_reloaded = False
            provider_deadline = min(deadline, time.monotonic() + slice_s)
            while attempts_left > 0:
                remaining = min(provider_deadline, deadline) - time.monotonic()
                if remaining <= 0:
                    attempts.append(
                        {
                            "provider": name,
                            "kind": "budget",
                            "error": "search budget exhausted",
                            "elapsed_ms": 0,
                        }
                    )
                    break
                provider_obj = self.providers.get(name)
                if provider_obj is None:
                    break
                attempt_started = time.monotonic()
                result, error = await self._run_attempt(
                    provider_obj, query, count, min(attempt_timeout, remaining)
                )
                elapsed = int((time.monotonic() - attempt_started) * 1000)
                if result is not None:
                    attempts.append({"provider": name, "kind": "ok", "error": "", "elapsed_ms": elapsed})
                    payload = result.as_dict()
                    payload["provider"] = name
                    payload["attempts"] = [dict(a) for a in attempts]
                    self._record_success(name, attempts)
                    self._cache[key] = (time.monotonic(), payload)
                    return self._copy(payload)

                kind, retryable = classify_provider_error(error)
                auth_kind = auth_kind or (kind if kind == "auth" else "")
                attempts.append({"provider": name, "kind": kind, "error": error[:300], "elapsed_ms": elapsed})
                logger.warning(
                    "web_search provider %s failed (%s, %sms): %s", name, kind, elapsed, error[:500]
                )

                if kind == "auth" and not credentials_reloaded:
                    credentials_reloaded = True
                    if self._reload_credentials():
                        logger.warning("web_search reloaded credentials after auth error; retrying %s", name)
                        continue  # free retry with the refreshed snapshot
                    break  # nothing to reload: let the fallback provider try

                attempts_left -= 1
                if not retryable or attempts_left <= 0:
                    break
                pause = backoff * (max_attempts - attempts_left)
                if time.monotonic() + pause >= min(provider_deadline, deadline):
                    break
                await asyncio.sleep(pause)

        total_ms = int((time.monotonic() - started) * 1000)
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "no provider attempted"
        kind = auth_kind or (attempts[-1]["kind"] if attempts else "provider")
        self._record_failure(primary, detail, kind, attempts)
        logger.error("web_search failed for %r after %sms (%s): %s", query, total_ms, kind, detail[:800])
        return SearchResult(
            provider=primary,
            query=query,
            elapsed_ms=total_ms,
            error=detail,
            kind=kind,
            attempts=attempts,
        ).as_dict()

    async def compare(self, query: str, providers: list[str] | None = None) -> list[dict]:
        names = providers or [n for n, p in self.providers.items() if p.available()[0]]
        results = []
        for name in names:
            try:
                result = await self.providers[name].search(query, self.settings.search_count)
                results.append(result.as_dict())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "provider": name,
                        "query": query,
                        "summary": "",
                        "sources": [],
                        "hits": [],
                        "error": str(exc)[:300],
                    }
                )
        return results
