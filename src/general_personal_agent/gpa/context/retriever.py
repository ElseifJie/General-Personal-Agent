"""C2 OpenViking retrieval over OV's native directory layout.

Every query is scoped to the directory that owns the concept instead of the
whole user tree: memory documents come from ``memories/`` (plus ``peers/``),
personal knowledge from ``resources/``, shared knowledge from
``viking://resources``, and capabilities from the two skill registries.  See
:mod:`general_personal_agent.gpa.context.layout` for the taxonomy.
"""

from __future__ import annotations

import asyncio
from typing import Any

from general_personal_agent.clients.ov import OpenVikingClient
from general_personal_agent.gpa.context.layout import (
    MEMORY_TYPE_LABELS,
    SCOPE_AGENT_SKILL,
    SCOPE_PEER_MEMORY,
    SCOPE_PERSONAL_KB,
    SCOPE_SHARED_KB,
    SCOPE_USER_SKILL,
    OvLayout,
    build_layout,
)
from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)

_GENERATED_SUFFIXES = (".abstract.md", ".overview.md")
_RESULT_BUCKETS = ("resources", "memories", "skills")


def _name(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1].replace(".md", "") or uri


def _subject(uri: str) -> str:
    """Map OV's generated ``.abstract.md`` / ``.overview.md`` back to the node.

    OpenViking indexes a document folder by its generated summary, so a query
    that matches a whole collection returns ``<dir>/.abstract.md`` rather than
    a leaf file.  The hit it describes *is* the directory, so normalize before
    classifying and use the summary as the snippet.
    """
    for suffix in _GENERATED_SUFFIXES:
        if uri.endswith(suffix):
            return uri[: -len(suffix)].rstrip("/")
    return uri


