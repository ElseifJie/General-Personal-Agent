"""Real-Postgres file_job lease reclaim and claim tokens."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.integration
async def test_expired_file_job_lease_is_reclaimed_with_new_token(gpa_env):
    env = gpa_env
    await env.gpa.files.jobs.enqueue(
        tenant_id=env.tenant, app_user_id=env.user, kind="file_gc",
        payload={}, idempotency_key="lease-gc-1",
    )
    first = await env.gpa.files.store.claim_jobs(10)
    assert len(first) == 1
    job = first[0]
    assert job["claim_token"]
    await env.db.pool.execute(
        "UPDATE file_job SET lease_until=now() - interval '1 second' WHERE job_id=$1",
        job["job_id"],
    )
    reclaimed = await env.gpa.files.store.claim_jobs(10)
    assert len(reclaimed) == 1
    assert reclaimed[0]["job_id"] == job["job_id"]
    assert reclaimed[0]["claim_token"] != job["claim_token"]
    assert await env.gpa.files.store.complete_job(job["job_id"], job["claim_token"]) is False
    assert await env.gpa.files.store.complete_job(
        job["job_id"], reclaimed[0]["claim_token"]
    ) is True
