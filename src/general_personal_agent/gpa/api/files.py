"""GPA-native file/artifact listing for the workbench."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user, gpa_services, tenant_id
from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.schemas import CamelModel

logger = get_logger(__name__)


class FileArtifactView(CamelModel):
    artifact_id: str
    task_id: str
    conversation_id: str
    conversation_title: str = ""
    name: str
    mime: str | None = None
    bytes: int | None = None
    created_at: str
    download_url: str
    task_goal: str = ""


class FileArtifactList(CamelModel):
    items: list[FileArtifactView]


router = APIRouter(prefix="/api/files", tags=["gpa-files"])


@router.get("", response_model=FileArtifactList)
async def list_files(
    request: Request,
    user: SessionUser = Depends(current_user),
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
):
    rows = await gpa_services(request).task_store.db.pool.fetch(
        """
        SELECT a.artifact_id, a.task_id, a.name, a.mime, a.bytes, a.created_at,
               t.conversation_id, t.goal, COALESCE(c.title, '') AS conversation_title
          FROM task_artifact a
          JOIN task_run t USING (task_id)
          LEFT JOIN conversation c USING (conversation_id)
         WHERE t.tenant_id = $1 AND t.app_user_id = $2
           AND ($3 = '' OR a.name ILIKE '%' || $3 || '%')
         ORDER BY a.created_at DESC
         LIMIT $4
        """,
        tenant_id(request),
        user.app_user_id,
        query.strip(),
        limit,
    )
    return FileArtifactList(
        items=[
            FileArtifactView(
                artifact_id=row["artifact_id"],
                task_id=row["task_id"],
                conversation_id=row["conversation_id"],
                conversation_title=row["conversation_title"] or "",
                name=row["name"],
                mime=row["mime"],
                bytes=row["bytes"],
                created_at=row["created_at"].isoformat(),
                download_url=(f"/api/tasks/{row['task_id']}/artifacts/{row['artifact_id']}/download"),
                task_goal=row["goal"] or "",
            )
            for row in rows
        ]
    )


@router.delete("/{artifact_id}", status_code=204)
async def delete_file(
    artifact_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
) -> Response:
    """Remove one artifact from the user's files page.

    Only the GPA-owned copy is deleted: the object key must live under the
    configured project prefix and differ from the MA source key, mirroring the
    retention janitor. Metadata deletion is the user-visible contract, so a
    failed object cleanup is logged but does not keep the row in the list.
    """
    gpa = gpa_services(request)
    artifact = await gpa.tasks.store.get_artifact(artifact_id)
    if (
        artifact is None
        or artifact["tenant_id"] != tenant_id(request)
        or artifact["app_user_id"] != user.app_user_id
    ):
        raise GpaError("NOT_FOUND", "artifact not found")

    storage_ref = artifact.get("storage_ref") or ""
    source_ref = artifact.get("source_ref") or ""
    tos = request.app.state.services.tos
    prefix = f"{getattr(tos, 'prefix', '')}/"
    if (
        tos is not None
        and storage_ref
        and storage_ref != source_ref
        and prefix != "/"
        and storage_ref.startswith(prefix)
    ):
        try:
            await tos.delete(storage_ref)
        except Exception as exc:  # noqa: BLE001 - metadata delete still wins
            logger.warning("artifact object delete failed key=%s: %s", storage_ref, exc)

    await gpa.task_store.delete_artifact(artifact_id)
    await record_audit(
        gpa.task_store.db,
        tenant_id=tenant_id(request),
        app_user_id=user.app_user_id,
        action="artifact.delete",
        resource_id=artifact_id,
    )
    return Response(status_code=204)
