"""Persistence for uploaded files, conversation/KB references and file jobs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import asyncpg

from general_personal_agent.gpa.ids import (
    new_conversation_file_id,
    new_file_id,
    new_file_job_id,
    new_kb_doc_id,
    new_task_input_id,
    new_tombstone_id,
    new_turn_attachment_id,
)


class FileUnavailableError(LookupError):
    """The file exists but cannot accept a new reference (GC/deleted/mismatch)."""


FILE_COLUMNS = {
    "name",
    "mime",
    "bytes",
    "storage_ref",
    "status",
    "text_status",
    "text_chars",
    "text_truncated",
    "page_count",
    "error",
    "gc_state",
    "gc_candidate_at",
    "meta",
    "deleted_at",
}
KB_COLUMNS = {
    "collection",
    "logical_path",
    "target_uri",
    "content_sha256",
    "title",
    "index_status",
    "reconcile_state",
    "reconcile_attempts",
    "last_reconciled_at",
    "index_generation",
    "ov_task_id",
    "error",
    "indexed_at",
    "deleted_at",
    "delete_attempts",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def task_mount_path(file_id: str, name: str) -> str:
    """Stable in-sandbox path for one task input file."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or "file"))[:80]
    return f"/workspace/inputs/{file_id[:8]}_{safe or 'file'}"


