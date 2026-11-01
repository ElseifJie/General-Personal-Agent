"""GC deleting leases and same-content re-upload must not conflict."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FlakyTOS:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_delete = False

    async def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = data
        return key

    async def get_bytes(self, key):
        return self.objects[key], "application/octet-stream"

    async def delete(self, key):
        if self.fail_delete:
            raise RuntimeError("tos unavailable")
        self.objects.pop(key, None)


@pytest.mark.integration
async def test_gc_failure_keeps_logical_delete_and_retries(gpa_env):
    env = gpa_env
    tos = FlakyTOS()
    env.gpa.files.tos = tos
    record, _ = await env.gpa.files.store_file(
        tenant_id=env.tenant, app_user_id=env.user,
        name="gc.md", mime="text/markdown", data=b"same-content", purpose="conversation",
    )
    file_id = record["file_id"]
    await env.db.pool.execute(
        "UPDATE file_object SET gc_state='candidate',"
        " gc_candidate_at=now() - interval '9 days' WHERE file_id=$1",
        file_id,
    )
    tos.fail_delete = True
    await env.gpa.files.gc_once()
    failed = await env.db.pool.fetchrow(
        "SELECT gc_state, deleted_at, gc_lease_until, gc_attempts FROM file_object"
        " WHERE file_id=$1", file_id,
    )
    assert failed["gc_state"] == "deleting"
    assert failed["deleted_at"] is not None
    assert failed["gc_lease_until"] is None
    assert failed["gc_attempts"] == 1

    # A same-content re-upload must be able to insert a new row while the old
    # row remains logically deleted.
    replacement, created = await env.gpa.files.store_file(
        tenant_id=env.tenant, app_user_id=env.user,
        name="gc-again.md", mime="text/markdown", data=b"same-content", purpose="conversation",
    )
    assert created is True
    assert replacement["file_id"] != file_id

    tos.fail_delete = False
    await env.gpa.files.gc_once()
    deleted = await env.db.pool.fetchrow(
        "SELECT gc_state FROM file_object WHERE file_id=$1", file_id
    )
    assert deleted["gc_state"] == "deleted"


@pytest.mark.integration
async def test_stale_deleting_claim_is_reclaimed(gpa_env):
    env = gpa_env
    record, _ = await env.gpa.files.store_file(
        tenant_id=env.tenant, app_user_id=env.user,
        name="lease.md", mime="text/markdown", data=b"lease-content", purpose="conversation",
    )
    file_id = record["file_id"]
    await env.db.pool.execute(
        "UPDATE file_object SET gc_state='candidate',"
        " gc_candidate_at=now() - interval '9 days' WHERE file_id=$1",
        file_id,
    )
    first = await env.gpa.files.store.claim_gc_candidates(7, limit=10)
    assert any(row["file_id"] == file_id for row in first)
    await env.db.pool.execute(
        "UPDATE file_object SET gc_lease_until=now() - interval '1 second'"
        " WHERE file_id=$1", file_id,
    )
    second = await env.gpa.files.store.claim_gc_candidates(7, limit=10)
    reclaimed = next(row for row in second if row["file_id"] == file_id)
    assert reclaimed["gc_attempts"] == 2
