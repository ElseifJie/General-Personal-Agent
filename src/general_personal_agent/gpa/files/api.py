"""HTTP API for conversation files and knowledge bases."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user, gpa_services, tenant_id
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.rollout import files_rollout_allows
from general_personal_agent.gpa.files.schemas import (
    ConversationFileBind,
    ConversationFileList,
    ConversationFileView,
    FileObjectView,
    KbContent,
    KbDeletionList,
    KbEntryList,
    KbEntryView,
    PromotionResult,
)
from general_personal_agent.gpa.files.upload import spool_upload
from general_personal_agent.gpa.ids import new_file_job_id
from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)

files_router = APIRouter(prefix="/api/files", tags=["gpa-files-upload"])
kb_router = APIRouter(prefix="/api/kb", tags=["gpa-kb"])
conversation_files_router = APIRouter(prefix="/api/conversations", tags=["gpa-conversation-files"])


def _svc(request: Request):
    return gpa_services(request)


def _ensure_files(request: Request, user: SessionUser | None = None):
    gpa = _svc(request)
    if not getattr(gpa.settings, "gpa_files_enabled", True):
        raise GpaError("NOT_FOUND", "file upload is disabled")
    if user is not None and not files_rollout_allows(gpa.settings, user.app_user_id, user.role):
        raise GpaError("NOT_FOUND", "file upload is not enabled for this account")


def _ensure_kb(request: Request, user: SessionUser | None = None):
    _ensure_files(request, user)
    if not getattr(_svc(request).settings, "gpa_kb_enabled", True):
        raise GpaError("NOT_FOUND", "knowledge base is disabled")


def _file_view(row: dict) -> FileObjectView:
    return FileObjectView(
        file_id=row["file_id"],
        name=row["name"],
        mime=row.get("mime"),
        bytes=row.get("bytes"),
        status=row["status"],
        text_status=row.get("text_status"),
        text_chars=row.get("text_chars") or 0,
        text_truncated=bool(row.get("text_truncated")),
        page_count=row.get("page_count"),
        error=row.get("error"),
        created_at=row["created_at"],
        download_url=f"/api/files/{row['file_id']}/download",
    )


@files_router.post("", response_model=dict, status_code=201)
async def upload_file(
    request: Request,
    user: SessionUser = Depends(current_user),
    file: UploadFile = File(...),
    purpose: Literal["conversation", "private_kb", "shared_kb"] = Form(...),
    conversation_id: str | None = Form(default=None, alias="conversationId"),
    category_id: str | None = Form(default=None, alias="categoryId"),
    client_upload_id: str | None = Form(default=None, alias="clientUploadId"),
):
    _ensure_files(request, user)
    gpa = _svc(request)
    name = file.filename or "file"
    mime = file.content_type
    spooled = await spool_upload(file, gpa.files.max_bytes)
    try:
        if purpose == "conversation":
            result = await gpa.files.upload_conversation(
                tenant_id=tenant_id(request),
                app_user_id=user.app_user_id,
                conversation_id=conversation_id,
                name=name,
                mime=mime,
                path=spooled.path,
                size=spooled.size,
                sha256=spooled.sha256,
            )
            return {
                "fileId": result["file"]["file_id"],
                "conversationFileId": (result.get("conversation_file") or {}).get("conversation_file_id"),
                "name": result["file"]["name"],
                "mime": result["file"].get("mime"),
                "bytes": result["file"].get("bytes"),
                "status": result["file"]["status"],
            }
        _ensure_kb(request, user)
        scope = "private" if purpose == "private_kb" else "shared"
        result = await gpa.kb.upload(
            tenant_id=tenant_id(request), app_user_id=user.app_user_id, role=user.role,
            scope=scope, name=name, mime=mime, path=spooled.path,
            size=spooled.size, sha256=spooled.sha256, category_id=category_id,
        )
        return {
            "fileId": result["file"]["file_id"],
            "docId": result["doc"]["doc_id"],
            "name": result["file"]["name"],
            "mime": result["file"].get("mime"),
            "bytes": result["file"].get("bytes"),
            "status": result["doc"].get("index_status") or "pending",
            "dedupe": bool(result.get("dedupe")),
        }
    finally:
        spooled.cleanup()


@files_router.get("/{file_id}", response_model=FileObjectView)
async def get_file(file_id: str, request: Request, user: SessionUser = Depends(current_user)):
    _ensure_files(request, user)
    gpa = _svc(request)
    row = await gpa.files.store.get_file(file_id)
    if row is None or row["tenant_id"] != tenant_id(request) or row["app_user_id"] != user.app_user_id:
        raise GpaError("NOT_FOUND", "file not found")
    return _file_view(row)


@files_router.get("/{file_id}/download")
async def download_file(file_id: str, request: Request, user: SessionUser = Depends(current_user)):
    _ensure_files(request, user)
    gpa = _svc(request)
    row = await gpa.files.store.get_file(file_id)
    if row is None or row["tenant_id"] != tenant_id(request) or row["app_user_id"] != user.app_user_id:
        raise GpaError("NOT_FOUND", "file not found")
    if not row.get("storage_ref") or gpa.files.tos is None:
        raise GpaError("NOT_FOUND", "file content unavailable")
    data, content_type = await gpa.files.tos.get_bytes(row["storage_ref"])
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row['name'])}",
        "X-Content-Type-Options": "nosniff",
        "Content-Length": str(len(data)),
    }
    return Response(content=data, media_type=content_type or row.get("mime") or "application/octet-stream", headers=headers)


@files_router.post("/{file_id}/reparse", response_model=FileObjectView, status_code=202)
async def reparse_file(file_id: str, request: Request, user: SessionUser = Depends(current_user)):
    _ensure_files(request, user)
    gpa = _svc(request)
    row = await gpa.files.store.get_file(file_id)
    if row is None or row["tenant_id"] != tenant_id(request) or row["app_user_id"] != user.app_user_id:
        raise GpaError("NOT_FOUND", "file not found")
    await gpa.files.store.update_file(file_id, status="stored", text_status="pending", error=None)
    await gpa.file_jobs.enqueue(
        tenant_id=tenant_id(request), app_user_id=user.app_user_id, kind="extract",
        payload={"file_id": file_id}, idempotency_key=f"extract:{file_id}:{new_file_job_id()}",
    )
    fresh = await gpa.files.store.get_file(file_id) or row
    return _file_view(fresh)


@files_router.post("/{file_id}/promote", response_model=PromotionResult)
async def promote_file(
    file_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
    scope: Literal["private", "shared"] = Query(default="private"),
    category_id: str | None = Query(default=None, alias="categoryId"),
):
    _ensure_kb(request, user)
    result = await _svc(request).kb.promote(
        file_id, tenant_id=tenant_id(request), app_user_id=user.app_user_id,
        role=user.role, scope=scope, category_id=category_id,
    )
    return PromotionResult(doc_id=result["doc"]["doc_id"], dedupe=bool(result.get("dedupe")))


@files_router.delete("/objects/{file_id}", status_code=204)
async def delete_file_object(file_id: str, request: Request, user: SessionUser = Depends(current_user)):
    _ensure_files(request, user)
    gpa = _svc(request)
    row = await gpa.files.store.get_file(file_id)
    if row is None or row["tenant_id"] != tenant_id(request) or row["app_user_id"] != user.app_user_id:
        raise GpaError("NOT_FOUND", "file not found")
    await gpa.files.delete_object(file_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- conversation files
@conversation_files_router.post("/{conversation_id}/files", response_model=ConversationFileList)
async def bind_conversation_files(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
    body: ConversationFileBind | None = None,
    file_ids: list[str] = Query(default=[]),
):
    _ensure_files(request, user)
    bound = await _svc(request).files.bind_files(
        conversation_id=conversation_id, tenant_id=tenant_id(request),
        app_user_id=user.app_user_id, file_ids=(body.file_ids if body else file_ids),
    )
    return ConversationFileList(items=[ConversationFileView(**{
        "conversationFileId": row["conversation_file_id"], "fileId": row["file_id"],
        "name": row.get("name") or "", "mime": row.get("mime"), "bytes": row.get("bytes"),
        "status": row.get("status") or "active",
    }) for row in bound])


@conversation_files_router.get("/{conversation_id}/files", response_model=ConversationFileList)
async def list_conversation_files(
    conversation_id: str, request: Request, user: SessionUser = Depends(current_user)
):
    _ensure_files(request, user)
    rows = await _svc(request).files.list_conversation_files(
        conversation_id, tenant_id(request), user.app_user_id
    )
    return ConversationFileList(items=[ConversationFileView(**row) for row in rows])


@conversation_files_router.delete("/{conversation_id}/files/{conversation_file_id}", status_code=204)
async def remove_conversation_file(
    conversation_id: str, conversation_file_id: str, request: Request,
    user: SessionUser = Depends(current_user),
):
    _ensure_files(request, user)
    ok = await _svc(request).files.store.remove_conversation_file(
        conversation_file_id, conversation_id, tenant_id(request), user.app_user_id
    )
    if not ok:
        raise GpaError("NOT_FOUND", "conversation file not found")
    return Response(status_code=204)


# ---------------------------------------------------------------- knowledge base
@kb_router.get("/entries", response_model=KbEntryList)
async def list_kb_entries(
    request: Request,
    user: SessionUser = Depends(current_user),
    scope: Literal["private", "shared"] = Query(default="private"),
    path: str = Query(default=""),
    query: str = Query(default="", max_length=200),
):
    _ensure_kb(request, user)
    data = await _svc(request).kb.entries(
        tenant_id=tenant_id(request), app_user_id=user.app_user_id,
        scope=scope, path=path, query=query,
    )
    return KbEntryList(
        scope=data["scope"], root_uri=data["rootUri"],
        items=[KbEntryView(**item) for item in data["items"]],
        degraded=data.get("degraded") or [],
    )


@kb_router.get("/content", response_model=KbContent)
async def read_kb_content(
    request: Request,
    user: SessionUser = Depends(current_user),
    scope: Literal["private", "shared"] = Query(default="private"),
    uri: str = Query(...),
):
    _ensure_kb(request, user)
    text = await _svc(request).kb.content(
        tenant_id=tenant_id(request), app_user_id=user.app_user_id, scope=scope, uri=uri,
    )
    return KbContent(uri=uri, content=text[:400_000], truncated=len(text) > 400_000)


@kb_router.get("/documents/{doc_id}/download")
async def download_kb_document(
    doc_id: str, request: Request, user: SessionUser = Depends(current_user)
):
    _ensure_kb(request, user)
    data, content_type, name = await _svc(request).kb.download(
        doc_id, tenant_id=tenant_id(request), app_user_id=user.app_user_id, role=user.role
    )
    return Response(
        content=data, media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@kb_router.delete("/entries", status_code=202)
async def delete_kb_entry(
    request: Request,
    user: SessionUser = Depends(current_user),
    scope: Literal["private", "shared"] = Query(default="private"),
    uri: str = Query(...),
):
    _ensure_kb(request, user)
    gpa = _svc(request)
    doc = await gpa.files.store.get_kb_by_uri(tenant_id(request), uri)
    if doc is None or doc["scope"] != scope:
        raise GpaError("NOT_FOUND", "document not found")
    await gpa.kb.delete_document(
        doc["doc_id"], app_user_id=user.app_user_id, tenant_id=tenant_id(request), role=user.role
    )
    return {"docId": doc["doc_id"], "status": "deleting"}


@kb_router.post("/documents/{doc_id}/reindex", response_model=KbEntryView)
async def reindex_kb_document(
    doc_id: str, request: Request, user: SessionUser = Depends(current_user)
):
    _ensure_kb(request, user)
    doc = await _svc(request).kb.reindex(
        doc_id, tenant_id=tenant_id(request), app_user_id=user.app_user_id, role=user.role
    )
    return KbEntryView(
        uri=doc["target_uri"], path=doc["logical_path"], name=doc.get("title") or "",
        is_dir=False, size=None, updated_at=None, doc_id=doc["doc_id"],
        status=doc["index_status"], scope=doc["scope"], mime=None,
        download_url=f"/api/kb/documents/{doc['doc_id']}/download",
        error=doc.get("error"), actions=["retry", "delete"],
    )


@kb_router.post("/documents/{doc_id}/retry-delete", response_model=KbEntryView)
async def retry_delete_kb_document(
    doc_id: str, request: Request, user: SessionUser = Depends(current_user)
):
    _ensure_kb(request, user)
    doc = await _svc(request).kb.retry_delete(
        doc_id, tenant_id=tenant_id(request), app_user_id=user.app_user_id, role=user.role
    )
    return KbEntryView(
        uri=doc["target_uri"], path=doc["logical_path"], name=doc.get("title") or "",
        is_dir=False, size=None, updated_at=None, doc_id=doc["doc_id"],
        status=doc["index_status"], scope=doc["scope"], mime=None,
        download_url=None, error=doc.get("error"), actions=["retry", "delete"],
    )


@kb_router.get("/deletions", response_model=KbDeletionList)
async def list_kb_deletions(request: Request, user: SessionUser = Depends(current_user)):
    _ensure_kb(request, user)
    rows = await _svc(request).kb.deletions(
        tenant_id=tenant_id(request), app_user_id=user.app_user_id, role=user.role
    )
    return KbDeletionList(items=[KbEntryView(**row) for row in rows])
