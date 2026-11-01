"""Read-only OpenViking context browser.

Unlike the old memory-only browser this lists everything the signed-in
account's OpenViking key can see: the shared ``agent``/``resources`` scopes and
the whole personal ``user/<id>`` scope, not just ``memories``. Access control
stays in OpenViking — the API never widens what the key is allowed to read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user, tenant_id
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.schemas import CamelModel

CONTEXT_ROOT = "viking://"
MAX_NODES = 2000
TREE_LEVELS = 12
MAX_CONTENT_CHARS = 400_000


class ContextNode(CamelModel):
    uri: str
    path: str
    name: str
    is_dir: bool = False
    size: int | None = None
    updated_at: str | None = None


class ContextTree(CamelModel):
    root_uri: str = CONTEXT_ROOT
    user_uri: str = ""
    items: list[ContextNode]
    truncated: bool = False


class ContextContent(CamelModel):
    uri: str
    content: str
    truncated: bool = False


router = APIRouter(prefix="/api/context", tags=["gpa-context"])


async def _client(request: Request, user: SessionUser):
    services = request.app.state.services
    try:
        return await services.ov_client_for(user.app_user_id)
    except Exception as exc:  # noqa: BLE001
        raise GpaError("UPSTREAM_UNAVAILABLE", "context identity unavailable", retryable=True) from exc


async def _user_uri(request: Request, user: SessionUser) -> str:
    services = request.app.state.services
    if services.identity_service is None:
        raise GpaError("UPSTREAM_UNAVAILABLE", "context service unavailable", retryable=True)
    try:
        identity = await services.identity_service.resolve(user.app_user_id)
    except Exception as exc:  # noqa: BLE001
        raise GpaError("UPSTREAM_UNAVAILABLE", "context identity unavailable", retryable=True) from exc
    return f"viking://user/{identity.ov_user}"


def _validate_uri(uri: str) -> str:
    value = uri.strip()
    if not value.startswith(CONTEXT_ROOT) or len(value) > 500 or ".." in value.split("/"):
        raise GpaError("VALIDATION_ERROR", "invalid context uri")
    return value.rstrip("/") or CONTEXT_ROOT


def _ov_error(exc: Exception, fallback: str) -> GpaError:
    status = getattr(exc, "status", None)
    message = getattr(exc, "message", "") or str(exc)
    if status in (401, 403):
        return GpaError("FORBIDDEN", "没有读取该上下文的权限")
    if status == 404:
        return GpaError("NOT_FOUND", "上下文不存在")
    if status == 400 and "Directory URI" in message:
        return GpaError("VALIDATION_ERROR", "目录不能按文件读取")
    return GpaError("UPSTREAM_UNAVAILABLE", fallback, retryable=True)


async def _guard_allows(
    request: Request,
    user_uri: str,
    uri: str,
    *,
    allow_invalidated_dirs: bool = False,
) -> bool:
    gpa = getattr(request.app.state, "gpa", None)
    guard = getattr(getattr(gpa, "kb", None), "guard", None)
    if guard is None:
        return True
    if uri == "viking://resources" or uri.startswith("viking://resources/"):
        scope = "shared"
    elif uri == user_uri or uri.startswith(user_uri.rstrip("/") + "/resources"):
        scope = "private"
    else:
        return True
    return await guard.is_allowed(
        tenant_id(request), scope, uri, allow_invalidated_dirs=allow_invalidated_dirs
    )


def _node(entry: dict[str, Any]) -> ContextNode:
    uri = str(entry.get("uri") or "").rstrip("/")
    rel = str(entry.get("rel_path") or "")
    if not rel and uri.startswith(CONTEXT_ROOT):
        rel = uri[len(CONTEXT_ROOT) :]
    name = str(entry.get("name") or rel.rsplit("/", 1)[-1] or uri)
    size = entry.get("size")
    return ContextNode(
        uri=uri,
        path=rel,
        name=name,
        is_dir=bool(entry.get("isDir")),
        size=size if isinstance(size, int) else None,
        updated_at=_iso(entry.get("modTime")),
    )


@router.get("/tree", response_model=ContextTree)
async def read_tree(
    request: Request,
    user: SessionUser = Depends(current_user),
):
    user_uri = await _user_uri(request, user)
    client = await _client(request, user)
    try:
        entries = await client.tree(CONTEXT_ROOT, level_limit=TREE_LEVELS, node_limit=MAX_NODES)
    except Exception as exc:  # noqa: BLE001
        raise _ov_error(exc, "上下文目录读取失败") from exc

    items = [_node(entry) for entry in entries if str(entry.get("uri") or "").startswith(CONTEXT_ROOT)]
    filtered: list[ContextNode] = []
    for item in items:
        if await _guard_allows(request, user_uri, item.uri, allow_invalidated_dirs=True):
            filtered.append(item)
    items = filtered
    items.sort(key=lambda item: item.uri)
    return ContextTree(
        root_uri=CONTEXT_ROOT,
        user_uri=user_uri,
        items=items,
        truncated=len(items) >= MAX_NODES,
    )


@router.get("/content", response_model=ContextContent)
async def read_content(
    request: Request,
    user: SessionUser = Depends(current_user),
    uri: str = Query(min_length=1, max_length=500),
):
    target = _validate_uri(uri)
    if not await _guard_allows(request, await _user_uri(request, user), target):
        raise GpaError("NOT_FOUND", "上下文不存在")
    client = await _client(request, user)
    try:
        content = await client.read(target)
    except Exception as exc:  # noqa: BLE001
        raise _ov_error(exc, "上下文文件读取失败") from exc
    truncated = len(content) > MAX_CONTENT_CHARS
    return ContextContent(
        uri=target,
        content=content[:MAX_CONTENT_CHARS],
        truncated=truncated,
    )


def _iso(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return None