class FileStore:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------- files
    async def create_file(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        sha256: str,
        name: str,
        mime: str | None,
        size: int,
        storage_ref: str | None,
        meta: dict[str, Any] | None = None,
        file_id: str | None = None,
    ) -> dict:
        # Callers that already derived an object key from a file id must pass it
        # back, otherwise ``storage_ref`` points at a different id than the row.
        file_id = file_id or new_file_id()
        row = await self.db.pool.fetchrow(
            """
            INSERT INTO file_object
              (file_id, tenant_id, app_user_id, sha256, name, mime, bytes,
               storage_ref, status, meta)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'stored',$9)
            RETURNING *
            """,
            file_id,
            tenant_id,
            app_user_id,
            sha256,
            name,
            mime,
            size,
            storage_ref,
            meta or {},
        )
        return dict(row)

    async def get_file(self, file_id: str, *, include_deleted: bool = False) -> dict | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = await self.db.pool.fetchrow(f"SELECT * FROM file_object WHERE file_id = $1{clause}", file_id)
        return dict(row) if row else None

    async def find_by_hash(self, tenant_id: str, app_user_id: str, sha256: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            """
            SELECT * FROM file_object
             WHERE tenant_id=$1 AND app_user_id=$2 AND sha256=$3
               AND deleted_at IS NULL
             ORDER BY created_at DESC LIMIT 1
            """,
            tenant_id,
            app_user_id,
            sha256,
        )
        return dict(row) if row else None

    async def update_file(self, file_id: str, **fields: Any) -> dict | None:
        clean = {k: v for k, v in fields.items() if k in FILE_COLUMNS}
        if not clean:
            return await self.get_file(file_id)
        sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(clean))
        row = await self.db.pool.fetchrow(
            f"UPDATE file_object SET {sets}, updated_at=now() WHERE file_id=$1 RETURNING *",
            file_id,
            *clean.values(),
        )
        return dict(row) if row else None

    async def replace_chunks(self, file_id: str, chunks: Iterable[dict[str, Any]]) -> None:
        async with self.db.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM file_chunk WHERE file_id=$1", file_id)
            for chunk in chunks:
                await conn.execute(
                    """
                    INSERT INTO file_chunk
                      (chunk_id, file_id, seq, text, tokens, page_from, page_to,
                       char_start, char_end)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    """,
                    f"chunk_{file_id}_{chunk['seq']}",
                    file_id,
                    chunk["seq"],
                    chunk["text"],
                    chunk.get("tokens"),
                    chunk.get("page_from"),
                    chunk.get("page_to"),
                    chunk["char_start"],
                    chunk["char_end"],
                )

    async def list_chunks(self, file_id: str) -> list[dict]:
        rows = await self.db.pool.fetch("SELECT * FROM file_chunk WHERE file_id=$1 ORDER BY seq", file_id)
        return [dict(row) for row in rows]

    # ------------------------------------------------- conversation files
    async def conversation_bytes(self, conversation_id: str) -> int:
        value = await self.db.pool.fetchval(
            """
            SELECT COALESCE(sum(f.bytes), 0)
              FROM conversation_file cf JOIN file_object f USING (file_id)
             WHERE cf.conversation_id=$1 AND cf.status='active' AND f.deleted_at IS NULL
            """,
            conversation_id,
        )
        return int(value or 0)

    async def get_file_for_update(self, conn, file_id: str) -> dict | None:
        row = await conn.fetchrow("SELECT * FROM file_object WHERE file_id=$1 FOR UPDATE", file_id)
        return dict(row) if row else None

    async def bind_conversation_file(
        self, *, conversation_id: str, file_id: str, tenant_id: str, app_user_id: str
    ) -> dict:
        """Bind a file to a conversation under the same row lock GC uses.

        GC claims a candidate with ``FOR UPDATE`` and marks it ``deleting``;
        this method locks the same row and rejects a file that is already
        deleted/deleting, so a bind cannot land after the reference check but
        before the TOS object is removed.
        """
        async with self.db.pool.acquire() as conn, conn.transaction():
            file = await conn.fetchrow("SELECT * FROM file_object WHERE file_id=$1 FOR UPDATE", file_id)
            if (
                file is None
                or file["tenant_id"] != tenant_id
                or file["app_user_id"] != app_user_id
                or file["deleted_at"] is not None
                or file["gc_state"] in {"deleting", "deleted"}
            ):
                raise FileUnavailableError("file is not available")
            existing = await conn.fetchrow(
                """
                SELECT * FROM conversation_file
                 WHERE conversation_id=$1 AND file_id=$2 AND status='active'
                """,
                conversation_id,
                file_id,
            )
            if existing:
                return dict(existing)
            cf_id = new_conversation_file_id()
            row = await conn.fetchrow(
                """
                INSERT INTO conversation_file
                  (conversation_file_id, conversation_id, file_id, tenant_id, app_user_id)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (conversation_id, file_id) WHERE status='active'
                DO NOTHING
                RETURNING *
                """,
                cf_id,
                conversation_id,
                file_id,
                tenant_id,
                app_user_id,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM conversation_file
                     WHERE conversation_id=$1 AND file_id=$2 AND status='active'
                    """,
                    conversation_id,
                    file_id,
                )
            if row is None:
                raise FileUnavailableError("file is not available")
            await conn.execute(
                """
                UPDATE file_object
                   SET gc_state='active', gc_candidate_at=NULL, updated_at=now()
                 WHERE file_id=$1 AND gc_state='candidate'
                """,
                file_id,
            )
            return dict(row)

    async def list_conversation_files(
        self, conversation_id: str, tenant_id: str, app_user_id: str
    ) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT cf.*, f.name, f.mime, f.bytes, f.sha256, f.status AS file_status,
                   f.text_status, f.text_chars, f.text_truncated, f.page_count,
                   f.error
              FROM conversation_file cf
              JOIN file_object f USING (file_id)
             WHERE cf.conversation_id=$1 AND cf.tenant_id=$2
               AND cf.app_user_id=$3 AND cf.status='active'
               AND f.deleted_at IS NULL
             ORDER BY cf.created_at
            """,
            conversation_id,
            tenant_id,
            app_user_id,
        )
        return [dict(row) for row in rows]

    async def get_conversation_file(
        self,
        conversation_file_id: str,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        *,
        conn=None,
    ) -> dict | None:
        # Callers that already hold a transaction connection must pass it:
        # acquiring a second connection here can deadlock the whole pool when
        # several attachment-bearing submissions run concurrently.
        runner = conn or self.db.pool
        row = await runner.fetchrow(
            """
            SELECT cf.*, f.name, f.mime, f.bytes, f.sha256, f.status AS file_status
              FROM conversation_file cf JOIN file_object f USING (file_id)
             WHERE cf.conversation_file_id=$1 AND cf.conversation_id=$2
               AND cf.tenant_id=$3 AND cf.app_user_id=$4
               AND cf.status='active' AND f.deleted_at IS NULL
            """,
            conversation_file_id,
            conversation_id,
            tenant_id,
            app_user_id,
        )
        return dict(row) if row else None

    async def remove_conversation_file(
        self, conversation_file_id: str, conversation_id: str, tenant_id: str, app_user_id: str
    ) -> bool:
        row = await self.db.pool.fetchrow(
            """
            UPDATE conversation_file SET status='removed', removed_at=now()
             WHERE conversation_file_id=$1 AND conversation_id=$2
               AND tenant_id=$3 AND app_user_id=$4 AND status='active'
             RETURNING file_id
            """,
            conversation_file_id,
            conversation_id,
            tenant_id,
            app_user_id,
        )
        if row:
            await self.reconcile_file(row["file_id"])
        return row is not None

    async def insert_turn_attachments(self, turn_id: str, files: list[dict]) -> None:
        for file in files:
            await self.db.pool.execute(
                """
                INSERT INTO turn_attachment (turn_attachment_id, turn_id, file_id, role)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (turn_id, file_id, role) DO NOTHING
                """,
                new_turn_attachment_id(),
                turn_id,
                file["file_id"],
                file.get("role", "input"),
            )

    async def insert_turn_attachments_conn(self, conn, turn_id: str, files: list[dict]) -> None:
        for file in files:
            await conn.execute(
                """
                INSERT INTO turn_attachment (turn_attachment_id, turn_id, file_id, role)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (turn_id, file_id, role) DO NOTHING
                """,
                new_turn_attachment_id(),
                turn_id,
                file["file_id"],
                file.get("role", "input"),
            )

    async def list_turn_attachments(self, turn_ids: list[str]) -> dict[str, list[dict]]:
        if not turn_ids:
            return {}
        rows = await self.db.pool.fetch(
            """
            SELECT ta.turn_attachment_id, ta.turn_id, ta.file_id, ta.role, f.name, f.mime, f.bytes,
                   f.status AS file_status, f.text_status, f.error
              FROM turn_attachment ta JOIN file_object f USING (file_id)
             WHERE ta.turn_id = ANY($1::text[]) AND f.deleted_at IS NULL
             ORDER BY ta.created_at
            """,
            turn_ids,
        )
        out: dict[str, list[dict]] = {turn_id: [] for turn_id in turn_ids}
        for row in rows:
            out.setdefault(row["turn_id"], []).append(dict(row))
        return out

    # -------------------------------------------------------- task inputs
    async def create_task_inputs(
        self,
        *,
        task_id: str,
        tenant_id: str,
        app_user_id: str,
        files: list[dict],
        attempt_id: str | None = None,
    ) -> list[dict]:
        created: list[dict] = []
        async with self.db.pool.acquire() as conn, conn.transaction():
            for file in files:
                locked = await conn.fetchrow(
                    "SELECT * FROM file_object WHERE file_id=$1 FOR UPDATE", file["file_id"]
                )
                if (
                    locked is None
                    or locked["tenant_id"] != tenant_id
                    or locked["app_user_id"] != app_user_id
                    or locked["deleted_at"] is not None
                    or locked["gc_state"] in {"deleting", "deleted"}
                ):
                    raise FileUnavailableError("file is not available")
                mount = task_mount_path(file["file_id"], file.get("name") or "")
                row = await conn.fetchrow(
                    """
                    INSERT INTO task_input_file
                      (task_input_id, task_id, attempt_id, file_id, mount_path)
                    VALUES ($1,$2,$3,$4,$5)
                    ON CONFLICT (task_id, file_id) DO UPDATE SET mount_path=EXCLUDED.mount_path
                    RETURNING *
                    """,
                    new_task_input_id(),
                    task_id,
                    attempt_id,
                    file["file_id"],
                    mount,
                )
                created.append(dict(row))
                await conn.execute(
                    """
                    UPDATE file_object
                       SET gc_state='active', gc_candidate_at=NULL, updated_at=now()
                     WHERE file_id=$1 AND gc_state='candidate'
                    """,
                    file["file_id"],
                )
        return created

    async def list_task_inputs(self, task_id: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT ti.*, f.name, f.mime, f.bytes, f.storage_ref, f.sha256
              FROM task_input_file ti JOIN file_object f USING (file_id)
             WHERE ti.task_id=$1 AND ti.ma_file_status <> 'deleted'
               AND f.deleted_at IS NULL
             ORDER BY ti.created_at
            """,
            task_id,
        )
        return [dict(row) for row in rows]

    async def set_task_input_ma(
        self,
        task_input_id: str,
        *,
        ma_file_id: str | None,
        status: str,
        attempt_id: str | None = None,
    ) -> None:
        await self.db.pool.execute(
            """
            UPDATE task_input_file
               SET ma_file_id=$2, ma_file_status=$3,
                   attempt_id=COALESCE($4, attempt_id)
             WHERE task_input_id=$1
            """,
            task_input_id,
            ma_file_id,
            status,
            attempt_id,
        )

    async def mark_task_inputs_deleted(self, task_id: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            UPDATE task_input_file SET ma_file_status='deleted'
             WHERE task_id=$1 AND ma_file_status <> 'deleted'
             RETURNING *
            """,
            task_id,
        )
        return [dict(row) for row in rows]

    # ----------------------------------------------------------- ref / GC
    async def count_refs(self, file_id: str) -> int:
        value = await self.db.pool.fetchval(
            """
            SELECT
              (SELECT count(*) FROM conversation_file
                WHERE file_id=$1 AND status='active') +
              (SELECT count(*) FROM turn_attachment WHERE file_id=$1) +
              (SELECT count(*) FROM task_input_file
                WHERE file_id=$1 AND ma_file_status <> 'deleted') +
              (SELECT count(*) FROM kb_document
                WHERE file_id=$1 AND deleted_at IS NULL)
            """,
            file_id,
        )
        return int(value or 0)

    async def reconcile_file(self, file_id: str) -> dict | None:
        async with self.db.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM file_object WHERE file_id=$1 FOR UPDATE", file_id)
            if row is None:
                return None
            refs = await conn.fetchval(
                """
                SELECT
                  (SELECT count(*) FROM conversation_file
                    WHERE file_id=$1 AND status='active') +
                  (SELECT count(*) FROM turn_attachment WHERE file_id=$1) +
                  (SELECT count(*) FROM task_input_file
                    WHERE file_id=$1 AND ma_file_status <> 'deleted') +
                  (SELECT count(*) FROM kb_document
                    WHERE file_id=$1 AND deleted_at IS NULL)
                """,
                file_id,
            )
            if int(refs or 0) > 0:
                await conn.execute(
                    """
                    UPDATE file_object
                       SET gc_state='active', gc_candidate_at=NULL, deleted_at=NULL,
                           updated_at=now()
                     WHERE file_id=$1 AND gc_state <> 'deleted'
                    """,
                    file_id,
                )
            elif row["file_id"] and row["gc_state"] != "deleted":
                await conn.execute(
                    """
                    UPDATE file_object
                       SET gc_state='candidate',
                           gc_candidate_at=COALESCE(gc_candidate_at, now()),
                           updated_at=now()
                     WHERE file_id=$1
                    """,
                    file_id,
                )
            fresh = await conn.fetchrow("SELECT * FROM file_object WHERE file_id=$1", file_id)
            return dict(fresh) if fresh else None

    async def candidate_orphans(self, orphan_hours: int) -> int:
        status = await self.db.pool.execute(
            """
            UPDATE file_object f SET gc_state='candidate', gc_candidate_at=now(), updated_at=now()
             WHERE f.gc_state='active' AND f.deleted_at IS NULL
               AND f.created_at < now() - ($1::int * interval '1 hour')
               AND NOT EXISTS (SELECT 1 FROM conversation_file cf
                                WHERE cf.file_id=f.file_id AND cf.status='active')
               AND NOT EXISTS (SELECT 1 FROM turn_attachment ta WHERE ta.file_id=f.file_id)
               AND NOT EXISTS (SELECT 1 FROM task_input_file ti
                                WHERE ti.file_id=f.file_id AND ti.ma_file_status <> 'deleted')
               AND NOT EXISTS (SELECT 1 FROM kb_document kd
                                WHERE kd.file_id=f.file_id AND kd.deleted_at IS NULL)
            """,
            max(1, int(orphan_hours)),
        )
        try:
            return int(status.split()[-1])
        except (AttributeError, IndexError, ValueError):
            return 0

    async def claim_gc_candidates(self, retention_days: int, limit: int = 20) -> list[dict]:
        """Claim due candidates and reclaim expired/stale ``deleting`` rows.

        A failed deletion must never clear ``deleted_at``: a same-content
        re-upload is already a separate row by then, and clearing the old
        logical delete would violate the partial dedupe index.
        """
        async with self.db.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT * FROM file_object
                 WHERE gc_attempts < 5
                   AND (
                     (gc_state='candidate'
                      AND gc_candidate_at IS NOT NULL
                      AND gc_candidate_at <= now() - ($1::int * interval '1 day'))
                     OR (gc_state='deleting'
                         AND (gc_lease_until IS NULL OR gc_lease_until < now()))
                   )
                 ORDER BY COALESCE(gc_lease_until, gc_candidate_at, updated_at)
                 LIMIT $2 FOR UPDATE SKIP LOCKED
                """,
                max(1, int(retention_days)),
                limit,
            )
            if not rows:
                return []
            ids = [row["file_id"] for row in rows]
            await conn.execute(
                """
                UPDATE file_object
                   SET gc_state='deleting',
                       deleted_at=COALESCE(deleted_at, now()),
                       gc_lease_until=now() + interval '5 minutes',
                       gc_attempts=gc_attempts+1,
                       updated_at=now()
                 WHERE file_id = ANY($1::text[])
                """,
                ids,
            )
            claimed = await conn.fetch(
                "SELECT * FROM file_object WHERE file_id = ANY($1::text[])"
                " ORDER BY COALESCE(gc_lease_until, gc_candidate_at, updated_at)",
                ids,
            )
            return [dict(row) for row in claimed]

    async def fail_exhausted_gc(self, max_attempts: int = 5) -> int:
        status = await self.db.pool.execute(
            """
            UPDATE file_object
               SET gc_state='delete_failed', gc_lease_until=NULL,
                   gc_error=COALESCE(gc_error, 'gc attempts exhausted'), updated_at=now()
             WHERE gc_state='deleting' AND gc_attempts >= $1
               AND (gc_lease_until IS NULL OR gc_lease_until < now())
            """,
            max(1, int(max_attempts)),
        )
        try:
            return int(status.split()[-1])
        except (AttributeError, IndexError, ValueError):
            return 0

    async def release_gc_claim(self, file_id: str, error: str, max_attempts: int = 5) -> None:
        await self.db.pool.execute(
            """
            UPDATE file_object
               SET gc_state=CASE WHEN gc_attempts >= $2 THEN 'delete_failed' ELSE 'deleting' END,
                   gc_lease_until=NULL, gc_error=$3, updated_at=now()
             WHERE file_id=$1
            """,
            file_id,
            max(1, int(max_attempts)),
            error[:500],
        )

    async def mark_gc_deleted(self, file_id: str) -> None:
        await self.db.pool.execute(
            """
            UPDATE file_object
               SET gc_state='deleted', gc_lease_until=NULL, gc_error=NULL,
                   deleted_at=COALESCE(deleted_at, now()), updated_at=now()
             WHERE file_id=$1
            """,
            file_id,
        )

    async def revive_file(self, file_id: str) -> dict | None:
        """Turn a candidate back into an active file only if GC has not claimed it."""
        row = await self.db.pool.fetchrow(
            """
            UPDATE file_object
               SET gc_state='active', gc_candidate_at=NULL, updated_at=now()
             WHERE file_id=$1 AND gc_state='candidate' AND deleted_at IS NULL
             RETURNING *
            """,
            file_id,
        )
        return dict(row) if row else None

    # ------------------------------------------------------------- KB docs
    async def kb_usage_bytes(self, tenant_id: str, scope: str, app_user_id: str | None = None) -> int:
        clauses = ["kd.tenant_id=$1", "kd.scope=$2", "kd.deleted_at IS NULL"]
        args: list[Any] = [tenant_id, scope]
        if scope == "private":
            if app_user_id is None:
                return 0
            args.append(app_user_id)
            clauses.append(f"kd.app_user_id=${len(args)}")
        value = await self.db.pool.fetchval(
            f"SELECT COALESCE(sum(f.bytes),0) FROM kb_document kd"
            f" JOIN file_object f USING (file_id) WHERE {' AND '.join(clauses)}",
            *args,
        )
        return int(value or 0)

    async def create_kb_document(
        self,
        *,
        file_id: str,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        target_uri: str,
        logical_path: str,
        content_sha256: str,
        title: str,
    ) -> dict:
        async with self.db.pool.acquire() as conn:
            return await self.create_kb_document_conn(
                conn,
                file_id=file_id,
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                scope=scope,
                target_uri=target_uri,
                logical_path=logical_path,
                content_sha256=content_sha256,
                title=title,
            )

    async def create_kb_document_conn(
        self,
        conn,
        *,
        file_id: str,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        target_uri: str,
        logical_path: str,
        content_sha256: str,
        title: str,
    ) -> dict:
        doc_id = new_kb_doc_id()
        row = await conn.fetchrow(
            """
            INSERT INTO kb_document
              (doc_id, file_id, tenant_id, app_user_id, scope, target_uri,
               logical_path, content_sha256, title)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *
            """,
            doc_id,
            file_id,
            tenant_id,
            app_user_id,
            scope,
            target_uri,
            logical_path,
            content_sha256,
            title,
        )
        return dict(row)

    async def get_kb_document_for_update(self, conn, doc_id: str) -> dict | None:
        row = await conn.fetchrow("SELECT * FROM kb_document WHERE doc_id=$1 FOR UPDATE", doc_id)
        return dict(row) if row else None

    async def find_kb_by_hash(
        self,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        content_sha256: str,
        conn=None,
    ) -> dict | None:
        clauses = ["tenant_id=$1", "scope=$2", "content_sha256=$3", "deleted_at IS NULL"]
        args: list[Any] = [tenant_id, scope, content_sha256]
        if scope == "private":
            args.append(app_user_id)
            clauses.append(f"app_user_id=${len(args)}")
        runner = conn or self.db.pool
        row = await runner.fetchrow(
            f"SELECT * FROM kb_document WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 1",
            *args,
        )
        return dict(row) if row else None

    async def get_kb_document(self, doc_id: str) -> dict | None:
        row = await self.db.pool.fetchrow("SELECT * FROM kb_document WHERE doc_id=$1", doc_id)
        return dict(row) if row else None

    async def get_kb_by_uris(self, tenant_id: str, uris: list[str]) -> dict[str, dict]:
        if not uris:
            return {}
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE tenant_id=$1 AND target_uri = ANY($2::text[])
            """,
            tenant_id,
            uris,
        )
        docs: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            # Prefer the live row when a URI also has historical deleted rows.
            if item["target_uri"] not in docs or item.get("deleted_at") is None:
                docs[item["target_uri"]] = item
        return docs

    async def get_kb_by_uri(
        self, tenant_id: str, target_uri: str, *, include_deleted: bool = False
    ) -> dict | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = await self.db.pool.fetchrow(
            f"""
            SELECT * FROM kb_document
             WHERE tenant_id=$1 AND target_uri=$2{clause}
             ORDER BY (deleted_at IS NULL) DESC, created_at DESC LIMIT 1
            """,
            tenant_id,
            target_uri,
        )
        return dict(row) if row else None

    async def update_kb_document(self, doc_id: str, **fields: Any) -> dict | None:
        async with self.db.pool.acquire() as conn:
            return await self.update_kb_document_conn(conn, doc_id, **fields)

    async def update_kb_document_conn(self, conn, doc_id: str, **fields: Any) -> dict | None:
        clean = {k: v for k, v in fields.items() if k in KB_COLUMNS}
        if not clean:
            return None
        sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(clean))
        row = await conn.fetchrow(
            f"UPDATE kb_document SET {sets}, updated_at=now() WHERE doc_id=$1 RETURNING *",
            doc_id,
            *clean.values(),
        )
        return dict(row) if row else None

    async def transition_kb_document(
        self,
        doc_id: str,
        *,
        expected_generation: int | None = None,
        from_statuses: set[str] | None = None,
        **fields: Any,
    ) -> dict | None:
        """Compare-and-set a KB lifecycle state.

        An index/delete job may only write if the document is still in the
        generation and state it observed.  This is what prevents a stale index
        task from turning a deleted document back into ``ready``.
        """
        clean = {k: v for k, v in fields.items() if k in KB_COLUMNS}
        if not clean:
            return None
        sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(clean))
        args: list[Any] = [doc_id, *clean.values()]
        clauses = ["doc_id=$1"]
        if expected_generation is not None:
            args.append(int(expected_generation))
            clauses.append(f"index_generation=${len(args)}")
        if from_statuses:
            args.append(list(from_statuses))
            clauses.append(f"index_status = ANY(${len(args)}::text[])")
        row = await self.db.pool.fetchrow(
            f"UPDATE kb_document SET {sets}, updated_at=now() WHERE {' AND '.join(clauses)} RETURNING *",
            *args,
        )
        return dict(row) if row else None

    async def begin_kb_delete(
        self,
        *,
        doc_id: str,
        reason: str,
        tombstone_expires_at: datetime,
        dir_uris: list[str],
    ) -> dict | None:
        """Bump generation and hide/tombstone a document atomically."""
        async with self.db.pool.acquire() as conn, conn.transaction():
            doc = await conn.fetchrow("SELECT * FROM kb_document WHERE doc_id=$1 FOR UPDATE", doc_id)
            if doc is None:
                return None
            generation = int(doc["index_generation"]) + 1
            row = await conn.fetchrow(
                """
                UPDATE kb_document
                   SET index_generation=$2, index_status='deleting',
                       deleted_at=COALESCE(deleted_at, now()), error=NULL,
                       delete_attempts=delete_attempts+1, updated_at=now()
                 WHERE doc_id=$1
                 RETURNING *
                """,
                doc_id,
                generation,
            )
            # OV turns one imported file into a folder tree.  The tombstone
            # must cover the whole subtree, otherwise a late import leaks its
            # child nodes back into the context browser.
            await conn.execute(
                """
                INSERT INTO kb_tombstone
                  (tombstone_id, tenant_id, app_user_id, scope, uri, recursive,
                   reason, expires_at)
                VALUES ($1,$2,$3,$4,$5,true,$6,$7)
                """,
                new_tombstone_id(),
                doc["tenant_id"],
                doc["app_user_id"],
                doc["scope"],
                doc["target_uri"],
                reason,
                tombstone_expires_at,
            )
            for dir_uri in dict.fromkeys(dir_uris):
                await conn.execute(
                    """
                    INSERT INTO kb_dir_invalidation (tenant_id, scope, dir_uri, reason)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (tenant_id, scope, dir_uri) DO UPDATE
                      SET resolved_at=NULL, reason=EXCLUDED.reason
                    """,
                    doc["tenant_id"],
                    doc["scope"],
                    dir_uri,
                    reason,
                )
            return dict(row) if row else None

    async def requeue_missing_kb_document(self, doc_id: str, expected_generation: int) -> dict | None:
        """Atomically bump generation and return a reconciling doc to pending."""
        row = await self.db.pool.fetchrow(
            """
            UPDATE kb_document
               SET index_generation = index_generation + 1,
                   index_status = 'pending',
                   reconcile_state = 'none',
                   reconcile_attempts = 0,
                   error = NULL,
                   updated_at = now()
             WHERE doc_id=$1 AND index_generation=$2 AND index_status='reconciling'
             RETURNING *
            """,
            doc_id,
            int(expected_generation),
        )
        return dict(row) if row else None

    async def list_deleting_kb_documents(self, older_than_s: int = 120, limit: int = 20) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE index_status IN ('deleting', 'delete_failed')
               AND delete_attempts < 5
               AND updated_at < now() - ($1::int * interval '1 second')
             ORDER BY updated_at
             LIMIT $2
            """,
            max(1, int(older_than_s)),
            max(1, int(limit)),
        )
        return [dict(row) for row in rows]

    async def list_stalled_kb_documents(self, older_than_s: int = 300, limit: int = 20) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE index_status IN ('pending', 'indexing')
               AND updated_at < now() - ($1::int * interval '1 second')
             ORDER BY updated_at
             LIMIT $2
            """,
            max(1, int(older_than_s)),
            max(1, int(limit)),
        )
        return [dict(row) for row in rows]

    async def list_deleted_kb_documents(
        self, older_than_s: int = 300, limit: int = 20, *, lookback_hours: int = 48
    ) -> list[dict]:
        """Recently deleted docs whose OV target may still materialize late."""
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE index_status='deleted'
               AND deleted_at IS NOT NULL
               AND deleted_at > now() - ($1::int * interval '1 hour')
               AND updated_at < now() - ($2::int * interval '1 second')
             ORDER BY deleted_at DESC
             LIMIT $3
            """,
            max(1, int(lookback_hours)),
            max(1, int(older_than_s)),
            max(1, int(limit)),
        )
        return [dict(row) for row in rows]

    async def list_kb_documents(
        self,
        tenant_id: str,
        app_user_id: str | None,
        scope: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict]:
        clauses = ["kd.tenant_id=$1", "kd.scope=$2"]
        args: list[Any] = [tenant_id, scope]
        if scope == "private":
            if app_user_id is None:
                return []
            args.append(app_user_id)
            clauses.append(f"kd.app_user_id=${len(args)}")
        if not include_deleted:
            clauses.append("kd.deleted_at IS NULL")
        rows = await self.db.pool.fetch(
            "SELECT kd.*, f.bytes AS file_bytes, f.mime AS file_mime,"
            " f.name AS file_name FROM kb_document kd"
            " LEFT JOIN file_object f USING (file_id)"
            f" WHERE {' AND '.join(clauses)} ORDER BY kd.created_at DESC",
            *args,
        )
        return [dict(row) for row in rows]

    async def list_kb_deletions(
        self, tenant_id: str, app_user_id: str, *, include_shared: bool = False
    ) -> list[dict]:
        scopes = ["private"] + (["shared"] if include_shared else [])
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE tenant_id=$1 AND scope = ANY($2::text[])
               AND index_status IN ('deleting','delete_failed')
               AND (scope='shared' OR app_user_id=$3)
             ORDER BY created_at DESC
            """,
            tenant_id,
            scopes,
            app_user_id,
        )
        return [dict(row) for row in rows]

    async def insert_tombstone(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        uri: str,
        recursive: bool,
        reason: str,
        expires_at: datetime,
    ) -> dict:
        row = await self.db.pool.fetchrow(
            """
            INSERT INTO kb_tombstone
              (tombstone_id, tenant_id, app_user_id, scope, uri, recursive, reason, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *
            """,
            new_tombstone_id(),
            tenant_id,
            app_user_id,
            scope,
            uri,
            recursive,
            reason,
            expires_at,
        )
        return dict(row)

    async def list_tombstones(self, tenant_id: str, scope: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_tombstone
             WHERE tenant_id=$1 AND scope=$2 AND expires_at > now()
            """,
            tenant_id,
            scope,
        )
        return [dict(row) for row in rows]

    async def remove_tombstone_for_uri(self, tenant_id: str, scope: str, uri: str) -> None:
        await self.db.pool.execute(
            "DELETE FROM kb_tombstone WHERE tenant_id=$1 AND scope=$2 AND uri=$3",
            tenant_id,
            scope,
            uri,
        )

    # ------------------------------------------------------ URI locks/dirs
    async def lock_uri(
        self,
        *,
        tenant_id: str,
        scope: str,
        uri_prefix: str,
        owner_job_id: str,
        expires_at: datetime,
    ) -> bool:
        try:
            await self.db.pool.execute(
                """
                INSERT INTO kb_uri_lock
                  (tenant_id, scope, uri_prefix, mode, owner_job_id, expires_at)
                VALUES ($1,$2,$3,'delete',$4,$5)
                """,
                tenant_id,
                scope,
                uri_prefix,
                owner_job_id,
                expires_at,
            )
            return True
        except asyncpg.UniqueViolationError:
            row = await self.db.pool.fetchrow(
                """
                SELECT * FROM kb_uri_lock
                 WHERE tenant_id=$1 AND scope=$2 AND uri_prefix=$3 AND expires_at > now()
                """,
                tenant_id,
                scope,
                uri_prefix,
            )
            return row is None

    async def release_uri_lock(self, tenant_id: str, scope: str, uri_prefix: str) -> None:
        await self.db.pool.execute(
            "DELETE FROM kb_uri_lock WHERE tenant_id=$1 AND scope=$2 AND uri_prefix=$3",
            tenant_id,
            scope,
            uri_prefix,
        )

    async def uri_locked(self, tenant_id: str, scope: str, uri: str) -> bool:
        row = await self.db.pool.fetchrow(
            """
            SELECT 1 FROM kb_uri_lock
             WHERE tenant_id=$1 AND scope=$2 AND expires_at > now()
               AND ($3 = uri_prefix OR left($3, length(uri_prefix) + 1) = uri_prefix || '/')
             LIMIT 1
            """,
            tenant_id,
            scope,
            uri,
        )
        return row is not None

    async def insert_dir_invalidation(self, *, tenant_id: str, scope: str, dir_uri: str, reason: str) -> None:
        await self.db.pool.execute(
            """
            INSERT INTO kb_dir_invalidation (tenant_id, scope, dir_uri, reason)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (tenant_id, scope, dir_uri) DO UPDATE
              SET resolved_at=NULL, reason=EXCLUDED.reason
            """,
            tenant_id,
            scope,
            dir_uri,
            reason,
        )

    async def resolve_dir_invalidation(self, tenant_id: str, scope: str, dir_uri: str) -> None:
        await self.db.pool.execute(
            """
            UPDATE kb_dir_invalidation SET resolved_at=now()
             WHERE tenant_id=$1 AND scope=$2 AND dir_uri=$3
            """,
            tenant_id,
            scope,
            dir_uri,
        )

    async def bump_dir_invalidation(self, tenant_id: str, scope: str, dir_uri: str) -> int:
        value = await self.db.pool.fetchval(
            """
            UPDATE kb_dir_invalidation
               SET rebuild_attempts=rebuild_attempts+1, last_attempt_at=now()
             WHERE tenant_id=$1 AND scope=$2 AND dir_uri=$3
             RETURNING rebuild_attempts
            """,
            tenant_id,
            scope,
            dir_uri,
        )
        return int(value or 0)

    async def list_open_invalidations(self, tenant_id: str, scope: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_dir_invalidation
             WHERE tenant_id=$1 AND scope=$2 AND resolved_at IS NULL
            """,
            tenant_id,
            scope,
        )
        return [dict(row) for row in rows]

    async def list_reconciling_documents(self, limit: int = 20) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM kb_document
             WHERE index_status='reconciling'
               AND (last_reconciled_at IS NULL OR last_reconciled_at < now() - interval '2 minutes')
             ORDER BY COALESCE(last_reconciled_at, created_at)
             LIMIT $1
            """,
            max(1, int(limit)),
        )
        return [dict(row) for row in rows]

    async def next_kb_generation(self, doc_id: str) -> int:
        value = await self.db.pool.fetchval(
            "UPDATE kb_document SET index_generation=index_generation+1 WHERE doc_id=$1"
            " RETURNING index_generation",
            doc_id,
        )
        return int(value or 0)

    # ------------------------------------------------------------- jobs
    async def enqueue_job(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        kind: str,
        payload: dict,
        idempotency_key: str,
        run_after: datetime | None = None,
    ) -> bool:
        status = await self.db.pool.execute(
            """
            INSERT INTO file_job
              (job_id, tenant_id, app_user_id, type, payload, idempotency_key, run_after)
            VALUES ($1,$2,$3,$4,$5,$6,COALESCE($7, now()))
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            new_file_job_id(),
            tenant_id,
            app_user_id,
            kind,
            payload,
            idempotency_key,
            run_after,
        )
        return status == "INSERT 0 1"

    async def claim_jobs(self, limit: int = 10) -> list[dict]:
        """Claim queued jobs and reclaim running jobs whose lease expired."""
        token = new_file_job_id()
        async with self.db.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT * FROM file_job
                 WHERE (status='queued' AND run_after <= now())
                    OR (status='running' AND lease_until IS NOT NULL AND lease_until < now())
                 ORDER BY run_after, created_at, job_id
                 LIMIT $1 FOR UPDATE SKIP LOCKED
                """,
                limit,
            )
            if not rows:
                return []
            ids = [row["job_id"] for row in rows]
            await conn.execute(
                """
                UPDATE file_job
                   SET status='running', attempts=attempts+1,
                       started_at=COALESCE(started_at, now()),
                       lease_until=now() + interval '15 minutes',
                       heartbeat_at=now(), claim_token=$2
                 WHERE job_id = ANY($1::text[])
                """,
                ids,
                token,
            )
            claimed = await conn.fetch(
                "SELECT * FROM file_job WHERE job_id = ANY($1::text[])"
                " ORDER BY run_after, created_at, job_id",
                ids,
            )
            return [dict(row) for row in claimed]

    async def complete_job(self, job_id: str, claim_token: str | None = None) -> bool:
        status = await self.db.pool.execute(
            """
            UPDATE file_job
               SET status='done', finished_at=now(), error=NULL,
                   lease_until=NULL, heartbeat_at=NULL
             WHERE job_id=$1 AND ($2::text IS NULL OR claim_token=$2)
            """,
            job_id,
            claim_token,
        )
        return status == "UPDATE 1"

    async def fail_job(
        self,
        job_id: str,
        error: str,
        *,
        retry_after_s: int = 5,
        claim_token: str | None = None,
    ) -> None:
        row = await self.db.pool.fetchrow(
            "SELECT attempts, max_attempts FROM file_job WHERE job_id=$1"
            " AND ($2::text IS NULL OR claim_token=$2)",
            job_id,
            claim_token,
        )
        if row is None:
            return
        dead = int(row["attempts"] or 0) >= int(row["max_attempts"] or 3)
        await self.db.pool.execute(
            """
            UPDATE file_job
               SET status=$2, error=$3,
                   run_after=CASE WHEN $2='queued' THEN now() + ($4::int * interval '1 second') ELSE run_after END,
                   lease_until=NULL, heartbeat_at=NULL,
                   finished_at=CASE WHEN $2='dead' THEN now() ELSE finished_at END
             WHERE job_id=$1 AND ($5::text IS NULL OR claim_token=$5)
            """,
            job_id,
            "dead" if dead else "queued",
            error[:500],
            max(1, retry_after_s),
            claim_token,
        )

    async def job(self, job_id: str) -> dict | None:
        row = await self.db.pool.fetchrow("SELECT * FROM file_job WHERE job_id=$1", job_id)
        return dict(row) if row else None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
