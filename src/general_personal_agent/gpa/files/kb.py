"""Knowledge-base operations backed by OpenViking, TOS and PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from general_personal_agent.clients.ov import OpenVikingClient, OVError
from general_personal_agent.gpa.context.layout import build_layout, safe_segment
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.extract import safe_name
from general_personal_agent.gpa.files.guard import KbReadGuard
from general_personal_agent.gpa.files.service import FileService
from general_personal_agent.gpa.files.store import FileStore
from general_personal_agent.gpa.ids import new_file_job_id, new_kb_doc_id
from general_personal_agent.gpa.util import sha256_bytes

logger = logging.getLogger(__name__)

#: OV ``add_resource`` terminal states (success and failure alike).
_IMPORT_TERMINAL_STATUSES = {
    "completed",
    "success",
    "succeeded",
    "done",
    "failed",
    "error",
    "cancelled",
    "canceled",
}

ClientFactory = Callable[[str, str], Awaitable[OpenVikingClient]]

#: OV's per-document parse output: one markdown file per detected section.
_GENERATED_FILES = {".abstract.md", ".overview.md"}
_SECTION_SUFFIX = ".md"

#: Size at which a short section stops being suspicious.  Length alone is not
#: enough: a two-line paragraph that ends with a full stop is content, while a
#: slide-deck cover (``title`` + ``author · role``) is an artifact even though
#: both are < 250 chars.  See :func:`_heading_only`.
_TITLE_BLOCK_MAX_CHARS = 120
_MIN_CONTENT_CHARS = 24

#: Hard ceiling for one ``read_uri`` page so a tool call cannot flood context.
READ_MAX_CHARS = 8000
READ_DEFAULT_CHARS = 4000


def normalize_kb_uri(raw: str) -> str:
    """Normalize a viking URI and reject traversal / query tricks."""
    uri = (raw or "").strip()
    if not uri.startswith("viking://"):
        raise GpaError("VALIDATION_ERROR", "invalid knowledge uri")
    if "\\" in uri or any(ord(ch) < 32 for ch in uri):
        raise GpaError("VALIDATION_ERROR", "invalid knowledge uri")
    parsed = urlsplit(uri)
    if parsed.scheme != "viking" or not parsed.netloc or parsed.query or parsed.fragment:
        raise GpaError("VALIDATION_ERROR", "invalid knowledge uri")
    segments = [seg for seg in parsed.path.split("/") if seg]
    if any(seg in {".", ".."} for seg in segments):
        raise GpaError("VALIDATION_ERROR", "invalid knowledge uri")
    path = "/".join(segments)
    return f"viking://{parsed.netloc}/{path}" if path else f"viking://{parsed.netloc}"


def uri_within(uri: str, root: str) -> bool:
    return uri == root or uri.startswith(root.rstrip("/") + "/")


def _owner_doc(uri: str, docs: list[dict]) -> dict | None:
    """Deepest indexed document whose resource folder contains ``uri``.

    OV expands an imported file into ``<target_uri>/<slug>/<section>.md``, so a
    retrieved URI is usually a *section*, not the document row.  The longest
    matching folder wins when a document is nested under another one.
    """
    best: dict | None = None
    best_len = -1
    for doc in docs:
        root = str(doc.get("target_uri") or "").rstrip("/")
        if root and uri_within(uri, root) and len(root) > best_len:
            best, best_len = doc, len(root)
    return best


def _is_section(uri: str) -> bool:
    name = uri.rstrip("/").rsplit("/", 1)[-1]
    return name.endswith(_SECTION_SUFFIX) and name not in _GENERATED_FILES


def _section_title(uri: str) -> str:
    name = uri.rstrip("/").rsplit("/", 1)[-1]
    return name[: -len(_SECTION_SUFFIX)] if name.endswith(_SECTION_SUFFIX) else name


def _readable_chars(text: str) -> int:
    """Body length with whitespace removed (headings are short; pages are not)."""
    return len(re.sub(r"\s+", "", text or ""))


#: Prose ends with punctuation; a cover (``标题`` + ``作者 · 职务``) does not.
_SENTENCE_END = re.compile(r"[。！？!?]|[.]$", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")


def _heading_only(body: str) -> bool:
    """True when a parsed section is a heading/byline shell, not prose.

    A slide deck's cover page becomes its own section holding the title and the
    author line; ranking it first is what made the agent report "indexed with
    the title and author only".  Length is a weak signal on its own — a report
    can have perfectly valid two-line paragraphs — so short bodies are only
    called thin when they also look like a title block: at most three lines,
    no bullets, and no sentence-ending punctuation.
    """
    text = (body or "").strip()
    if not text:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    # A bullet list is content even when it is short and unpunctuated.
    if any(_BULLET.match(line) for line in lines):
        return False
    readable = _readable_chars(text)
    if readable < _MIN_CONTENT_CHARS:
        return True
    if readable > _TITLE_BLOCK_MAX_CHARS:
        return False
    if len(lines) > 3:
        return False
    return not _SENTENCE_END.search(text)


#: OV embeds every parsed page as ``![image](viking://…)``; the agent cannot
#: render those URIs, so they only burn context.  ``<KBImage>`` wrappers do
#: carry OCR text inside the tag, so the wrapper is unwrapped, not dropped.
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(viking://[^)]*\)")
_KB_IMAGE = re.compile(r"<KBImage\b[^>]*>(.*?)</KBImage>", re.DOTALL)


def _clean_markdown(text: str) -> str:
    """Drop unrenderable image links, keep OCR text, collapse blank runs."""
    cleaned = _KB_IMAGE.sub(lambda match: match.group(1), text or "")
    lines = [
        line for line in cleaned.splitlines() if not _IMAGE_LINK.fullmatch(line.strip())
    ]
    cleaned = _IMAGE_LINK.sub("", "\n".join(lines))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def import_outcome_unknown(exc: BaseException) -> bool:
    """True when the remote may have accepted the import despite the error."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, OVError):
        return exc.status in {408, 429} or exc.status >= 500
    # TOS/OV parsing errors, malformed responses, etc. are not proof that the
    # remote rejected the write.  Reconcile instead of declaring failed.
    return True


