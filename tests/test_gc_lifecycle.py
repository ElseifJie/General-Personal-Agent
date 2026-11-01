"""GC / URI lock / read guard lifecycle tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from general_personal_agent.gpa.files.guard import KbReadGuard

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeTOS:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    async def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = (data, content_type)
        return key

    async def get_bytes(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.mark.integration
async def test_gc_only_deletes_after_candidate_retention(gpa_env):
    env = gpa_env
    tos = FakeTOS()
    env.gpa.files.tos = tos
    record, _ = await env.gpa.files.store_file(
        tenant_id=env.tenant, app_user_id=env.user,
        name="orphan.txt", mime="text/plain", data=b"orphan", purpose="conversation",
    )
    file_id = record["file_id"]
    await env.gpa.files.store.reconcile_file(file_id)
    assert (await env.gpa.files.store.get_file(file_id))["gc_state"] == "candidate"
    # Not old enough: GC must keep it.
    assert await env.gpa.files.gc_once() == 0
    await env.db.pool.execute(
        "UPDATE file_object SET gc_candidate_at=now() - interval '8 days' WHERE file_id=$1",
        file_id,
    )
    assert await env.gpa.files.gc_once() == 1
    assert (await env.gpa.files.store.get_file(file_id, include_deleted=True))["gc_state"] == "deleted"
    assert tos.deleted


@pytest.mark.integration
async def test_uri_lock_blocks_until_released(gpa_env):
    env = gpa_env
    store = env.gpa.files.store
    uri = f"viking://user/{env.user}/resources/uploads/a.md"
    assert await store.lock_uri(
        tenant_id=env.tenant, scope="private", uri_prefix=uri,
        owner_job_id="job-1", expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert await store.uri_locked(env.tenant, "private", uri)
    await store.release_uri_lock(env.tenant, "private", uri)
    assert not await store.uri_locked(env.tenant, "private", uri)


@pytest.mark.integration
async def test_tombstone_hides_document_from_guard(gpa_env):
    env = gpa_env
    tos = FakeTOS()
    env.gpa.files.tos = tos
    record, _ = await env.gpa.files.store_file(
        tenant_id=env.tenant, app_user_id=env.user,
        name="gone.md", mime="text/markdown", data=b"gone", purpose="private_kb",
    )
    doc = await env.gpa.files.store.create_kb_document(
        file_id=record["file_id"], tenant_id=env.tenant, app_user_id=env.user,
        scope="private", target_uri=f"viking://user/{env.user}/resources/uploads/gone.md",
        logical_path="uploads/gone.md", content_sha256=record["sha256"], title="gone.md",
    )
    await env.gpa.files.store.update_kb_document(doc["doc_id"], index_status="ready", reconcile_state="confirmed")
    guard = KbReadGuard(env.gpa.files.store)
    assert await guard.is_allowed(env.tenant, "private", doc["target_uri"])
    await env.gpa.files.store.insert_tombstone(
        tenant_id=env.tenant, app_user_id=env.user, scope="private",
        uri=doc["target_uri"], recursive=False, reason="test",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert not await guard.is_allowed(env.tenant, "private", doc["target_uri"])
