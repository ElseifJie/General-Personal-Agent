"""Upload, attachment and GC services for the file subsystem."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.extract import safe_name, sha256_bytes
from general_personal_agent.gpa.files.store import FileStore, FileUnavailableError
from general_personal_agent.gpa.ids import new_attachment_id, new_file_id

logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {
    ".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".xlsx", ".csv",
    ".json", ".png", ".jpg", ".jpeg",
}
BLOCKED_SUFFIXES = {".exe", ".dll", ".bat", ".cmd", ".sh", ".zip", ".rar", ".7z",
                    ".docm", ".xlsm", ".js", ".msi"}


def _suffix(name: str) -> str:
    lower = (name or "").lower()
    return "." + lower.rsplit(".", 1)[-1] if "." in lower else ""


def _sha256_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


class FileService:
    def __init__(self, settings, db, store: FileStore, tos, conversations=None, jobs=None):
        self.settings = settings
        self.db = db
        self.store = store
        self.tos = tos
        self.conversations = conversations
        self.jobs = jobs

    @staticmethod
    def sha256_path(path: str) -> str:
        return _sha256_path(path)

    @property
    def max_bytes(self) -> int:
        return max(1, int(getattr(self.settings, "gpa_file_max_mb", 50))) * 1024 * 1024

    def _validate(self, name: str, mime: str | None, size: int) -> None:
        if size <= 0:
            raise GpaError("VALIDATION_ERROR", "empty file")
        if size > self.max_bytes:
            raise GpaError("VALIDATION_ERROR", f"file too large (max {self.max_bytes // 1024 // 1024}MB)")
        suffix = _suffix(name)
        if not suffix:
            raise GpaError("VALIDATION_ERROR", "file type is required")
        if suffix in BLOCKED_SUFFIXES:
            raise GpaError("VALIDATION_ERROR", f"unsupported file type: {suffix}")
        if suffix and suffix not in ALLOWED_SUFFIXES:
            raise GpaError("VALIDATION_ERROR", f"unsupported file type: {suffix}")

    async def store_file(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        name: str,
        mime: str | None,
        data: bytes | None = None,
        path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        purpose: str,
    ) -> tuple[dict, bool]:
        if data is None and path is None:
            raise GpaError("VALIDATION_ERROR", "empty upload")
        if size is None:
            size = len(data) if data is not None else Path(path).stat().st_size  # type: ignore[arg-type]
        self._validate(name, mime, size)
        digest = sha256 or (sha256_bytes(data) if data is not None else _sha256_path(str(path)))
        existing = await self.store.find_by_hash(tenant_id, app_user_id, digest)
        if existing is not None:
            state = existing.get("gc_state")
            if state == "candidate":
                revived = await self.store.revive_file(existing["file_id"])
                if revived is not None:
                    return revived, False
            elif state not in {"deleting", "deleted"}:
                return existing, False
        safe = safe_name(name)
        file_id = new_file_id()
        storage_ref = None
        if self.tos is not None:
            key = f"uploads/{tenant_id}/{app_user_id}/{file_id}/original{_suffix(safe)}"
            object_key = getattr(self.tos, "object_key", None)
            if callable(object_key):
                key = object_key("uploads", tenant_id, app_user_id, file_id, f"original{_suffix(safe)}")
            try:
                put_file = getattr(self.tos, "put_file", None)
                if path is not None and callable(put_file):
                    storage_ref = await put_file(
                        key, path, mime or "application/octet-stream"
                    )
                else:
                    payload = data if data is not None else Path(str(path)).read_bytes()
                    storage_ref = await self.tos.put_bytes(
                        key,
                        payload,
                        mime or "application/octet-stream",
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("TOS upload failed key=%s: %s", key, exc)
                message = "文件存储服务不可用，请稍后重试"
                if "InvalidSecurityToken" in str(exc) or "token" in str(exc).lower():
                    message = "文件存储认证失败：TOS Token 无效或已过期，请更新凭据后重试"
                raise GpaError(
                    "UPSTREAM_UNAVAILABLE",
                    message,
                    retryable=True,
                    details={"provider": "tos"},
                ) from exc
        try:
            record = await self.store.create_file(
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                sha256=digest,
                name=safe,
                mime=mime or mimetypes.guess_type(safe)[0],
                size=size,
                storage_ref=storage_ref,
                meta={"purpose": purpose},
            )
        except asyncpg.UniqueViolationError:
            existing = await self.store.find_by_hash(tenant_id, app_user_id, digest)
            if existing is None:
                raise
            return existing, False
        if self.jobs is not None:
            await self.jobs.enqueue(
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                kind="extract",
                payload={"file_id": record["file_id"]},
                idempotency_key=f"extract:{record['file_id']}",
            )
        return record, True

    async def upload_conversation(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        conversation_id: str | None,
        name: str,
        mime: str | None,
        data: bytes | None = None,
        path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        role: str = "user",
    ) -> dict:
        if size is None:
            size = len(data) if data is not None else (Path(path).stat().st_size if path else 0)
        if conversation_id:
            if self.conversations is None:
                raise GpaError("UPSTREAM_UNAVAILABLE", "conversation service unavailable")
            await self.conversations.require_conversation(conversation_id, tenant_id, app_user_id)
            quota = int(getattr(self.settings, "gpa_conversation_quota_mb", 200)) * 1024 * 1024
            used = await self.store.conversation_bytes(conversation_id)
            if used + size > quota:
                raise GpaError("VALIDATION_ERROR", "conversation attachment quota exceeded")
        record, _created = await self.store_file(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            name=name,
            mime=mime,
            data=data,
            path=path,
            size=size,
            sha256=sha256,
            purpose="conversation",
        )
        conversation_file = None
        if conversation_id:
            try:
                conversation_file = await self.store.bind_conversation_file(
                    conversation_id=conversation_id,
                    file_id=record["file_id"],
                    tenant_id=tenant_id,
                    app_user_id=app_user_id,
                )
            except FileUnavailableError as exc:
                raise GpaError("CONFLICT", "file is being deleted", retryable=True) from exc
        return {
            "file": record,
            "conversation_file": conversation_file,
        }

    async def bind_files(
        self, *, conversation_id: str, tenant_id: str, app_user_id: str, file_ids: list[str]
    ) -> list[dict]:
        if self.conversations is None:
            raise GpaError("UPSTREAM_UNAVAILABLE", "conversation service unavailable")
        await self.conversations.require_conversation(conversation_id, tenant_id, app_user_id)
        quota = int(getattr(self.settings, "gpa_conversation_quota_mb", 200)) * 1024 * 1024
        used = await self.store.conversation_bytes(conversation_id)
        out: list[dict] = []
        for file_id in file_ids:
            file = await self.store.get_file(file_id)
            if file is None or file["tenant_id"] != tenant_id or file["app_user_id"] != app_user_id:
                raise GpaError("NOT_FOUND", "file not found")
            if used + int(file.get("bytes") or 0) > quota:
                raise GpaError("VALIDATION_ERROR", "conversation attachment quota exceeded")
            used += int(file.get("bytes") or 0)
            try:
                bound = await self.store.bind_conversation_file(
                    conversation_id=conversation_id,
                    file_id=file_id,
                    tenant_id=tenant_id,
                    app_user_id=app_user_id,
                )
            except FileUnavailableError as exc:
                raise GpaError("CONFLICT", "file is being deleted", retryable=True) from exc
            out.append(bound)
        return out

    async def list_conversation_files(
        self, conversation_id: str, tenant_id: str, app_user_id: str
    ) -> list[dict]:
        rows = await self.store.list_conversation_files(conversation_id, tenant_id, app_user_id)
        return [self._conversation_file_view(row) for row in rows]

    def _conversation_file_view(self, row: dict) -> dict:
        return {
            "conversationFileId": row["conversation_file_id"],
            "fileId": row["file_id"],
            "name": row.get("name") or "",
            "mime": row.get("mime"),
            "bytes": row.get("bytes"),
            "status": row.get("file_status") or "stored",
            "textStatus": row.get("text_status"),
            "textChars": row.get("text_chars") or 0,
            "textTruncated": bool(row.get("text_truncated")),
            "pageCount": row.get("page_count"),
            "error": row.get("error"),
            "downloadUrl": f"/api/files/{row['file_id']}/download",
        }

    async def attachment_payloads(self, turn_ids: list[str]) -> dict[str, list[dict]]:
        rows = await self.store.list_turn_attachments(turn_ids)
        out: dict[str, list[dict]] = {}
        for turn_id, items in rows.items():
            out[turn_id] = [
                {
                    "attachmentId": item.get("turn_attachment_id") or new_attachment_id(),
                    "fileId": item["file_id"],
                    "name": item.get("name") or "",
                    "mime": item.get("mime"),
                    "bytes": item.get("bytes"),
                    "status": item.get("file_status") or "stored",
                    "textStatus": item.get("text_status"),
                    "downloadUrl": f"/api/files/{item['file_id']}/download",
                }
                for item in items
            ]
        return out

    async def read_attachment(self, file_id: str, offset: int = 0, limit: int = 4000) -> dict:
        file = await self.store.get_file(file_id)
        if file is None:
            raise GpaError("NOT_FOUND", "file not found")
        wait_ms = int(getattr(self.settings, "gpa_file_read_wait_ms", 1500) or 0)
        deadline = asyncio.get_running_loop().time() + max(0, wait_ms) / 1000
        while file.get("text_status") in {"pending", "extracting"} and time_left(deadline) > 0:
            await asyncio.sleep(0.05)
            file = await self.store.get_file(file_id) or file
        if file.get("text_status") in {"pending", "extracting"}:
            return {"fileId": file_id, "status": "extracting", "retryAfterMs": 1000, "text": "", "nextOffset": offset, "truncated": False}
        if file.get("text_status") in {"failed", "skipped"}:
            return {"fileId": file_id, "status": file["text_status"], "text": "", "nextOffset": offset, "truncated": False, "error": file.get("error")}
        chunks = await self.store.list_chunks(file_id)
        text = "".join(chunk["text"] for chunk in chunks)
        start = max(0, int(offset))
        end = min(len(text), start + max(1, int(limit)))
        return {
            "fileId": file_id,
            "status": "ready",
            "text": text[start:end],
            "nextOffset": end,
            "truncated": end < len(text),
            "textTruncated": bool(file.get("text_truncated")),
        }

    async def delete_object(self, file_id: str, *, force: bool = False) -> dict:
        file = await self.store.get_file(file_id)
        if file is None:
            raise GpaError("NOT_FOUND", "file not found")
        refs = await self.store.count_refs(file_id)
        if refs and not force:
            raise GpaError("CONFLICT", "file still has references")
        if refs and force:
            raise GpaError("CONFLICT", "force cannot detach KB/task references; delete them first")
        await self.store.reconcile_file(file_id)
        return await self.store.get_file(file_id) or file

    async def gc_once(self) -> int:
        removed = 0
        await self.store.fail_exhausted_gc(5)
        candidates = await self.store.claim_gc_candidates(
            int(getattr(self.settings, "gpa_file_gc_retention_days", 7)), limit=20
        )
        for file in candidates:
            refs = await self.store.count_refs(file["file_id"])
            if refs:
                try:
                    await self.store.reconcile_file(file["file_id"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("gc reconcile failed %s: %s", file["file_id"], exc)
                    await self.store.release_gc_claim(file["file_id"], str(exc))
                continue
            try:
                if self.tos is not None and file.get("storage_ref"):
                    await self.tos.delete(file["storage_ref"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("file delete failed %s: %s", file["file_id"], exc)
                await self.store.release_gc_claim(file["file_id"], str(exc))
                continue
            await self.store.mark_gc_deleted(file["file_id"])
            removed += 1
        await self.store.candidate_orphans(int(getattr(self.settings, "gpa_file_orphan_hours", 24)))
        return removed


def time_left(deadline: float) -> float:
    return deadline - asyncio.get_running_loop().time()
