"""A2A outbound executor contract test (in-memory JSON-RPC transport)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from general_personal_agent.gpa.task.a2a import A2aExecutor

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeTOS:
    prefix = "general-personal-agent"

    def __init__(self):
        self.puts: list[tuple[str, bytes]] = []

    def object_key(self, *parts):
        return "/".join(["general-personal-agent", *map(str, parts)])

    async def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.puts.append((key, data))
        return key


def handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload["method"]
    if method == "tasks/send":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"id": "remote-1", "status": {"state": "working"}},
            },
        )
    if method == "tasks/get":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "id": "remote-1",
                    "status": {
                        "state": "completed",
                        "message": {"parts": [{"type": "text", "text": "A2A 完成"}]},
                    },
                    "artifacts": [
                        {
                            "artifactId": "a1",
                            "name": "a2a.txt",
                            "parts": [
                                {
                                    "type": "file",
                                    "file": {
                                        "name": "a2a.txt",
                                        "mimeType": "text/plain",
                                        "bytes": base64.b64encode(b"a2a artifact").decode(),
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
        )
    return httpx.Response(404)


async def test_a2a_executor_completes_and_stores_artifact(gpa_env):
    env = gpa_env
    env.settings.a2a_enabled = True
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="A2A", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="委托 A2A",
        task_type="general",
        brief={"a2a_url": "http://a2a.test/rpc"},
        executor="a2a",
    )
    settings = env.settings.model_copy(
        update={
            "a2a_agent_url": "http://a2a.test/rpc",
            "a2a_allowed_hosts": "a2a.test",
            "a2a_poll_interval_s": 0.5,
        }
    )
    tos = FakeTOS()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = A2aExecutor(settings, env.task_store, tos=tos, http=client)
    assert await executor.run_once() is True
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"
    assert "A2A 完成" in view["summary"]
    assert view["artifacts"]
    assert tos.puts and tos.puts[0][0].startswith("general-personal-agent/tasks/")
    await client.aclose()


async def test_a2a_executor_rejects_private_brief_url(gpa_env):
    """A task-chosen URL must not turn the executor into an SSRF proxy."""
    env = gpa_env
    env.settings.a2a_enabled = True
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="A2A SSRF", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="委托 A2A",
        task_type="general",
        brief={"a2a_url": "http://169.254.169.254/latest/meta-data/"},
        executor="a2a",
    )
    settings = env.settings.model_copy(
        update={"a2a_agent_url": "http://a2a.test/rpc", "a2a_allowed_hosts": "a2a.test"}
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = A2aExecutor(settings, env.task_store, tos=FakeTOS(), http=client)
    assert await executor.run_once() is True
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "failed"
    assert "169.254.169.254" in executor.last_error
    await client.aclose()
