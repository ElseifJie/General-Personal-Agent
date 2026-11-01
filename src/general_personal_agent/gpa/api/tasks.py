"""Task detail and authenticated artifact proxy."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user, gpa_services, tenant_id
from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.schemas import ArtifactRef, TaskView

logger = get_logger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["gpa-tasks"])


@router.get("/{task_id}", response_model=TaskView)
async def get_gpa_task(
    task_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    task = await gpa_services(request).tasks.get(task_id, tenant_id(request), user.app_user_id)
    return TaskView(**task)


@router.post("/{task_id}/cancel", response_model=TaskView)
async def cancel_gpa_task(
    task_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    task = await gpa_services(request).tasks.cancel(
        task_id, tenant_id(request), user.app_user_id, "cancelled from workbench"
    )
    return TaskView(**task)


@router.get("/{task_id}/artifacts", response_model=list[ArtifactRef])
async def list_artifacts(
    task_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    task = await gpa_services(request).tasks.get(task_id, tenant_id(request), user.app_user_id)
    return [ArtifactRef(**item) for item in task["artifacts"]]


@router.get("/{task_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    return await _serve_artifact(request, task_id, artifact_id, user, head=False)


@router.head("/{task_id}/artifacts/{artifact_id}/download")
async def head_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    return await _serve_artifact(request, task_id, artifact_id, user, head=True)


async def _serve_artifact(request: Request, task_id: str, artifact_id: str, user: SessionUser, *, head: bool):
    gpa = gpa_services(request)
    if not await gpa.rate_limiter.allow(
        f"download:{user.app_user_id}",
        int(gpa.settings.gpa_rate_limit_downloads_per_min),
    ):
        await record_audit(
            gpa.task_store.db,
            tenant_id=tenant_id(request),
            app_user_id=user.app_user_id,
            action="artifact.download",
            resource_id=artifact_id,
            result="denied",
        )
        raise GpaError("RATE_LIMITED", "too many downloads; retry shortly", retryable=True)
    artifact = await gpa.tasks.store.get_artifact(artifact_id)
    if (
        artifact is None
        or artifact["task_id"] != task_id
        or artifact["tenant_id"] != tenant_id(request)
        or artifact["app_user_id"] != user.app_user_id
    ):
        raise GpaError("NOT_FOUND", "artifact not found")
    expires_at = artifact.get("expires_at")
    if expires_at is not None and expires_at.timestamp() <= datetime.now(UTC).timestamp():
        raise GpaError("ARTIFACT_UNAVAILABLE", "artifact has expired")
    storage_ref = artifact.get("storage_ref")
    if not storage_ref:
        raise GpaError("ARTIFACT_UNAVAILABLE", "artifact has no stored object")
    await record_audit(
        gpa.task_store.db,
        tenant_id=tenant_id(request),
        app_user_id=user.app_user_id,
        action="artifact.download",
        resource_id=artifact_id,
    )
    tos = request.app.state.services.tos
    total = artifact.get("bytes")
    checksum = artifact.get("checksum")
    etag = f'"{checksum}"' if checksum else f'"{artifact_id}"'
    last_modified = artifact["created_at"].strftime("%a, %d %b %Y %H:%M:%S GMT")
    if_range = request.headers.get("if-range")
    range_header = request.headers.get("range")
    if if_range and if_range not in {etag, last_modified}:
        range_header = None
    ranges = _parse_ranges(range_header, total)
    if ranges is False:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
        )
    base_headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.get('name') or artifact_id)}",
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }
    if not ranges:
        headers = {**base_headers}
        if total is not None:
            headers["Content-Length"] = str(total)
        if head:
            return Response(
                status_code=200,
                headers=headers,
                media_type=artifact.get("mime") or "application/octet-stream",
            )
        return StreamingResponse(
            _artifact_stream(tos, storage_ref, None, None, task_id, artifact_id),
            media_type=artifact.get("mime") or "application/octet-stream",
            headers=headers,
        )
    if len(ranges) == 1:
        start, end = ranges[0]
        headers = {
            **base_headers,
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        if head:
            return Response(
                status_code=206,
                headers=headers,
                media_type=artifact.get("mime") or "application/octet-stream",
            )
        return StreamingResponse(
            _artifact_stream(tos, storage_ref, start, end, task_id, artifact_id),
            status_code=206,
            media_type=artifact.get("mime") or "application/octet-stream",
            headers=headers,
        )
    boundary = f"gpa-{artifact_id[:16]}-{next(_BOUNDARY_COUNTER)}"
    headers = {
        **base_headers,
        "Content-Type": f"multipart/byteranges; boundary={boundary}",
    }
    if head:
        return Response(
            status_code=206,
            headers=headers,
            media_type=f"multipart/byteranges; boundary={boundary}",
        )
    return StreamingResponse(
        _multipart_stream(
            tos,
            storage_ref,
            ranges,
            total,
            artifact.get("mime") or "application/octet-stream",
            boundary,
            task_id,
            artifact_id,
        ),
        status_code=206,
        media_type=f"multipart/byteranges; boundary={boundary}",
        headers=headers,
    )


async def _artifact_stream(tos, storage_ref, start, end, task_id, artifact_id):
    try:
        if start is not None and end is not None:
            async for chunk in tos.iter_range(storage_ref, start, end):
                yield chunk
        else:
            async for chunk in tos.iter_bytes(storage_ref):
                yield chunk
    except Exception as exc:  # noqa: BLE001 - surface a clean proxy error
        logger.warning("artifact proxy failed task=%s artifact=%s: %s", task_id, artifact_id, exc)
        raise


async def _multipart_stream(tos, storage_ref, ranges, total, mime, boundary, task_id, artifact_id):
    for start, end in ranges:
        yield f"--{boundary}\r\nContent-Type: {mime}\r\nContent-Range: bytes {start}-{end}/{total}\r\n\r\n".encode()
        try:
            async for chunk in tos.iter_range(storage_ref, start, end):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact proxy failed task=%s artifact=%s: %s", task_id, artifact_id, exc)
            raise
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


def _parse_ranges(value: str | None, total: int | None) -> list[tuple[int, int]] | None | bool:
    """Return ranges, None for full body, or False for 416."""
    if not value:
        return None
    if total is None or total <= 0 or not value.startswith("bytes="):
        return False
    ranges: list[tuple[int, int]] = []
    for spec in value[6:].split(","):
        spec = spec.strip()
        if not spec or "-" not in spec:
            return False
        left, right = spec.split("-", 1)
        try:
            if left == "":
                suffix = int(right)
                if suffix <= 0:
                    return False
                ranges.append((max(0, total - suffix), total - 1))
                continue
            start = int(left)
            end = int(right) if right else total - 1
        except ValueError:
            return False
        if start < 0 or start >= total or end < start:
            return False
        ranges.append((start, min(end, total - 1)))
    return ranges or False


def _parse_range(value: str | None, total: int | None) -> tuple[int, int] | None | bool:
    ranges = _parse_ranges(value, total)
    if ranges is None or ranges is False:
        return ranges
    return ranges[0]


_BOUNDARY_COUNTER = iter(range(1, 10**9))
