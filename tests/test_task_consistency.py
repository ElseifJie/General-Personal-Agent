"""Task submit idempotency, atomic inputs and terminal-state protection."""

from __future__ import annotations

import pytest

from general_personal_agent.gpa.errors import GpaError

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.integration
async def test_idempotency_fingerprint_covers_brief_type_and_executor(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="idem", channel="text"
    )
    cid = conversation["conversation_id"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="做海报",
        task_type="image",
        brief={"facts": ["a"]},
        idempotency_key="same-key",
    )
    stored = await env.task_store.get(task["task_id"])
    assert stored["payload_fingerprint"]

    with pytest.raises(GpaError) as caught:
        await env.tasks.submit(
            tenant_id=env.tenant,
            app_user_id=env.user,
            conversation_id=cid,
            goal="做海报",
            task_type="image",
            brief={"facts": ["b"]},
            idempotency_key="same-key",
        )
    assert caught.value.code == "IDEMPOTENCY_MISMATCH"

    with pytest.raises(GpaError):
        await env.tasks.submit(
            tenant_id=env.tenant,
            app_user_id=env.user,
            conversation_id=cid,
            goal="做海报",
            task_type="video",
            brief={"facts": ["a"]},
            idempotency_key="same-key",
        )


@pytest.mark.integration
async def test_finish_cannot_overwrite_cancelled_task(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="cancel", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="写报告",
        task_type="report",
        idempotency_key="cancel-key",
    )
    await env.tasks.cancel(task["task_id"], env.tenant, env.user, reason="用户取消")
    finished = await env.task_store.finish(
        task["task_id"],
        status="completed",
        progress=1.0,
        summary="不应写入",
        artifacts=[{"name": "late.txt", "mime": "text/plain", "bytes": 1}],
    )
    assert finished is False
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "cancelled"
    assert view["artifacts"] == []
    assert view["summary"] != "不应写入"


@pytest.mark.integration
async def test_finish_uses_owner_lease(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="lease", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="写报告",
        task_type="report",
        idempotency_key="lease-key",
    )
    claimed = await env.task_store.claim_next_ma("owner-a")
    assert claimed and claimed["task"]["task_id"] == task["task_id"]
    assert await env.task_store.heartbeat(task["task_id"], "owner-b") is False
    assert await env.task_store.finish(
        task["task_id"], status="completed", progress=1.0, summary="ok",
        owner_id="owner-b",
    ) is False
    assert await env.task_store.finish(
        task["task_id"], status="completed", progress=1.0, summary="ok",
        owner_id="owner-a",
    ) is True


@pytest.mark.integration
async def test_idempotency_key_cannot_cross_conversations(gpa_env):
    env = gpa_env
    first = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="A", channel="text"
    )
    second = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="B", channel="text"
    )
    await env.tasks.submit(
        tenant_id=env.tenant, app_user_id=env.user,
        conversation_id=first["conversation_id"],
        goal="同一目标", task_type="report", brief={"facts": ["x"]},
        idempotency_key="cross-conversation",
    )
    with pytest.raises(GpaError) as caught:
        await env.tasks.submit(
            tenant_id=env.tenant, app_user_id=env.user,
            conversation_id=second["conversation_id"],
            goal="同一目标", task_type="report", brief={"facts": ["x"]},
            idempotency_key="cross-conversation",
        )
    assert caught.value.code == "IDEMPOTENCY_MISMATCH"


@pytest.mark.integration
async def test_a2a_rejects_attachments_before_submit(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="a2a", channel="text"
    )
    uploaded = await env.gpa.files.upload_conversation(
        tenant_id=env.tenant, app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        name="a2a.md", mime="text/markdown", data=b"# a2a",
    )
    env.settings.a2a_enabled = True
    with pytest.raises(GpaError) as caught:
        await env.tasks.submit(
            tenant_id=env.tenant, app_user_id=env.user,
            conversation_id=conversation["conversation_id"],
            goal="send file", task_type="general", executor="a2a",
            idempotency_key="a2a-attachment",
            attachment_ids=[uploaded["file"]["file_id"]],
        )
    assert caught.value.code == "VALIDATION_ERROR"
