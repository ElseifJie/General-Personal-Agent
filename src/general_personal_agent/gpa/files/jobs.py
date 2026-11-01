"""Persistent file jobs: extraction, KB indexing, reconciliation, GC."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from general_personal_agent.gpa.files.extract import extract_text, split_chunks
from general_personal_agent.gpa.files.store import FileStore

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class FileJobQueue:
    def __init__(self, store: FileStore):
        self.store = store

    async def enqueue(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        kind: str,
        payload: dict,
        idempotency_key: str,
        run_after=None,
    ) -> bool:
        return await self.store.enqueue_job(
            tenant_id=tenant_id,
            app_user_id=app_user_id,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            run_after=run_after,
        )


class FileJobWorker:
    def __init__(self, settings, store: FileStore, tos, handlers: dict[str, Handler] | None = None):
        self.settings = settings
        self.store = store
        self.tos = tos
        self._handlers = handlers or {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_gc_hour: str | None = None
        self._last_reconcile_sweep = 0.0
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "gpa_files_enabled", True))

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="gpa-file-job-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        for handler in self._handlers.values():
            close = getattr(handler, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    logger.debug("file job handler close failed", exc_info=True)

    async def _run(self) -> None:
        interval = max(0.2, float(getattr(self.settings, "gpa_file_job_interval_s", 1.0)))
        while not self._stop.is_set():
            try:
                await self._schedule_gc()
                await self._schedule_reconcile()
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("file job sweep failed")
                processed = 0
            if not processed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

    async def _schedule_gc(self) -> None:
        key = datetime.now(UTC).strftime("%Y%m%d%H")
        if self._last_gc_hour == key:
            return
        self._last_gc_hour = key
        await self.store.enqueue_job(
            tenant_id="system",
            app_user_id="system",
            kind="file_gc",
            payload={},
            idempotency_key=f"file_gc:{key}",
        )

    async def _schedule_reconcile(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_reconcile_sweep < 120:
            return
        self._last_reconcile_sweep = now
        docs = await self.store.list_reconciling_documents(20)
        bucket = int(now // 120)
        for doc in docs:
            await self.store.enqueue_job(
                tenant_id=doc["tenant_id"],
                app_user_id=doc["app_user_id"],
                kind="kb_reconcile",
                payload={"doc_id": doc["doc_id"], "generation": doc["index_generation"]},
                idempotency_key=f"kb_reconcile:{doc['doc_id']}:sweep:{bucket}",
            )
        # A crash between "status=pending/indexing" and the job insert used to
        # leave a document stuck forever.  Re-enqueue a bounded retry.
        for doc in await self.store.list_deleting_kb_documents(older_than_s=120, limit=20):
            await self.store.enqueue_job(
                tenant_id=doc["tenant_id"],
                app_user_id=doc["app_user_id"],
                kind="kb_delete",
                payload={"doc_id": doc["doc_id"], "generation": doc["index_generation"]},
                idempotency_key=f"kb_delete:{doc['doc_id']}:{doc['index_generation']}:sweep:{bucket}",
            )
        # A late OV import can materialize the target after the local row is
        # already ``deleted``.  Re-check recently deleted docs once an hour.
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        for doc in await self.store.list_deleted_kb_documents(older_than_s=300, limit=10):
            await self.store.enqueue_job(
                tenant_id=doc["tenant_id"],
                app_user_id=doc["app_user_id"],
                kind="kb_delete",
                payload={
                    "doc_id": doc["doc_id"],
                    "generation": doc["index_generation"],
                    "verify": True,
                    "attempt": 0,
                },
                idempotency_key=(f"kb_delete_verify_sweep:{doc['doc_id']}:{doc['index_generation']}:{hour}"),
            )
        for doc in await self.store.list_stalled_kb_documents(older_than_s=300, limit=20):
            if doc["index_status"] == "indexing":
                # A crashed worker left the row in indexing.  Reset it so the
                # recovery job can claim it; a live duplicate job has the same
                # generation and will simply lose the conditional transition.
                reset = await self.store.transition_kb_document(
                    doc["doc_id"],
                    expected_generation=doc["index_generation"],
                    from_statuses={"indexing"},
                    index_status="pending",
                )
                if reset is None:
                    continue
            await self.store.enqueue_job(
                tenant_id=doc["tenant_id"],
                app_user_id=doc["app_user_id"],
                kind="kb_index",
                payload={"doc_id": doc["doc_id"], "generation": doc["index_generation"]},
                idempotency_key=f"kb_index:{doc['doc_id']}:{doc['index_generation']}:recover:{bucket}",
            )

    async def run_once(self) -> int:
        batch = max(1, int(getattr(self.settings, "gpa_file_job_batch_size", 10)))
        jobs = await self.store.claim_jobs(batch)
        for job in jobs:
            token = job.get("claim_token")
            handler = self._handlers.get(job["type"])
            if handler is None:
                await self.store.fail_job(job["job_id"], f"no handler for {job['type']}", claim_token=token)
                continue
            try:
                await handler(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.warning("file job %s (%s) failed: %s", job["job_id"], job["type"], exc)
                await self.store.fail_job(job["job_id"], str(exc), claim_token=token)
            else:
                await self.store.complete_job(job["job_id"], claim_token=token)
        return len(jobs)


def _limit_parse_memory(megabytes: int) -> None:
    """Best-effort per-worker address-space cap (Linux/macOS)."""
    try:
        import resource

        limit = max(128, int(megabytes)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:  # noqa: BLE001 - unsupported platform or sandbox
        pass


def _parse_child(conn, filename: str, mime: str | None, data: bytes, memory_mb: int) -> None:
    try:
        _limit_parse_memory(memory_mb)
        result = extract_text(filename, mime, data)
        conn.send(("ok", result))
    except BaseException as exc:  # noqa: BLE001 - report even MemoryError
        try:
            conn.send(("error", repr(exc)[:500]))
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


class _KillableParsePool:
    """One short-lived process per parse, so a timeout can really stop work."""

    def __init__(self, workers: int, memory_mb: int = 512):
        self.workers = max(1, int(workers))
        self.memory_mb = max(128, int(memory_mb))
        self._sem = asyncio.Semaphore(self.workers)
        self._ctx = mp.get_context("spawn")

    async def run(self, filename: str, mime: str | None, data: bytes, timeout_s: float):
        async with self._sem:
            parent, child = self._ctx.Pipe(duplex=False)
            proc = self._ctx.Process(
                target=_parse_child,
                args=(child, filename, mime, data, self.memory_mb),
                daemon=True,
            )
            proc.start()
            child.close()

            async def read_result():
                ready = await asyncio.to_thread(parent.poll, timeout_s + 5)
                if not ready:
                    raise TimeoutError("parse worker produced no result")
                return await asyncio.to_thread(parent.recv)

            reader = asyncio.create_task(read_result())
            try:
                await asyncio.to_thread(proc.join, timeout_s)
                if proc.is_alive():
                    raise TimeoutError(f"parse exceeded {timeout_s:g}s")
                status, payload = await reader
                if status != "ok":
                    raise RuntimeError(str(payload))
                return payload
            finally:
                if proc.is_alive():
                    proc.terminate()
                    await asyncio.to_thread(proc.join, 5)
                    if proc.is_alive():
                        proc.kill()
                        await asyncio.to_thread(proc.join, 5)
                parent.close()
                if not reader.done():
                    reader.cancel()

    async def close(self) -> None:
        return None


class ExtractHandler:
    """Parse uploads off the event loop with a bounded process pool."""

    def __init__(self, store: FileStore, tos, settings=None):
        self.store = store
        self.tos = tos
        self.settings = settings
        self._pool: _KillableParsePool | None = None

    def _ensure_pool(self) -> _KillableParsePool | None:
        workers = int(getattr(self.settings, "gpa_file_extract_workers", 0) or 0) if self.settings else 0
        if workers <= 0:
            return None
        if self._pool is None:
            memory_mb = int(getattr(self.settings, "gpa_file_extract_memory_mb", 512) or 512)
            self._pool = _KillableParsePool(workers, memory_mb=memory_mb)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _extract(self, name: str, mime: str | None, data: bytes):
        timeout_s = (
            float(getattr(self.settings, "gpa_file_extract_timeout_s", 120) or 120)
            if self.settings
            else 120.0
        )
        pool = self._ensure_pool()
        if pool is not None:
            return await pool.run(name, mime, data, timeout_s)
        return await asyncio.wait_for(asyncio.to_thread(extract_text, name, mime, data), timeout=timeout_s)

    async def __call__(self, job: dict[str, Any]) -> None:
        file_id = str(job.get("payload", {}).get("file_id") or "")
        if not file_id:
            raise ValueError("extract job without file_id")
        file = await self.store.get_file(file_id)
        if file is None:
            return
        await self.store.update_file(file_id, status="extracting", text_status="extracting")
        if not file.get("storage_ref") or self.tos is None:
            await self.store.update_file(
                file_id,
                status="ready",
                text_status="skipped",
                error="no storage reference",
                text_chars=0,
            )
            return
        data, _content_type = await self.tos.get_bytes(file["storage_ref"])
        result = await self._extract(file.get("name") or "file", file.get("mime"), data)
        if result.text:
            chunks = split_chunks(result.text)
            await self.store.replace_chunks(file_id, chunks)
            await self.store.update_file(
                file_id,
                status="ready" if not result.error else "ready",
                text_status="ready",
                text_chars=len(result.text),
                text_truncated=bool(result.truncated),
                page_count=result.pages,
                error=result.error,
            )
            return
        await self.store.update_file(
            file_id,
            status="ready",
            text_status="skipped" if result.skipped else "failed",
            error=result.error or "no text extracted",
            page_count=result.pages,
        )
