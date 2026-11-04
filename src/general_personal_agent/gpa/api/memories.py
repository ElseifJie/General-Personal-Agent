"""Read-only OpenViking memory browser (filesystem-shaped).

Memory is not one directory in OpenViking: self memory lives in
``user/<user>/memories/<type>/<subject>/<doc>.md`` and peer memory in
``user/<user>/peers/<peer>/memories/...``.  This API exposes both, keeps the
memory type as structured data, and refuses to read outside those two roots.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user
from general_personal_agent.gpa.context.layout import (
    MEMORY_TYPE_LABELS,
    SCOPE_PEER_MEMORY,
    SCOPE_SELF_MEMORY,
    OvLayout,
    build_layout,
)
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.schemas import CamelModel
from general_personal_agent.gpa.util import to_iso


class MemoryNode(CamelModel):
    uri: str
    path: str
    name: str
    is_dir: bool = False
    type: str = "file"
    title: str = ""
    size: int | None = None
    updated_at: str | None = None
    snippet: str = ""
    scope: str = "self"
    memory_type_label: str = ""


class MemoryScope(CamelModel):
    key: str
    label: str
    uri: str


class MemoryList(CamelModel):
    items: list[MemoryNode]
    root_uri: str = ""
    scopes: list[MemoryScope] = []


class MemoryContent(CamelModel):
    uri: str
    content: str


router = APIRouter(prefix="/api/memories", tags=["gpa-memories"])
MAX_NODES = 500
MAX_DEPTH = 6


def _identity(request: Request, user: SessionUser):
    services = request.app.state.services
    if services.identity_service is None:
        raise GpaError("UPSTREAM_UNAVAILABLE", "memory service unavailable")
    return services.identity_service


async def _client(request: Request, user: SessionUser):
    services = request.app.state.services
    try:
        return await services.ov_client_for(user.app_user_id)
    except Exception as exc:  # noqa: BLE001
        raise GpaError("UPSTREAM_UNAVAILABLE", "memory identity unavailable") from exc


async def _layout(request: Request, user: SessionUser) -> OvLayout:
    services = request.app.state.services
    identity = await _identity(request, user).resolve(user.app_user_id)
    return build_layout(services.settings, identity)


def _name_of(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _scope_key(layout: OvLayout, uri: str) -> str:
    peer = layout.peer_of(uri)
    return f"peer:{peer}" if peer else "self"


def _path_of(layout: OvLayout, uri: str) -> str:
    """Path relative to the user root, e.g. ``/memories/preferences/user/x.md``."""
    return "/" + uri[len(layout.user_root) + 1 :]


def _type_of(layout: OvLayout, uri: str, *, is_dir: bool) -> str:
    if is_dir:
        return "dir"
    return layout.memory_type_of(uri) or "memory"


def _title_of(name: str, is_dir: bool) -> str:
    return name if is_dir else name.removesuffix(".md")


def _node(
    layout: OvLayout,
    uri: str,
    *,
    is_dir: bool,
    entry: dict[str, Any] | None = None,
    title: str | None = None,
    snippet: str = "",
) -> MemoryNode:
    name = _name_of(uri)
    memory_type = layout.memory_type_of(uri)
    return MemoryNode(
        uri=uri,
        path=_path_of(layout, uri),
        name=name,
        is_dir=is_dir,
        type=_type_of(layout, uri, is_dir=is_dir),
        title=title if title is not None else _title_of(name, is_dir),
        size=(entry or {}).get("size"),
        updated_at=to_iso((entry or {}).get("modTime")),
        snippet=snippet,
        scope=_scope_key(layout, uri),
        memory_type_label=MEMORY_TYPE_LABELS.get(memory_type or "", ""),
    )


async def _scopes(layout: OvLayout, client) -> list[MemoryScope]:
    scopes = [MemoryScope(key="self", label="本人记忆", uri=layout.memories)]
    try:
        peers = await client.ls(layout.peers)
    except Exception:  # noqa: BLE001 - peers/ is optional
        peers = []
    for entry in peers or []:
        uri = str(entry.get("uri") or "").rstrip("/")
        if not entry.get("isDir") or not uri:
            continue
        peer = uri.rsplit("/", 1)[-1]
        scopes.append(MemoryScope(key=f"peer:{peer}", label=f"对端 {peer}", uri=f"{uri}/memories"))
    return scopes


def _matches_scope(layout: OvLayout, uri: str, scope: str) -> bool:
    current = _scope_key(layout, uri)
    if scope == "all":
        return True
    if scope == "peer":
        return current != "self"
    return current == scope


@router.get("", response_model=MemoryList)
async def list_memories(
    request: Request,
    user: SessionUser = Depends(current_user),
    query: str = Query(default="", max_length=200),
    scope: str = Query(default="all", max_length=40),
):
    services = request.app.state.services
    layout = await _layout(request, user)
    client = await _client(request, user)
    scopes = await _scopes(layout, client)

    if query.strip():
        from general_personal_agent.gpa.context.retriever import GpaContextRetriever

        retriever = GpaContextRetriever(services.settings, services.identity_service)
        try:
            hits = await retriever.search_memory(
                query, user.app_user_id, services.settings.ov_tenant_id or "demo", limit=20
            )
        finally:
            await retriever.close()
        items: list[MemoryNode] = []
        seen: set[str] = set()
        for hit in hits:
            uri = str(hit.get("uri") or "")
            if not uri or uri in seen or not _matches_scope(layout, uri, scope):
                continue
            if layout.scope_of(uri) not in {SCOPE_SELF_MEMORY, SCOPE_PEER_MEMORY}:
                continue
            seen.add(uri)
            items.append(
                _node(
                    layout,
                    uri,
                    is_dir=False,
                    title=hit.get("title") or None,
                    snippet=hit.get("snippet") or "",
                )
            )
        return MemoryList(items=items, root_uri=layout.memories, scopes=scopes)

    roots: list[str] = []
    if scope == "all" or scope == "self":
        roots.append(layout.memories)
    if scope == "all" or scope == "peer" or scope.startswith("peer:"):
        roots.extend(
            item.uri
            for item in scopes
            if item.key != "self" and (scope == "all" or scope == "peer" or item.key == scope)
        )
    nodes: list[MemoryNode] = []
    queue: list[tuple[str, int]] = [(root, 0) for root in roots]
    while queue and len(nodes) < MAX_NODES:
        uri, depth = queue.pop(0)
        try:
            entries = await client.ls(uri)
        except Exception:  # noqa: BLE001
            continue
        for entry in entries:
            if len(nodes) >= MAX_NODES:
                break
            entry_uri = str(entry.get("uri") or "").rstrip("/")
            if not entry_uri or entry_uri == uri:
                continue
            if _name_of(entry_uri) in {".", ".."}:
                continue
            is_dir = bool(entry.get("isDir"))
            nodes.append(_node(layout, entry_uri, is_dir=is_dir, entry=entry))
            if is_dir and depth < MAX_DEPTH:
                queue.append((entry_uri, depth + 1))
    return MemoryList(items=nodes, root_uri=layout.memories, scopes=scopes)


@router.get("/content", response_model=MemoryContent)
async def read_memory(
    request: Request,
    user: SessionUser = Depends(current_user),
    uri: str = Query(min_length=1, max_length=500),
):
    layout = await _layout(request, user)
    if layout.scope_of(uri) not in {SCOPE_SELF_MEMORY, SCOPE_PEER_MEMORY}:
        raise GpaError("FORBIDDEN", "memory uri out of scope")
    client = await _client(request, user)
    try:
        content = await client.read(uri)
    except Exception as exc:  # noqa: BLE001
        raise GpaError("NOT_FOUND", "memory not found") from exc
    return MemoryContent(uri=uri, content=content)
