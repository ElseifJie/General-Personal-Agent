"""Expired artifact janitor: remove GPA-owned copies, keep MA source objects."""

from __future__ import annotations

import asyncio

from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)


class ArtifactJanitor:
    def __init__(self, settings, store, tos=None):
        self.settings = settings
        self.store = store
        self.tos = tos
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_removed = 0

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "gpa_artifact_cleanup_enabled", True))

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="gpa-artifact-janitor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        interval = max(60, int(getattr(self.settings, "gpa_artifact_cleanup_interval_s", 3600)))
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("artifact janitor sweep failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def run_once(self) -> int:
        rows = await self.store.expired_artifacts()
        removed = 0
        for row in rows:
            storage_ref = row.get("storage_ref") or ""
            source_ref = row.get("source_ref") or ""
            # Only delete our own copy; never delete the MA source object.
            if (
                self.tos is not None
                and storage_ref
                and storage_ref != source_ref
                and storage_ref.startswith(f"{self.tos.prefix}/")
            ):
                try:
                    await self.tos.delete(storage_ref)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("artifact delete failed key=%s: %s", storage_ref, exc)
                    continue
            await self.store.delete_artifact(row["artifact_id"])
            removed += 1
        self.last_removed = removed
        return removed