class KbService:
    def __init__(
        self,
        settings,
        store: FileStore,
        files: FileService,
        tos,
        identity_service,
        jobs=None,
        client_factory: ClientFactory | None = None,
    ):
        self.settings = settings
        self.store = store
        self.files = files
        self.tos = tos
        self.identity_service = identity_service
        self.jobs = jobs
        self.guard = KbReadGuard(store)
        self._client_factory = client_factory
        self._clients: dict[str, OpenVikingClient] = {}

    # ------------------------------------------------------------- identity
    async def _identity(self, scope: str, app_user_id: str, tenant_id: str, *, write: bool = False):
        if self.identity_service is None:
            raise GpaError("UPSTREAM_UNAVAILABLE", "identity unavailable")
        try:
            # Shared KB writes (create/index/delete) are admin operations.
            # Reads use the caller's own identity: viking://resources is the
            # account-shared library, and a normal user must never inherit the
            # admin key just to preview a shared document.
            if scope == "shared" and write:
                return await self.identity_service.for_admin(tenant_id)
            return await self.identity_service.resolve(app_user_id, tenant_id)
        except Exception as exc:  # noqa: BLE001
            raise GpaError("UPSTREAM_UNAVAILABLE", "knowledge identity unavailable") from exc

    async def role_for(self, app_user_id: str, tenant_id: str) -> str:
        if self.identity_service is None:
            return "user"
        identity = await self.identity_service.resolve(app_user_id, tenant_id)
        return identity.role

    async def _client(self, scope: str, app_user_id: str, tenant_id: str, *, write: bool = False):
        identity = await self._identity(scope, app_user_id, tenant_id, write=write)
        if scope == "shared":
            key = f"shared-admin:{tenant_id}" if write else f"shared-read:{tenant_id}:{identity.ov_user}"
        else:
            key = f"private:{tenant_id}:{identity.app_user_id}"
        if key not in self._clients:
            if self._client_factory is not None:
                self._clients[key] = await self._client_factory(identity.api_key, identity.ov_user)
            else:
                self._clients[key] = OpenVikingClient(
                    self.settings.ov_url, identity.api_key, self.settings.ov_timeout_s
                )
        return self._clients[key], identity

    def _layout(self, identity):
        return build_layout(self.settings, identity)

    def _root(self, identity, scope: str) -> str:
        layout = self._layout(identity)
        return layout.resources if scope == "private" else layout.shared_root

    def _write_root(self, identity, scope: str, category_id: str | None) -> str:
        layout = self._layout(identity)
        try:
            category = safe_segment(category_id, name="categoryId") if category_id else None
        except Exception as exc:  # noqa: BLE001
            raise GpaError("VALIDATION_ERROR", "invalid categoryId") from exc
        if scope == "shared":
            return layout.write_shared_kb(category)
        base = layout.write_personal_kb("uploads")
        return f"{base.rstrip('/')}/{category}" if category else base

    async def _ancestor_dirs(
        self, *, scope: str, app_user_id: str, tenant_id: str, dir_uri: str
    ) -> list[str]:
        """All directories from ``dir_uri`` up to (and including) the KB root."""
        identity = await self._identity(scope, app_user_id, tenant_id, write=scope == "shared")
        root = self._root(identity, scope).rstrip("/")
        dirs: list[str] = []
        current = dir_uri.rstrip("/")
        while current == root or current.startswith(root + "/"):
            dirs.append(current)
            if current == root:
                break
            parent = current.rsplit("/", 1)[0]
            if parent == current:
                break
            current = parent
        return dirs or [dir_uri.rstrip("/")]

    # ---------------------------------------------------------------- upload
    async def upload(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        role: str,
        scope: str,
        name: str,
        mime: str | None,
        data: bytes | None = None,
        path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        category_id: str | None = None,
    ) -> dict:
        if scope not in {"private", "shared"}:
            raise GpaError("VALIDATION_ERROR", "invalid knowledge scope")
        if scope == "shared" and role != "admin":
            raise GpaError("FORBIDDEN", "only admins may write the shared knowledge base")
        if scope == "shared" and not getattr(self.settings, "gpa_kb_shared_write_enabled", True):
            raise GpaError("FORBIDDEN", "shared knowledge writes are disabled")
        quota_mb = int(
            getattr(
                self.settings,
                "gpa_kb_shared_quota_mb" if scope == "shared" else "gpa_kb_private_quota_mb",
                2048,
            )
        )
        if size is None:
            size = len(data) if data is not None else (Path(path).stat().st_size if path else 0)
        used = await self.store.kb_usage_bytes(tenant_id, scope, app_user_id)
        if used + size > quota_mb * 1024 * 1024:
            raise GpaError("VALIDATION_ERROR", "knowledge base quota exceeded")
        identity = await self._identity(scope, app_user_id, tenant_id, write=scope == "shared")
        digest = sha256 or (sha256_bytes(data) if data is not None else self.files.sha256_path(str(path)))
        existing = await self.store.find_kb_by_hash(tenant_id, app_user_id, scope, digest)
        if existing is not None and existing["index_status"] not in {"deleting", "deleted"}:
            return {"doc": existing, "dedupe": True, "file": await self.store.get_file(existing["file_id"])}
        record, _created = await self.files.store_file(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            name=name,
            mime=mime,
            data=data,
            path=path,
            size=size,
            sha256=digest,
            purpose=f"{scope}_kb",
        )
        target_dir = self._write_root(identity, scope, category_id)
        category = (category_id or "").strip()
        slug = self._slug(name)
        doc_id_placeholder = new_kb_doc_id()
        ext = "." + safe_name(name).rsplit(".", 1)[-1] if "." in safe_name(name) else ""
        target_uri = f"{target_dir.rstrip('/')}/{slug}-{digest[:8]}-{doc_id_placeholder[4:12]}{ext}"
        logical_path = f"{category + '/' if category else ''}{slug}{ext}"
        if await self.store.uri_locked(tenant_id, scope, target_uri):
            raise GpaError("CONFLICT", "knowledge path is being deleted", retryable=True)
        if self._client_factory is None:
            client, _ = await self._client(scope, app_user_id, tenant_id, write=scope == "shared")
            await self._mkdir_parent(client, target_uri)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            locked = await self.store.get_file_for_update(conn, record["file_id"])
            if (
                locked is None
                or locked.get("deleted_at")
                or locked.get("gc_state") in {"deleting", "deleted"}
            ):
                raise GpaError("CONFLICT", "file is being deleted", retryable=True)
            doc = await self.store.create_kb_document_conn(
                conn,
                file_id=record["file_id"],
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                scope=scope,
                target_uri=target_uri,
                logical_path=logical_path,
                content_sha256=digest,
                title=safe_name(name),
            )
            # The placeholder doc_id is only used to make the URI version unique;
            # replace it with the real id without changing the file content.
            doc = (
                await self.store.update_kb_document_conn(
                    conn,
                    doc["doc_id"],
                    target_uri=f"{target_dir.rstrip('/')}/{slug}-{digest[:8]}-{doc['doc_id'][4:12]}{ext}",
                )
                or doc
            )
            await conn.execute(
                "UPDATE file_object SET gc_state='active', gc_candidate_at=NULL, updated_at=now()"
                " WHERE file_id=$1 AND gc_state='candidate'",
                record["file_id"],
            )
        await self._enqueue_index(doc, tenant_id, app_user_id)
        return {"doc": doc, "dedupe": False, "file": record}

    def _slug(self, name: str) -> str:
        base = safe_name(name)
        if "." in base:
            base = base.rsplit(".", 1)[0]
        out = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in base).strip("-._")
        return (out or "file")[:100]

    async def _mkdir_parent(self, client: OpenVikingClient, uri: str) -> None:
        parent = uri.rstrip("/").rsplit("/", 1)[0]
        parts = parent.split("/")
        for idx in range(4, len(parts)):
            candidate = "/".join(parts[: idx + 1])
            try:
                await client.mkdir(candidate)
            except Exception as exc:  # noqa: BLE001 - already exists / remote nuance
                logger.debug("ov mkdir %s: %s", candidate, exc)

    async def _enqueue_index(self, doc: dict, tenant_id: str, app_user_id: str) -> None:
        if self.jobs is None:
            return
        await self.jobs.enqueue(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            kind="kb_index",
            payload={"doc_id": doc["doc_id"], "generation": doc.get("index_generation", 0)},
            idempotency_key=f"kb_index:{doc['doc_id']}:{doc.get('index_generation', 0)}",
        )

    # --------------------------------------------------------------- worker
    async def index_document(self, doc_id: str, expected_generation: int | None = None) -> None:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None:
            return
        generation = int(doc["index_generation"])
        if expected_generation is not None and int(expected_generation) != generation:
            # A delete/reindex superseded this job before it started.
            return
        if doc["index_status"] in {"deleting", "deleted", "ready"}:
            return
        file = await self.store.get_file(doc["file_id"])
        if file is None or not file.get("storage_ref") or self.tos is None:
            await self.store.transition_kb_document(
                doc_id,
                expected_generation=generation,
                from_statuses={"pending", "failed", "reconciling", "indexing"},
                index_status="failed",
                error="file storage unavailable",
            )
            return
        claimed = await self.store.transition_kb_document(
            doc_id,
            expected_generation=generation,
            from_statuses={"pending", "failed", "reconciling"},
            index_status="indexing",
            reconcile_state="none",
            error=None,
        )
        if claimed is None:
            return
        client, _ = await self._client(
            doc["scope"],
            doc["app_user_id"],
            doc["tenant_id"],
            write=doc["scope"] == "shared",
        )
        try:
            data, content_type = await self.tos.get_bytes(file["storage_ref"])
            await self._mkdir_parent(client, doc["target_uri"])
            result = await client.import_bytes(
                file["name"],
                data,
                doc["target_uri"],
                content_type=file.get("mime") or content_type or "application/octet-stream",
                wait=False,
            )
            task_id = str((result or {}).get("task_id") or (result or {}).get("id") or "")
            updated = await self.store.transition_kb_document(
                doc_id,
                expected_generation=generation,
                from_statuses={"indexing"},
                ov_task_id=task_id or None,
                index_status="indexing",
                reconcile_state="none",
            )
            if updated is None:
                return
            if task_id:
                await self._wait_task(client, doc_id, task_id, generation)
            else:
                await self.store.transition_kb_document(
                    doc_id,
                    expected_generation=generation,
                    from_statuses={"indexing"},
                    index_status="reconciling",
                    reconcile_state="pending",
                    last_reconciled_at=datetime.now(UTC),
                )
                await self._enqueue_reconcile(doc_id, doc["tenant_id"], doc["app_user_id"], 1, generation)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb index failed doc=%s: %s", doc_id, exc)
            if import_outcome_unknown(exc):
                # The remote may have accepted the import and be about to
                # create the node.  Never claim a deterministic failure here.
                updated = await self.store.transition_kb_document(
                    doc_id,
                    expected_generation=generation,
                    from_statuses={"indexing"},
                    index_status="reconciling",
                    reconcile_state="unknown",
                    last_reconciled_at=datetime.now(UTC),
                    error=str(exc)[:500],
                )
                if updated is not None:
                    await self._enqueue_reconcile(
                        doc_id, updated["tenant_id"], updated["app_user_id"], 1, generation
                    )
            else:
                await self.store.transition_kb_document(
                    doc_id,
                    expected_generation=generation,
                    from_statuses={"indexing"},
                    index_status="failed",
                    error=str(exc)[:500],
                )

    async def _wait_task(self, client, doc_id: str, task_id: str, generation: int) -> None:
        for _ in range(5):
            try:
                task = await client.task(task_id)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.05)
                continue
            status = str((task or {}).get("status") or "").lower()
            if status in {"completed", "success", "succeeded", "done"}:
                await self.store.transition_kb_document(
                    doc_id,
                    expected_generation=generation,
                    from_statuses={"indexing", "reconciling"},
                    index_status="ready",
                    reconcile_state="confirmed",
                    indexed_at=datetime.now(UTC),
                    error=None,
                )
                return
            if status in {"failed", "error", "cancelled", "canceled"}:
                await self.store.transition_kb_document(
                    doc_id,
                    expected_generation=generation,
                    from_statuses={"indexing", "reconciling"},
                    index_status="failed",
                    error=f"ov task {status}",
                )
                return
            await asyncio.sleep(0.1)
        updated = await self.store.transition_kb_document(
            doc_id,
            expected_generation=generation,
            from_statuses={"indexing"},
            index_status="reconciling",
            reconcile_state="pending",
            reconcile_attempts=1,
            last_reconciled_at=datetime.now(UTC),
        )
        if updated is not None:
            await self._enqueue_reconcile(doc_id, updated["tenant_id"], updated["app_user_id"], 1, generation)

    async def reconcile_document(self, doc_id: str, expected_generation: int | None = None) -> None:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None:
            return
        generation = int(doc["index_generation"])
        if expected_generation is not None and int(expected_generation) != generation:
            return
        if doc["index_status"] in {"ready", "deleted", "deleting", "failed"}:
            return
        client, _ = await self._client(
            doc["scope"],
            doc["app_user_id"],
            doc["tenant_id"],
            write=doc["scope"] == "shared",
        )
        task_id = doc.get("ov_task_id")
        if task_id:
            try:
                task = await client.task(str(task_id))
                status = str((task or {}).get("status") or "").lower()
                if status in {"completed", "success", "succeeded", "done"}:
                    await self.store.transition_kb_document(
                        doc_id,
                        expected_generation=generation,
                        from_statuses={"reconciling"},
                        index_status="ready",
                        reconcile_state="confirmed",
                        indexed_at=datetime.now(UTC),
                        error=None,
                    )
                elif status in {"failed", "error", "cancelled", "canceled"}:
                    await self.store.transition_kb_document(
                        doc_id,
                        expected_generation=generation,
                        from_statuses={"reconciling"},
                        index_status="failed",
                        error=f"ov task {status}",
                    )
                else:
                    await self._bump_reconcile(doc, generation)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("ov task lookup failed doc=%s: %s", doc_id, exc)
        missing = False
        try:
            stat = await client.stat(doc["target_uri"])
            missing = not bool(stat)
        except OVError as exc:
            missing = exc.status == 404
        except Exception as exc:  # noqa: BLE001
            logger.debug("ov stat failed doc=%s: %s", doc_id, exc)
        if missing:
            # Check, bump generation and requeue in one conditional statement.
            # Doing next_kb_generation() first and then comparing against the
            # old generation can never match, leaving the doc stuck forever.
            updated = await self.store.requeue_missing_kb_document(doc_id, generation)
            if updated is not None:
                await self._enqueue_index(updated, doc["tenant_id"], doc["app_user_id"])
            return
        # A present node only proves storage, not that parsing/embedding
        # finished; never mark ready from stat alone.
        await self._bump_reconcile(doc, generation)

    async def _enqueue_reconcile(
        self,
        doc_id: str,
        tenant_id: str,
        app_user_id: str,
        generation: int,
        index_generation: int,
    ) -> None:
        if self.jobs is None:
            return
        await self.jobs.enqueue(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            kind="kb_reconcile",
            payload={"doc_id": doc_id, "generation": index_generation},
            idempotency_key=f"kb_reconcile:{doc_id}:{index_generation}:{generation}",
            run_after=datetime.now(UTC) + timedelta(seconds=60),
        )

    async def _bump_reconcile(self, doc: dict, generation: int) -> None:
        attempts = int(doc.get("reconcile_attempts") or 0) + 1
        status = "reconciling" if attempts < 5 else "failed"
        updated = await self.store.transition_kb_document(
            doc["doc_id"],
            expected_generation=generation,
            from_statuses={"reconciling", "indexing"},
            index_status=status,
            reconcile_state="unknown" if status == "reconciling" else "failed",
            reconcile_attempts=attempts,
            last_reconciled_at=datetime.now(UTC),
            error=None if status == "reconciling" else "reconcile_unknown",
        )
        if updated is not None and status == "reconciling":
            await self._enqueue_reconcile(
                doc["doc_id"], doc["tenant_id"], doc["app_user_id"], attempts + 1, generation
            )

    async def delete_document(self, doc_id: str, *, app_user_id: str, tenant_id: str, role: str) -> dict:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None or doc["tenant_id"] != tenant_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "private" and doc["app_user_id"] != app_user_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "shared" and role != "admin":
            raise GpaError("FORBIDDEN", "only admins may delete shared knowledge")
        lock_id = new_file_job_id()
        lock_prefix = doc["target_uri"].rsplit("/", 1)[0]
        dir_uris = await self._ancestor_dirs(
            scope=doc["scope"],
            app_user_id=doc["app_user_id"],
            tenant_id=tenant_id,
            dir_uri=lock_prefix,
        )
        locked = await self.store.lock_uri(
            tenant_id=tenant_id,
            scope=doc["scope"],
            uri_prefix=lock_prefix,
            owner_job_id=lock_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        if not locked:
            raise GpaError("CONFLICT", "knowledge path is being deleted", retryable=True)
        updated = await self.store.begin_kb_delete(
            doc_id=doc_id,
            reason="kb delete",
            tombstone_expires_at=datetime.now(UTC) + timedelta(days=30),
            dir_uris=dir_uris,
        )
        if updated is None:
            await self.store.release_uri_lock(tenant_id, doc["scope"], lock_prefix)
            raise GpaError("NOT_FOUND", "document not found")
        if self.jobs is not None:
            await self.jobs.enqueue(
                tenant_id=tenant_id,
                app_user_id=doc["app_user_id"],
                kind="kb_delete",
                payload={"doc_id": doc_id, "generation": updated["index_generation"]},
                idempotency_key=f"kb_delete:{doc_id}:{updated['index_generation']}",
            )
        return updated

    async def _defer_while_importing(self, doc: dict, client: OpenVikingClient) -> bool:
        """Return True when the delete must wait for a running OV import.

        OV materializes the target URI only when its asynchronous
        ``add_resource`` task finishes.  Deleting before that point makes
        ``DELETE``/``STAT`` look like a 404, which used to mark the local row
        deleted while the import kept running and later created an orphan tree
        (``verify_deleted`` below is the last-resort compensation).
        """
        task_id = str(doc.get("ov_task_id") or "")
        if not task_id:
            return False
        try:
            task = await client.task(task_id)
        except OVError as exc:
            if exc.status == 404:
                return False
            raise
        status = str((task or {}).get("status") or "").lower()
        if status in _IMPORT_TERMINAL_STATUSES:
            return False
        timeout_s = max(1.0, float(getattr(self.settings, "kb_import_timeout_s", 180)))
        poll_s = max(1.0, float(getattr(self.settings, "kb_import_poll_interval_s", 2.0)))
        started = doc.get("deleted_at") or doc.get("updated_at")
        if not isinstance(started, datetime):
            started = datetime.now(UTC)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if elapsed >= timeout_s:
            # A stuck task must not keep the row in ``deleting`` forever; the
            # scheduled verify jobs will clean up a target that lands later.
            logger.warning(
                "kb delete: import task %s still running after %.0fs doc=%s; deleting anyway",
                task_id,
                elapsed,
                doc["doc_id"],
            )
            return False
        delay = min(30.0, max(poll_s, timeout_s - elapsed))
        await self.store.release_uri_lock(doc["tenant_id"], doc["scope"], doc["target_uri"].rsplit("/", 1)[0])
        if self.jobs is not None:
            await self.jobs.enqueue(
                tenant_id=doc["tenant_id"],
                app_user_id=doc["app_user_id"],
                kind="kb_delete",
                payload={"doc_id": doc["doc_id"], "generation": doc["index_generation"]},
                idempotency_key=(
                    f"kb_delete:{doc['doc_id']}:{doc['index_generation']}:wait:"
                    f"{int(datetime.now(UTC).timestamp() // 30)}"
                ),
                run_after=datetime.now(UTC) + timedelta(seconds=delay),
            )
        logger.info(
            "kb delete deferred until OV import task %s finishes doc=%s status=%s",
            task_id,
            doc["doc_id"],
            status,
        )
        return True

    async def delete_remote(self, doc_id: str, expected_generation: int | None = None) -> None:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None:
            return
        generation = int(doc["index_generation"])
        if expected_generation is not None and int(expected_generation) != generation:
            return
        if doc["index_status"] == "deleted":
            return
        client, _ = await self._client(
            doc["scope"],
            doc["app_user_id"],
            doc["tenant_id"],
            write=doc["scope"] == "shared",
        )
        try:
            if await self._defer_while_importing(doc, client):
                return
        except Exception as exc:  # noqa: BLE001
            await self._fail_delete(doc, generation, f"import task check failed: {exc}")
            return
        try:
            await client.delete(doc["target_uri"], recursive=True)
        except OVError as exc:
            if exc.status != 404:
                await self._fail_delete(doc, generation, str(exc))
                return
        except Exception as exc:  # noqa: BLE001
            await self._fail_delete(doc, generation, str(exc))
            return
        # OV delete may be asynchronous.  The design requires a 404/stat
        # confirmation before the local state becomes ``deleted``.
        confirmed = False
        for _ in range(5):
            try:
                stat = await client.stat(doc["target_uri"])
                confirmed = not bool(stat)
            except OVError as exc:
                confirmed = exc.status == 404
            except Exception:  # noqa: BLE001
                confirmed = False
            if confirmed:
                break
            await asyncio.sleep(0.3)
        if not confirmed:
            await self._fail_delete(doc, generation, "remote delete not confirmed")
            return
        updated = await self.store.transition_kb_document(
            doc_id,
            expected_generation=generation,
            from_statuses={"deleting", "delete_failed"},
            index_status="deleted",
            deleted_at=datetime.now(UTC),
            error=None,
        )
        await self.store.release_uri_lock(doc["tenant_id"], doc["scope"], doc["target_uri"].rsplit("/", 1)[0])
        # Always leave a compensation job behind: the local transition may lose
        # a race with another worker, but a late OV import still has to be
        # cleaned up.  ``verify_deleted`` is generation-guarded.
        await self._enqueue_delete_verify(doc, generation, 0)
        if updated is not None and self.jobs is not None:
            dir_uris = await self._ancestor_dirs(
                scope=doc["scope"],
                app_user_id=doc["app_user_id"],
                tenant_id=doc["tenant_id"],
                dir_uri=doc["target_uri"].rsplit("/", 1)[0],
            )
            for dir_uri in dir_uris:
                await self.jobs.enqueue(
                    tenant_id=doc["tenant_id"],
                    app_user_id=doc["app_user_id"],
                    kind="kb_dir_refresh",
                    payload={
                        "tenant_id": doc["tenant_id"],
                        "scope": doc["scope"],
                        "dir_uri": dir_uri,
                        "app_user_id": doc["app_user_id"],
                    },
                    idempotency_key=f"kb_dir_refresh:{doc_id}:{generation}:{dir_uri}",
                )

    async def _fail_delete(self, doc: dict, generation: int, error: str) -> None:
        logger.warning("ov delete failed doc=%s: %s", doc["doc_id"], error)
        await self.store.transition_kb_document(
            doc["doc_id"],
            expected_generation=generation,
            from_statuses={"deleting", "delete_failed"},
            index_status="delete_failed",
            delete_attempts=int(doc.get("delete_attempts") or 0) + 1,
            error=error[:300],
        )
        await self.store.release_uri_lock(doc["tenant_id"], doc["scope"], doc["target_uri"].rsplit("/", 1)[0])

    async def _enqueue_delete_verify(self, doc: dict, generation: int, attempt: int) -> None:
        if self.jobs is None:
            return
        delay = (300, 1800, 7200)[min(max(0, attempt), 2)]
        await self.jobs.enqueue(
            tenant_id=doc["tenant_id"],
            app_user_id=doc["app_user_id"],
            kind="kb_delete",
            payload={
                "doc_id": doc["doc_id"],
                "generation": generation,
                "verify": True,
                "attempt": attempt,
            },
            idempotency_key=f"kb_delete_verify:{doc['doc_id']}:{generation}:{attempt}",
            run_after=datetime.now(UTC) + timedelta(seconds=delay),
        )

    async def verify_deleted(self, doc_id: str, expected_generation: int, attempt: int = 0) -> None:
        """Compensate a late OV import after the local delete completed."""
        doc = await self.store.get_kb_document(doc_id)
        if doc is None:
            return
        if int(doc["index_generation"]) != int(expected_generation):
            return
        if doc.get("index_status") != "deleted":
            return
        active = await self.store.get_kb_by_uri(doc["tenant_id"], doc["target_uri"])
        if active is not None and active["doc_id"] != doc_id:
            # A new document now owns this URI; never delete the replacement.
            return
        client, _ = await self._client(
            doc["scope"],
            doc["app_user_id"],
            doc["tenant_id"],
            write=doc["scope"] == "shared",
        )
        exists = False
        try:
            exists = bool(await client.stat(doc["target_uri"]))
        except OVError as exc:
            exists = exc.status != 404
        except Exception:  # noqa: BLE001
            exists = True
        if exists:
            try:
                await client.delete(doc["target_uri"], recursive=True)
            except OVError as exc:
                if exc.status != 404:
                    exists = True
            except Exception:  # noqa: BLE001
                exists = True
            else:
                try:
                    exists = bool(await client.stat(doc["target_uri"]))
                except OVError as exc:
                    exists = exc.status != 404
                except Exception:  # noqa: BLE001
                    exists = True
        if exists and attempt < 2:
            await self._enqueue_delete_verify(doc, expected_generation, attempt + 1)
        elif exists:
            logger.warning("late KB import still present doc=%s uri=%s", doc_id, doc["target_uri"])

    async def refresh_dir(
        self, tenant_id: str, scope: str, dir_uri: str, app_user_id: str | None = None
    ) -> None:
        if app_user_id:
            try:
                client, _ = await self._client(scope, app_user_id, tenant_id, write=scope == "shared")
                reindex = getattr(client, "reindex", None)
                if reindex is None:
                    logger.warning("ov reindex unavailable; keeping invalidation %s", dir_uri)
                    return
                await reindex(dir_uri)
            except Exception as exc:  # noqa: BLE001
                attempts = await self.store.bump_dir_invalidation(tenant_id, scope, dir_uri)
                logger.warning("kb dir refresh failed %s (attempts=%s): %s", dir_uri, attempts, exc)
                if attempts < 5 and self.jobs is not None:
                    delay = min(3600, 60 * (2 ** max(0, attempts - 1)))
                    await self.jobs.enqueue(
                        tenant_id=tenant_id,
                        app_user_id=app_user_id,
                        kind="kb_dir_refresh",
                        payload={
                            "tenant_id": tenant_id,
                            "scope": scope,
                            "dir_uri": dir_uri,
                            "app_user_id": app_user_id,
                        },
                        idempotency_key=f"kb_dir_refresh:{dir_uri}:{attempts}",
                        run_after=datetime.now(UTC) + timedelta(seconds=delay),
                    )
                return
        await self.store.resolve_dir_invalidation(tenant_id, scope, dir_uri)

    async def entries(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        path: str = "",
        query: str = "",
    ) -> dict:
        if ".." in path.split("/"):
            raise GpaError("VALIDATION_ERROR", "invalid knowledge path")
        identity = await self._identity(scope, app_user_id, tenant_id)
        root = self._root(identity, scope)
        docs = await self.store.list_kb_documents(tenant_id, app_user_id, scope)
        tombstones = await self.store.list_tombstones(tenant_id, scope)
        target = f"{root.rstrip('/')}/{path.strip('/')}" if path.strip("/") else root
        if not uri_within(target, root):
            raise GpaError("VALIDATION_ERROR", "invalid knowledge path")
        ov_items: list[dict] = []
        degraded: list[str] = []
        if query.strip():
            try:
                client, _ = await self._client(scope, app_user_id, tenant_id, write=False)
                result = await client.search(query.strip(), [root], 20, ["resource"])
                ov_items = [
                    {
                        "uri": item.get("uri"),
                        "name": item.get("name") or (item.get("uri") or "").rsplit("/", 1)[-1],
                        "is_dir": False,
                        "size": None,
                        "updated_at": None,
                    }
                    for item in (result.get("resources") or [])
                    if item.get("uri")
                ]
            except Exception:  # noqa: BLE001
                degraded.append("ov_unavailable")
        else:
            try:
                client, _ = await self._client(scope, app_user_id, tenant_id, write=False)
                listing = await client.ls(target)
                for item in listing:
                    name = str(item.get("name") or "")
                    if name in {".abstract.md", ".overview.md"}:
                        continue
                    ov_items.append(
                        {
                            "uri": item.get("uri"),
                            "name": name or str(item.get("uri") or "").rsplit("/", 1)[-1],
                            "is_dir": bool(item.get("isDir")),
                            "size": item.get("size"),
                            "updated_at": item.get("modTime") or item.get("updatedAt"),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("kb list ov failed: %s", exc)
                degraded.append("ov_unavailable")
        by_uri = {doc["target_uri"]: doc for doc in docs}
        items: list[dict] = []
        seen: set[str] = set()
        for item in ov_items:
            uri = str(item.get("uri") or "")
            if not uri or self._tombstoned(uri, tombstones):
                continue
            # ``uploads`` is the internal container for new personal uploads;
            # it is not a knowledge document and has no useful size/status.
            if (
                scope == "private"
                and not path.strip("/")
                and item.get("is_dir")
                and item.get("name") == "uploads"
            ):
                continue
            doc = by_uri.get(uri)
            if doc and doc.get("index_status") not in {
                "ready",
                "indexing",
                "reconciling",
                "pending",
                "failed",
            }:
                continue
            seen.add(uri)
            items.append(self._entry_view(item, doc, scope))
        for doc in docs:
            if doc["target_uri"] in seen or self._tombstoned(doc["target_uri"], tombstones):
                continue
            if doc.get("index_status") in {"deleting", "deleted"}:
                continue
            logical = doc.get("logical_path") or doc["target_uri"].rsplit("/", 1)[-1]
            if path.strip("/") and not logical.startswith(path.strip("/") + "/"):
                continue
            items.append(
                self._entry_view(
                    {
                        "uri": doc["target_uri"],
                        "name": logical.rsplit("/", 1)[-1],
                        "is_dir": False,
                        "size": None,
                        "updated_at": doc.get("created_at"),
                    },
                    doc,
                    scope,
                )
            )
        items.sort(key=lambda x: (not x["isDir"], x["name"].lower()))
        return {"scope": scope, "rootUri": root, "items": items, "degraded": degraded}

    def _entry_view(self, item: dict, doc: dict | None, scope: str) -> dict:
        doc = doc or {}
        return {
            "uri": item.get("uri"),
            "path": (doc.get("logical_path") or item.get("name") or ""),
            "name": item.get("name") or doc.get("file_name") or "",
            "isDir": bool(item.get("is_dir")),
            "size": doc.get("file_bytes") if doc.get("file_bytes") is not None else item.get("size"),
            "updatedAt": item.get("updated_at"),
            "docId": doc.get("doc_id"),
            "status": doc.get("index_status") or ("ready" if not doc else "unknown"),
            "scope": scope,
            "mime": doc.get("file_mime"),
            "downloadUrl": (f"/api/kb/documents/{doc['doc_id']}/download" if doc.get("doc_id") else None),
            "error": doc.get("error"),
            "actions": (
                ["preview"]
                if not doc.get("doc_id")
                else ["retry", "delete"]
                if doc.get("index_status") == "failed"
                else ["preview", "delete"]
            ),
        }

    def _tombstoned(self, uri: str, tombstones: list[dict]) -> bool:
        for item in tombstones:
            if uri == item["uri"] or (
                item.get("recursive") and uri.startswith(item["uri"].rstrip("/") + "/")
            ):
                return True
        return False

    async def content(self, *, tenant_id: str, app_user_id: str, scope: str, uri: str) -> str:
        uri = normalize_kb_uri(uri)
        identity = await self._identity(scope, app_user_id, tenant_id)
        root = self._root(identity, scope)
        if not uri_within(uri, root):
            raise GpaError("NOT_FOUND", "knowledge content not found")
        doc = await self.store.get_kb_by_uri(tenant_id, uri, include_deleted=True)
        if doc is not None:
            if doc["scope"] != scope:
                raise GpaError("NOT_FOUND", "knowledge content not found")
            if scope == "private" and doc["app_user_id"] != app_user_id:
                raise GpaError("NOT_FOUND", "knowledge content not found")
            if doc.get("deleted_at") is not None:
                raise GpaError("NOT_FOUND", "knowledge content not found")
        if not await self.guard.is_allowed(tenant_id, scope, uri):
            raise GpaError("NOT_FOUND", "knowledge content not found")
        client, _ = await self._client(scope, app_user_id, tenant_id, write=False)
        try:
            return await client.read(uri)
        except Exception as exc:  # noqa: BLE001
            raise GpaError("NOT_FOUND", "knowledge content not found") from exc

    # ------------------------------------------------------------- agent read
    async def scope_for_uri(self, *, tenant_id: str, app_user_id: str, uri: str) -> str:
        """``shared`` or ``private`` for a ``viking://`` URI the agent asked about."""
        uri = normalize_kb_uri(uri)
        if self.identity_service is None:
            raise GpaError("UPSTREAM_UNAVAILABLE", "identity unavailable")
        identity = await self._identity("private", app_user_id, tenant_id)
        layout = self._layout(identity)
        if uri_within(uri, layout.shared_root):
            return "shared"
        if uri_within(uri, layout.resources):
            return "private"
        raise GpaError("NOT_FOUND", "knowledge content not found")

    async def read_uri(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        scope: str,
        uri: str,
        offset: int = 0,
        limit: int = READ_DEFAULT_CHARS,
    ) -> dict:
        """Read what a retrieval hit actually points at.

        Retrieval only returns OV's ``abstract`` for a hit, which is a summary.
        This is the body: a document URI (an imported file folder) returns its
        section outline, a section URI returns paginated markdown.  A section
        that is only a heading (failed/partial PDF parse artifact) is reported
        as ``contentStatus="thin"`` together with its siblings, so the agent
        reads the real body instead of concluding the document has none.
        """
        uri = normalize_kb_uri(uri).rstrip("/")
        identity = await self._identity(scope, app_user_id, tenant_id)
        root = self._root(identity, scope).rstrip("/")
        if not uri_within(uri, root):
            raise GpaError("NOT_FOUND", "knowledge content not found")
        docs = await self.store.list_kb_documents(tenant_id, app_user_id, scope)
        doc = _owner_doc(uri, docs)
        if doc is not None:
            if doc.get("deleted_at") is not None:
                raise GpaError("NOT_FOUND", "knowledge content not found")
            if scope == "private" and doc.get("app_user_id") not in {None, app_user_id}:
                raise GpaError("NOT_FOUND", "knowledge content not found")
            status = str(doc.get("index_status") or "")
            if status != "ready":
                return {
                    "uri": uri,
                    "kind": "section" if _is_section(uri) else "document",
                    "status": status or "unknown",
                    "document": self._doc_ref(doc, scope),
                    "message": f"文档仍在索引中（{status or 'unknown'}），正文暂不可读；先不要断言内容缺失。",
                }
        if not await self.guard.is_allowed(tenant_id, scope, uri):
            raise GpaError("NOT_FOUND", "knowledge content not found")
        client, _ = await self._client(scope, app_user_id, tenant_id, write=False)
        if doc is not None and uri == str(doc.get("target_uri") or "").rstrip("/"):
            return await self._document_view(client, doc, uri, scope)
        return await self._section_view(
            client, doc, uri, scope, offset=offset, limit=limit, root=root
        )

    @staticmethod
    def _doc_ref(doc: dict, scope: str) -> dict:
        root = str(doc.get("target_uri") or "").rstrip("/")
        return {
            "docId": doc.get("doc_id"),
            "uri": root,
            "title": doc.get("title") or doc.get("file_name") or root.rsplit("/", 1)[-1],
            "mime": doc.get("file_mime"),
            "bytes": doc.get("file_bytes"),
            "scope": scope,
            "status": doc.get("index_status"),
        }

    async def _sections(
        self, client: OpenVikingClient, folder: str, *, depth: int = 0
    ) -> list[dict]:
        """Readable markdown sections OV produced for one imported resource.

        OV nests the parse output one level down: ``<target_uri>/<slug>/*.md``.
        A folder that only holds that wrapper directory is descended into once;
        deeper descent is refused so a KB root can never be enumerated here.
        """
        try:
            items = await client.ls(folder)
        except Exception as exc:  # noqa: BLE001 - outline is best effort
            logger.debug("kb ls %s failed: %s", folder, exc)
            return []
        sections: list[dict] = []
        nested: list[str] = []
        for item in items or []:
            section_uri = str(item.get("uri") or "").rstrip("/")
            if not section_uri:
                continue
            if item.get("isDir"):
                nested.append(section_uri)
                continue
            if not _is_section(section_uri):
                continue
            sections.append(
                {
                    "uri": section_uri,
                    "title": _section_title(section_uri),
                    "bytes": item.get("size"),
                }
            )
        if not sections and depth == 0:
            for child in nested[:3]:
                sections.extend(await self._sections(client, child, depth=1))
        sections.sort(key=lambda row: -int(row.get("bytes") or 0))
        return sections

    async def _document_view(
        self, client: OpenVikingClient, doc: dict, uri: str, scope: str
    ) -> dict:
        try:
            abstract = str(await client.abstract(uri) or "")
        except Exception:  # noqa: BLE001 - abstract is optional context
            abstract = ""
        sections = await self._sections(client, uri)
        return {
            "uri": uri,
            "kind": "document",
            "document": self._doc_ref(doc, scope),
            "abstract": abstract[:1200],
            "sections": sections,
            "instruction": (
                "这是文档目录，不是正文：用 read_knowledge 打开 sections 里的分节 uri 读取正文。"
            ),
        }

    async def _section_view(
        self,
        client: OpenVikingClient,
        doc: dict | None,
        uri: str,
        scope: str,
        *,
        offset: int,
        limit: int,
        root: str,
    ) -> dict:
        try:
            raw = str(await client.read(uri) or "")
        except Exception as exc:  # noqa: BLE001
            raise GpaError("NOT_FOUND", "knowledge content not found") from exc
        # Normalize before paginating so offsets/short-total describe what the
        # agent actually receives (raw OV text is mostly viking:// image links).
        body = _clean_markdown(raw)
        total = len(body)
        size = max(1, min(int(limit or READ_DEFAULT_CHARS), READ_MAX_CHARS))
        start = min(max(0, int(offset or 0)), total)
        end = min(total, start + size)
        payload: dict = {
            "uri": uri,
            "kind": "section",
            "title": _section_title(uri),
            "text": body[start:end],
            "offset": start,
            "nextOffset": end,
            "truncated": end < total,
            "totalChars": total,
            "document": self._doc_ref(doc, scope) if doc is not None else None,
        }
        if _heading_only(body):
            folder = (
                str(doc.get("target_uri") or "").rstrip("/")
                if doc is not None
                else uri.rsplit("/", 1)[0]
            )
            sections = (
                await self._sections(client, folder)
                if folder != root and uri_within(folder, root)
                else []
            )
            payload["contentStatus"] = "thin"
            payload["sections"] = [row for row in sections if row["uri"] != uri]
            payload["hint"] = (
                "该分节正文只有标题/署名一类的封面文字（PDF 排版被切成独立小节的常见结果），"
                "不代表整份文档没有内容：请读取同一文档其他分节（sections 中 bytes 较大的那些）"
                "再回答；不要据此说“知识库只索引到标题与作者”。"
            )
        else:
            payload["contentStatus"] = "ok"
        return payload

    async def download(
        self, doc_id: str, *, tenant_id: str, app_user_id: str, role: str
    ) -> tuple[bytes, str, str]:
        doc = await self.store.get_kb_document(doc_id)
        if (
            doc is None
            or doc["tenant_id"] != tenant_id
            or doc.get("deleted_at") is not None
            or doc.get("index_status") in {"deleted", "deleting", "delete_failed"}
        ):
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "private" and doc["app_user_id"] != app_user_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "shared" and role not in {"admin", "user"}:
            raise GpaError("FORBIDDEN", "forbidden")
        file = await self.store.get_file(doc["file_id"])
        if file is None or not file.get("storage_ref") or self.tos is None:
            raise GpaError("NOT_FOUND", "document content unavailable")
        data, content_type = await self.tos.get_bytes(file["storage_ref"])
        return (
            data,
            content_type or file.get("mime") or "application/octet-stream",
            file.get("name") or "download",
        )

    async def promote(
        self,
        file_id: str,
        *,
        tenant_id: str,
        app_user_id: str,
        role: str,
        scope: str,
        category_id: str | None,
    ):
        file = await self.store.get_file(file_id)
        if file is None or file["tenant_id"] != tenant_id or file["app_user_id"] != app_user_id:
            raise GpaError("NOT_FOUND", "file not found")
        if scope == "shared" and role != "admin":
            raise GpaError("FORBIDDEN", "only admins may promote to shared knowledge")
        if scope == "shared" and not getattr(self.settings, "gpa_kb_shared_write_enabled", True):
            raise GpaError("FORBIDDEN", "shared knowledge writes are disabled")
        existing = await self.store.find_kb_by_hash(tenant_id, app_user_id, scope, file["sha256"])
        if existing is not None and existing["index_status"] not in {"deleting", "deleted"}:
            return {"doc": existing, "dedupe": True}
        if self.tos is None or not file.get("storage_ref"):
            raise GpaError("UPSTREAM_UNAVAILABLE", "file storage unavailable", retryable=True)
        identity = await self._identity(scope, app_user_id, tenant_id, write=scope == "shared")
        target_dir = self._write_root(identity, scope, category_id)
        digest = file["sha256"]
        slug = self._slug(file["name"])
        ext = "." + safe_name(file["name"]).rsplit(".", 1)[-1] if "." in safe_name(file["name"]) else ""
        placeholder = f"{target_dir.rstrip('/')}/{slug}-{digest[:8]}-tmp{ext}"
        if await self.store.uri_locked(tenant_id, scope, placeholder):
            raise GpaError("CONFLICT", "knowledge path is being deleted", retryable=True)
        if self._client_factory is None:
            client, _ = await self._client(scope, app_user_id, tenant_id, write=scope == "shared")
            await self._mkdir_parent(client, placeholder)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            locked = await self.store.get_file_for_update(conn, file_id)
            if (
                locked is None
                or locked["tenant_id"] != tenant_id
                or locked["app_user_id"] != app_user_id
                or locked.get("deleted_at") is not None
                or locked.get("gc_state") in {"deleting", "deleted"}
            ):
                raise GpaError("CONFLICT", "file is being deleted", retryable=True)
            existing = await self.store.find_kb_by_hash(tenant_id, app_user_id, scope, digest, conn=conn)
            if existing is not None and existing["index_status"] not in {"deleting", "deleted"}:
                return {"doc": existing, "dedupe": True}
            doc = await self.store.create_kb_document_conn(
                conn,
                file_id=file_id,
                tenant_id=tenant_id,
                app_user_id=app_user_id,
                scope=scope,
                target_uri=placeholder,
                logical_path=f"{(category_id + '/') if category_id else ''}{slug}{ext}",
                content_sha256=digest,
                title=file["name"],
            )
            doc = (
                await self.store.update_kb_document_conn(
                    conn,
                    doc["doc_id"],
                    target_uri=f"{target_dir.rstrip('/')}/{slug}-{digest[:8]}-{doc['doc_id'][4:12]}{ext}",
                )
                or doc
            )
            await conn.execute(
                "UPDATE file_object SET gc_state='active', gc_candidate_at=NULL, updated_at=now()"
                " WHERE file_id=$1 AND gc_state='candidate'",
                file_id,
            )
        await self._enqueue_index(doc, tenant_id, app_user_id)
        return {"doc": doc, "dedupe": False}

    async def reindex(self, doc_id: str, *, tenant_id: str, app_user_id: str, role: str) -> dict:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None or doc["tenant_id"] != tenant_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "private" and doc["app_user_id"] != app_user_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "shared" and role != "admin":
            raise GpaError("FORBIDDEN", "only admins may reindex shared knowledge")
        if doc.get("deleted_at") is not None or doc.get("index_status") in {
            "deleted",
            "deleting",
            "delete_failed",
        }:
            raise GpaError("CONFLICT", "document is being deleted")
        generation = await self.store.next_kb_generation(doc_id)
        updated = await self.store.transition_kb_document(
            doc_id,
            expected_generation=generation,
            from_statuses={"pending", "indexing", "reconciling", "ready", "failed"},
            index_status="pending",
            reconcile_state="none",
            reconcile_attempts=0,
            error=None,
        )
        if updated is None:
            raise GpaError("CONFLICT", "document changed while reindexing", retryable=True)
        if self.jobs is not None:
            await self.jobs.enqueue(
                tenant_id=tenant_id,
                app_user_id=updated["app_user_id"],
                kind="kb_index",
                payload={"doc_id": doc_id, "generation": generation},
                idempotency_key=f"kb_index:{doc_id}:{generation}",
            )
        return updated

    async def retry_delete(self, doc_id: str, *, tenant_id: str, app_user_id: str, role: str) -> dict:
        doc = await self.store.get_kb_document(doc_id)
        if doc is None or doc["tenant_id"] != tenant_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "private" and doc["app_user_id"] != app_user_id:
            raise GpaError("NOT_FOUND", "document not found")
        if doc["scope"] == "shared" and role != "admin":
            raise GpaError("FORBIDDEN", "only admins may delete shared knowledge")
        if doc.get("index_status") != "delete_failed":
            raise GpaError("CONFLICT", "document is not pending deletion")
        lock_prefix = doc["target_uri"].rsplit("/", 1)[0]
        dir_uris = await self._ancestor_dirs(
            scope=doc["scope"],
            app_user_id=doc["app_user_id"],
            tenant_id=tenant_id,
            dir_uri=lock_prefix,
        )
        locked = await self.store.lock_uri(
            tenant_id=tenant_id,
            scope=doc["scope"],
            uri_prefix=lock_prefix,
            owner_job_id=new_file_job_id(),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        if not locked:
            raise GpaError("CONFLICT", "knowledge path is being deleted", retryable=True)
        updated = await self.store.begin_kb_delete(
            doc_id=doc_id,
            reason="kb delete retry",
            tombstone_expires_at=datetime.now(UTC) + timedelta(days=30),
            dir_uris=dir_uris,
        )
        if updated is None:
            await self.store.release_uri_lock(tenant_id, doc["scope"], lock_prefix)
            raise GpaError("NOT_FOUND", "document not found")
        if self.jobs is not None:
            await self.jobs.enqueue(
                tenant_id=tenant_id,
                app_user_id=updated["app_user_id"],
                kind="kb_delete",
                payload={"doc_id": doc_id, "generation": updated["index_generation"]},
                idempotency_key=f"kb_delete:{doc_id}:{updated['index_generation']}",
            )
        return updated

    async def deletions(self, *, tenant_id: str, app_user_id: str, role: str) -> list[dict]:
        docs = await self.store.list_kb_deletions(tenant_id, app_user_id, include_shared=role == "admin")
        return [
            self._entry_view(
                {"uri": d["target_uri"], "name": d.get("title") or "", "is_dir": False}, d, d["scope"]
            )
            for d in docs
        ]

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
