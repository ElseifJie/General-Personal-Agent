"""Concurrency regressions that sequential tests cannot catch."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.integration
async def test_attachment_submits_use_one_pool_connection_each(gpa_env):
    env = gpa_env
    pairs = []
    for i in range(8):
        conversation = await env.conversations.create(
            tenant_id=env.tenant, app_user_id=env.user,
            title=f"concurrent-{i}", channel="text",
        )
        uploaded = await env.gpa.files.upload_conversation(
            tenant_id=env.tenant, app_user_id=env.user,
            conversation_id=conversation["conversation_id"],
            name=f"a{i}.md", mime="text/markdown",
            data=f"# file {i}\n".encode(),
        )
        pairs.append((conversation["conversation_id"], uploaded["conversation_file"]["conversation_file_id"]))

    async def submit(index: int):
        cid, cf_id = pairs[index]
        return await env.conversations.submit_text(
            cid, env.tenant, env.user,
            content=f"hello {index}",
            client_submission_id=f"c-{index}",
            attachment_ids=[cf_id],
        )

    results = await asyncio.wait_for(
        asyncio.gather(*(submit(i) for i in range(len(pairs)))), timeout=20
    )
    assert len(results) == 8
    assert all(item["status"] == "running" for item in results)