def _score(item: dict) -> float:
    try:
        return float(item.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _abstract(item: dict) -> str:
    return str(item.get("abstract") or item.get("text") or "")[:800]


def _collect(result: dict) -> list[dict]:
    """Merge every hit bucket OV returns (find uses one bucket per ctx type)."""
    items: list[dict] = []
    for bucket in _RESULT_BUCKETS:
        for item in result.get(bucket) or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def _normalized(item: dict) -> dict:
    return {**item, "uri": _subject(str(item.get("uri") or "").rstrip("/"))}


def _dedup(items: list[dict], limit: int) -> list[dict]:
    hits: list[dict] = []
    seen: set[str] = set()
    for item in items:
        uri = str(item.get("uri") or "").rstrip("/")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        hits.append({**item, "uri": uri})
    hits.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return hits[:limit]


def _memory_hits(result: dict, layout: OvLayout, limit: int) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for raw in _collect(result):
        item = _normalized(raw)
        uri = item["uri"]
        memory_type = layout.memory_type_of(uri)
        if not memory_type:
            continue
        scope = layout.scope_of(uri)
        hits.append(
            {
                "text": _abstract(item),
                "score": _score(item),
                "uri": uri,
                "memoryType": memory_type,
                "memoryTypeLabel": MEMORY_TYPE_LABELS.get(memory_type, memory_type),
                "scope": "peer" if scope == SCOPE_PEER_MEMORY else "self",
                "peer": layout.peer_of(uri),
                "title": _name(uri),
            }
        )
    return _dedup(hits, limit)


def _kb_hits(result: dict, layout: OvLayout, limit: int) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    scope_roots = {layout.resources, layout.shared_root}
    for raw in _collect(result):
        item = _normalized(raw)
        uri = item["uri"]
        scope = layout.scope_of(uri)
        if scope not in {SCOPE_PERSONAL_KB, SCOPE_SHARED_KB} or uri in scope_roots:
            continue
        hits.append(
            {
                "uri": uri,
                "title": _name(uri),
                "snippet": _abstract(item),
                "score": _score(item),
                "scope": "personal" if scope == SCOPE_PERSONAL_KB else "shared",
                "category": _category_of(uri, layout, scope),
            }
        )
    return _dedup(hits, limit)


def _skill_hits(result: dict, layout: OvLayout, limit: int) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    registries = set(layout.skill_roots())
    for raw in _collect(result):
        item = _normalized(raw)
        uri = item["uri"]
        scope = layout.scope_of(uri)
        if scope not in {SCOPE_AGENT_SKILL, SCOPE_USER_SKILL} or uri in registries:
            continue
        name = _name(uri)
        if name == "SKILL":
            name = uri.rsplit("/", 2)[-2]
        hits.append(
            {
                "name": name,
                "uri": uri,
                "snippet": _abstract(item)[:400],
                "score": _score(item),
                "scope": "agent" if scope == SCOPE_AGENT_SKILL else "user",
            }
        )
    return _dedup(hits, limit)


def _category_of(uri: str, layout: OvLayout, scope: str) -> str:
    """First directory under the KB scope; root-level files report ``root``."""
    base = layout.resources if scope == SCOPE_PERSONAL_KB else layout.shared_root
    rel = uri[len(base) :].strip("/")
    if "/" not in rel:
        return "root"
    return rel.split("/", 1)[0]


class GpaContextRetriever:
    def __init__(self, settings, identity_service):
        self.settings = settings
        self.identity_service = identity_service
        self.guard = None
        self._clients: dict[str, OpenVikingClient] = {}

    async def _client(self, app_user_id: str, identity) -> OpenVikingClient:
        client = self._clients.get(app_user_id)
        if client is None:
            client = OpenVikingClient(self.settings.ov_url, identity.api_key, self.settings.ov_timeout_s)
            self._clients[app_user_id] = client
        return client

    async def _resolve(self, app_user_id: str, tenant_id: str):
        identity = await self.identity_service.resolve(app_user_id, tenant_id)
        client = await self._client(app_user_id, identity)
        return identity, client, build_layout(self.settings, identity)

    async def retrieve(
        self, query: str, app_user_id: str, tenant_id: str, timeout_ms: int = 500
    ) -> dict[str, Any]:
        empty = {
            "long_term_memory": [],
            "personal_kb": [],
            "shared_kb": [],
            "skills": [],
            "degraded": [],
        }
        if not query.strip() or self.identity_service is None:
            return empty
        try:
            _, client, layout = await self._resolve(app_user_id, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("gpa context identity unavailable: %s", exc)
            return {**empty, "degraded": ["memory_identity_unavailable"]}

        per_task_timeout = max(0.3, timeout_ms / 1000)
        top_k = max(1, int(self.settings.ov_search_top_k))
        calls = {
            "memory": client.search(query, layout.memory_roots(), 5, ["memory"]),
            "personal": client.search(query, layout.personal_kb_roots(), top_k, ["resource"]),
            "shared": client.search(query, layout.shared_kb_roots(), top_k, ["resource"]),
        }
        if getattr(self.settings, "ov_skills_enabled", True):
            calls["skills"] = client.search(query, layout.skill_roots(), 3, ["skill"])
        tasks = {
            name: asyncio.create_task(asyncio.wait_for(call, per_task_timeout))
            for name, call in calls.items()
        }
        results: dict[str, Any] = {}
        degraded: list[str] = []
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception as exc:  # noqa: BLE001
                logger.debug("gpa context %s degraded: %s", name, exc)
                degraded.append("memory_unavailable" if name == "memory" else f"{name}_unavailable")
                task.cancel()
        personal = await self._filter_kb(
            _kb_hits(results.get("personal") or {}, layout, limit=3), tenant_id, "private"
        )
        shared = await self._filter_kb(
            _kb_hits(results.get("shared") or {}, layout, limit=3), tenant_id, "shared"
        )
        return {
            "long_term_memory": _memory_hits(results.get("memory") or {}, layout, limit=3),
            "personal_kb": personal,
            "shared_kb": shared,
            "skills": _skill_hits(results.get("skills") or {}, layout, limit=3),
            "degraded": degraded,
        }

    async def _filter_kb(self, hits: list[dict], tenant_id: str, scope: str) -> list[dict]:
        if self.guard is None or not hits:
            return hits
        allowed = await self.guard.filter_uris(tenant_id, scope, [hit["uri"] for hit in hits])
        return [hit for hit in hits if hit["uri"] in allowed]

    async def search_memory(
        self, query: str, app_user_id: str, tenant_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        try:
            _, client, layout = await self._resolve(app_user_id, tenant_id)
            result = await client.search(query, layout.memory_roots(), limit, ["memory"])
            return _memory_hits(result, layout, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("gpa memory tool degraded: %s", exc)
            return []

    async def search_knowledge(
        self, query: str, app_user_id: str, tenant_id: str, scope: str = "all"
    ) -> list[dict[str, Any]]:
        try:
            _, client, layout = await self._resolve(app_user_id, tenant_id)
            targets: list[str] = []
            if scope in {"all", "personal"}:
                targets.extend(layout.personal_kb_roots())
            # "admin" stays accepted for older callers/traces.
            if scope in {"all", "shared", "admin"}:
                targets.extend(layout.shared_kb_roots())
            if not targets:
                return []
            result = await client.search(query, targets, self.settings.ov_search_top_k, ["resource"])
            hits = _kb_hits(result, layout, limit=6)
            if self.guard is not None:
                allowed_personal = await self.guard.filter_uris(
                    tenant_id, "private", [h["uri"] for h in hits if h.get("scope") == "personal"]
                )
                allowed_shared = await self.guard.filter_uris(
                    tenant_id, "shared", [h["uri"] for h in hits if h.get("scope") == "shared"]
                )
                hits = [
                    h for h in hits
                    if (h.get("scope") == "personal" and h["uri"] in allowed_personal)
                    or (h.get("scope") == "shared" and h["uri"] in allowed_shared)
                ]
            return hits
        except Exception as exc:  # noqa: BLE001
            logger.debug("gpa knowledge tool degraded: %s", exc)
            return []

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
