"""Read guard for deleted or not-yet-indexed knowledge-base URIs."""

from __future__ import annotations

from collections.abc import Iterable


class KbReadGuard:
    def __init__(self, store):
        self.store = store

    async def is_allowed(
        self,
        tenant_id: str,
        scope: str,
        uri: str,
        *,
        allow_invalidated_dirs: bool = False,
    ) -> bool:
        tombstones = await self.store.list_tombstones(tenant_id, scope)
        for item in tombstones:
            if uri == item["uri"] or (
                item.get("recursive") and uri.startswith(item["uri"].rstrip("/") + "/")
            ):
                return False
        doc = await self.store.get_kb_by_uri(tenant_id, uri)
        if doc is not None:
            if doc.get("deleted_at") is not None:
                return False
            return doc.get("index_status") == "ready" and doc.get("reconcile_state") in {"none", "confirmed"}
        # An ancestor directory summary is unsafe while a descendant delete is
        # being rebuilt.  Retrieval keeps the whole directory hidden; the
        # context browser may keep the directory node visible (flag) so
        # ``buildContextTree`` does not re-attach its children to ``resources``.
        invalidations = await self.store.list_open_invalidations(tenant_id, scope)
        for item in invalidations:
            dir_uri = item["dir_uri"].rstrip("/")
            if uri == dir_uri:
                if not allow_invalidated_dirs:
                    return False
            elif uri.startswith(dir_uri + "/") and (
                uri.endswith(".abstract.md") or uri.endswith(".overview.md")
            ):
                return False
        return True

    async def filter_uris(self, tenant_id: str, scope: str, uris: Iterable[str]) -> set[str]:
        """Batch visibility check (one tombstone/doc/invalidation query)."""
        candidates = list(dict.fromkeys(uris))
        if not candidates:
            return set()
        tombstones = await self.store.list_tombstones(tenant_id, scope)
        docs = await self.store.get_kb_by_uris(tenant_id, candidates)
        invalidations = await self.store.list_open_invalidations(tenant_id, scope)
        allowed: set[str] = set()
        for uri in candidates:
            if any(
                uri == item["uri"]
                or (item.get("recursive") and uri.startswith(item["uri"].rstrip("/") + "/"))
                for item in tombstones
            ):
                continue
            doc = docs.get(uri)
            if doc is not None:
                if doc.get("deleted_at") is not None:
                    continue
                if not (
                    doc.get("index_status") == "ready" and doc.get("reconcile_state") in {"none", "confirmed"}
                ):
                    continue
                allowed.add(uri)
                continue
            blocked = False
            for item in invalidations:
                dir_uri = item["dir_uri"].rstrip("/")
                if uri == dir_uri or uri.startswith(dir_uri + "/"):
                    if uri == dir_uri or uri.endswith(".abstract.md") or uri.endswith(".overview.md"):
                        blocked = True
                        break
            if not blocked:
                allowed.add(uri)
        return allowed
