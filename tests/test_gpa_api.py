"""GPA conversation HTTP + SSE contract tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from general_personal_agent.api.middleware import RequestContextMiddleware, install_error_handlers
from general_personal_agent.auth import sign_session
from general_personal_agent.gpa.api import conversations_router, files_router, tasks_router
from general_personal_agent.gpa.errors import install_gpa_error_handlers

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeTOS:
    async def iter_bytes(self, key):
        yield b"0123456789abcdef"

    async def iter_range(self, key, start, end):
        data = b"0123456789abcdef"
        yield data[start : end + 1]


@pytest_asyncio.fixture(loop_scope="session")
async def api_client(gpa_env):
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    install_gpa_error_handlers(app)
    app.include_router(conversations_router)
    app.include_router(tasks_router)
    app.include_router(files_router)
    app.state.services = SimpleNamespace(settings=gpa_env.settings, db=gpa_env.db, tos=_FakeTOS())
    app.state.gpa = gpa_env.gpa
    gpa_env.api_app = app
    token = sign_session(gpa_env.user, "user", gpa_env.settings.app_secret_key, 3600)
    headers = {"Cookie": f"{gpa_env.settings.session_cookie_name}={token}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        yield client, gpa_env


async def _wait_turn(client, cid: str, turn_id: str, timeout: float = 8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/conversations/{cid}")
        assert response.status_code == 200
        for turn in response.json()["turns"]:
            if turn["turnId"] == turn_id and turn["status"] != "running":
                return turn
        await asyncio.sleep(0.02)
    raise AssertionError("turn did not settle")


@pytest.mark.integration
async def test_conversation_api_flow(api_client):
    client, env = api_client
    response = await client.post("/api/conversations", json={"title": "API", "channel": "text"})
    assert response.status_code == 200
    cid = response.json()["conversationId"]
    assert response.json()["activeChannel"] == "text"
    audit_count = await env.db.pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE app_user_id = $1 AND action = 'conversation.create'",
        env.user,
    )
    assert audit_count == 1

    response = await client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "你好", "clientSubmissionId": "api-1"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["eventsUrl"] == f"/api/conversations/{cid}/events"
    turn = await _wait_turn(client, cid, body["turnId"])
    assert turn["status"] == "completed"

    response = await client.get(f"/api/conversations/{cid}")
    assert len(response.json()["turns"]) == 1
    response = await client.get(f"/api/conversations/{cid}/timeline")
    assert response.json()["items"][0]["kind"] == "turn"
    response = await client.get(f"/api/conversations/{cid}/context-traces")
    assert response.status_code == 200
    traces = response.json()
    assert len(traces) == 1 and traces[0]["turnId"] == body["turnId"]
    response = await client.get("/api/conversations?channel=text")
    assert any(item["conversationId"] == cid for item in response.json()["items"])
    response = await client.get("/api/conversations?channel=voice")
    assert not any(item["conversationId"] == cid for item in response.json()["items"])


@pytest.mark.integration
async def test_sse_replay_contract_and_persisted_events(api_client):
    """SSE frames are stable; replay is sourced from conversation_event.

    ASGITransport buffers streaming responses, so the live endpoint is exercised
    against uvicorn in the dev smoke (``curl -N``); here we pin the frame shape
    and the replay query the endpoint uses.
    """

    from general_personal_agent.gpa.api.conversations import _frame

    frame = _frame("evt_1", "message.final", {"text": "hi"})
    assert frame.startswith("id: evt_1\nevent: message.final\ndata: ")
    assert frame.endswith("\n\n")

    client, env = api_client
    cid = (await client.post("/api/conversations", json={"title": "SSE", "channel": "text"})).json()[
        "conversationId"
    ]
    turn_id = (await client.post(f"/api/conversations/{cid}/messages", json={"content": "流式测试"})).json()[
        "turnId"
    ]
    await _wait_turn(client, cid, turn_id)

    all_events = await env.store.list_events(cid)
    assert [event["type"] for event in all_events] == [
        "turn.started",
        "message.final",
        "turn.settled",
    ]
    assert all(event["seq"] > 0 for event in all_events)
    after_start = await env.store.list_events(cid, after_seq=all_events[0]["seq"], turn_id=turn_id)
    assert "turn.started" not in [event["type"] for event in after_start]
    assert after_start[-1]["type"] == "turn.settled"
    assert await env.store.get_event_seq(all_events[-1]["event_id"], cid) == all_events[-1]["seq"]


@pytest.mark.integration
async def test_api_auth_and_cross_user(api_client, gpa_env):
    client, env = api_client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.api_app), base_url="http://testserver"
    ) as anon:
        response = await anon.get("/api/conversations")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"

    cid = (await client.post("/api/conversations", json={"title": "private", "channel": "text"})).json()[
        "conversationId"
    ]
    token = sign_session("bob", "user", env.settings.app_secret_key, 3600)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.api_app),
        base_url="http://testserver",
        headers={"Cookie": f"{env.settings.session_cookie_name}={token}"},
    ) as other:
        response = await other.get(f"/api/conversations/{cid}")
    # bob exists but the conversation is not theirs.
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_task_api(api_client):
    client, env = api_client
    cid = (await client.post("/api/conversations", json={"title": "task api", "channel": "text"})).json()[
        "conversationId"
    ]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="生成周报",
        task_type="report",
    )
    response = await client.get(f"/api/tasks/{task['task_id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    response = await client.get(f"/api/tasks/{task['task_id']}/artifacts")
    assert response.status_code == 200
    assert response.json() == []
    artifact = await env.task_store.add_artifact(
        task_id=task["task_id"],
        name="report.md",
        mime="text/markdown",
        bytes=12,
        storage_ref="general-personal-agent/report.md",
    )
    response = await client.get("/api/files")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["artifactId"] == artifact["artifact_id"]
    assert items[0]["conversationId"] == cid
    assert items[0]["conversationTitle"] == "task api"
    assert items[0]["downloadUrl"].endswith("/download")
    response = await client.post(f"/api/tasks/{task['task_id']}/cancel", json={"reason": "test"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.integration
async def test_delete_file(api_client):
    client, env = api_client
    cid = (await client.post("/api/conversations", json={"title": "delete file", "channel": "text"})).json()[
        "conversationId"
    ]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="删除过期产物",
        task_type="report",
    )
    artifact = await env.task_store.add_artifact(
        task_id=task["task_id"],
        name="obsolete.md",
        mime="text/markdown",
        bytes=8,
        storage_ref="general-personal-agent/obsolete.md",
    )

    response = await client.delete(f"/api/files/{artifact['artifact_id']}")
    assert response.status_code == 204
    assert response.content == b""

    response = await client.get("/api/files")
    assert response.status_code == 200
    assert all(item["artifactId"] != artifact["artifact_id"] for item in response.json()["items"])
    response = await client.get(f"/api/tasks/{task['task_id']}/artifacts")
    assert response.status_code == 200
    assert response.json() == []

    response = await client.delete(f"/api/files/{artifact['artifact_id']}")
    assert response.status_code == 404
    count = await env.db.pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE app_user_id = $1 AND action = 'artifact.delete'",
        env.user,
    )
    assert count == 1


async def test_range_parser() -> None:
    from general_personal_agent.gpa.api.tasks import _parse_range, _parse_ranges

    assert _parse_ranges("bytes=0-9,20-29", 100) == [(0, 9), (20, 29)]
    assert _parse_range(None, 100) is None
    assert _parse_range("bytes=0-9", 100) == (0, 9)
    assert _parse_range("bytes=90-", 100) == (90, 99)
    assert _parse_range("bytes=-10", 100) == (90, 99)
    assert _parse_range("bytes=100-120", 100) is False
    assert _parse_range("bytes=5-3", 100) is False
    assert _parse_range("items=0-1", 100) is False
    assert _parse_range("bytes=0-1", None) is False


async def test_artifact_range_head_and_multipart(api_client):
    client, env = api_client
    cid = (await client.post("/api/conversations", json={"title": "range", "channel": "text"})).json()[
        "conversationId"
    ]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="range",
        task_type="general",
    )
    artifact = await env.task_store.add_artifact(
        task_id=task["task_id"],
        name="range.txt",
        mime="text/plain",
        bytes=16,
        storage_ref="general-personal-agent/tasks/range.txt",
    )
    url = f"/api/tasks/{task['task_id']}/artifacts/{artifact['artifact_id']}/download"
    response = await client.get(url, headers={"Range": "bytes=0-4"})
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-4/16"
    assert response.content == b"01234"
    response = await client.head(url)
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    response = await client.get(url, headers={"Range": "bytes=0-1,4-5"})
    assert response.status_code == 206
    assert response.headers["content-type"].startswith("multipart/byteranges")
    response = await client.get(url, headers={"Range": "bytes=0-4", "If-Range": "nope"})
    assert response.status_code == 200


@pytest.mark.integration
async def test_conversation_management_api(api_client):
    client, env = api_client
    first = (
        await client.post("/api/conversations", json={"title": "第一条", "channel": "text"})
    ).json()["conversationId"]
    second = (
        await client.post("/api/conversations", json={"title": "第二条", "channel": "text"})
    ).json()["conversationId"]

    response = await client.patch(f"/api/conversations/{first}", json={"title": "改过的标题"})
    assert response.status_code == 200
    assert response.json()["title"] == "改过的标题"

    response = await client.patch(f"/api/conversations/{first}", json={"pinned": True})
    assert response.status_code == 200
    assert response.json()["pinnedAt"] is not None

    response = await client.get("/api/conversations")
    items = response.json()["items"]
    assert items[0]["conversationId"] == first

    response = await client.patch(f"/api/conversations/{second}", json={"archived": True})
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    response = await client.get("/api/conversations")
    assert all(item["conversationId"] != second for item in response.json()["items"])
    response = await client.get("/api/conversations", params={"archived": True})
    assert [item["conversationId"] for item in response.json()["items"]] == [second]

    response = await client.patch(f"/api/conversations/{second}", json={"archived": False})
    assert response.json()["status"] == "active"
    response = await client.delete(f"/api/conversations/{second}")
    assert response.status_code == 204
    assert (await client.get(f"/api/conversations/{second}")).status_code == 404

    # Pinned pagination stays stable and does not duplicate or drop rows.
    cursor = None
    seen: list[str] = []
    for _ in range(5):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/api/conversations", params=params)
        body = response.json()
        seen.extend(item["conversationId"] for item in body["items"])
        cursor = body.get("nextCursor")
        if not cursor:
            break
    assert len(seen) == len(set(seen))
    assert first in seen

    audit_actions = {
        row["action"]
        for row in await env.db.pool.fetch(
            "SELECT action FROM audit_log WHERE app_user_id = $1", env.user
        )
    }
    assert {
        "conversation.rename",
        "conversation.pin",
        "conversation.archive",
        "conversation.unarchive",
        "conversation.delete",
    } <= audit_actions


@pytest.mark.integration
async def test_conversation_delete_clears_children_and_blocks_running_work(api_client):
    client, env = api_client
    cid = (
        await client.post("/api/conversations", json={"title": "待删除", "channel": "text"})
    ).json()["conversationId"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="生成一份材料",
        task_type="report",
    )
    response = await client.delete(f"/api/conversations/{cid}")
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"

    await env.db.pool.execute(
        "UPDATE task_run SET status = 'completed' WHERE task_id = $1", task["task_id"]
    )
    response = await client.delete(f"/api/conversations/{cid}")
    assert response.status_code == 204
    assert await env.db.pool.fetchval(
        "SELECT count(*) FROM task_run WHERE conversation_id = $1", cid
    ) == 0
    assert await env.db.pool.fetchval(
        "SELECT count(*) FROM conversation WHERE conversation_id = $1", cid
    ) == 0


@pytest.mark.integration
async def test_conversation_cursor_pagination_with_pins(api_client):
    client, env = api_client
    ids: list[str] = []
    for index in range(5):
        cid = (
            await client.post(
                "/api/conversations",
                json={"title": f"翻页-{index}", "channel": "text"},
            )
        ).json()["conversationId"]
        ids.append(cid)
    await client.patch(f"/api/conversations/{ids[0]}", json={"pinned": True})
    await client.patch(f"/api/conversations/{ids[1]}", json={"pinned": True})

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/api/conversations", params=params)
        assert response.status_code == 200
        body = response.json()
        seen.extend(item["conversationId"] for item in body["items"])
        pages += 1
        cursor = body.get("nextCursor")
        if not cursor:
            break
        assert pages < 10

    assert len(seen) == len(set(seen))
    assert set(seen) == set(ids)
    assert set(seen[:2]) == {ids[0], ids[1]}


@pytest.mark.integration
async def test_conversation_memory_commit_api(api_client):
    client, env = api_client
    cid = (
        await client.post("/api/conversations", json={"title": "保存记忆", "channel": "text"})
    ).json()["conversationId"]

    class _FakeWriter:
        def __init__(self) -> None:
            self.called: list[str] = []

        async def commit_now(self, conversation_id: str) -> bool:
            self.called.append(conversation_id)
            return True

    writer = _FakeWriter()
    original = env.gpa.conversations.context_writer
    env.gpa.conversations.context_writer = writer
    try:
        await env.db.pool.execute(
            "UPDATE conversation SET ov_session_id = 'ov-test', ov_message_count = 2"
            " WHERE conversation_id = $1",
            cid,
        )
        response = await client.post(f"/api/conversations/{cid}/memory/commit")
        assert response.status_code == 202
        assert response.json() == {
            "status": "queued",
            "message": "已提交，OpenViking 正在整理记忆",
        }
        assert writer.called == [cid]
        audit = await env.db.pool.fetchval(
            "SELECT action FROM audit_log WHERE app_user_id = $1"
            " AND action = 'conversation.memory_commit'",
            env.user,
        )
        assert audit == "conversation.memory_commit"

        await env.db.pool.execute(
            "UPDATE conversation SET ov_message_count = 0 WHERE conversation_id = $1", cid
        )
        response = await client.post(f"/api/conversations/{cid}/memory/commit")
        assert response.status_code == 409
        assert response.json()["code"] == "CONFLICT"
    finally:
        env.gpa.conversations.context_writer = original


@pytest.mark.integration
async def test_conversation_draft_api_visibility_and_auto_title(api_client):
    client, env = api_client
    created = (
        await client.post(
            "/api/conversations",
            json={"title": "", "channel": "voice", "draft": True},
        )
    ).json()
    cid = created["conversationId"]
    detail = (await client.get(f"/api/conversations/{cid}")).json()
    assert detail["conversation"]["isDraft"] is True
    listing = (await client.get("/api/conversations")).json()
    assert cid not in [item["conversationId"] for item in listing["items"]]

    accepted = (
        await client.post(
            f"/api/conversations/{cid}/messages",
            json={"content": "语音自动标题\n第二行", "channel": "voice"},
        )
    ).json()
    await _wait_turn(client, cid, accepted["turnId"])

    listing = (await client.get("/api/conversations")).json()
    item = next(i for i in listing["items"] if i["conversationId"] == cid)
    assert item["isDraft"] is False
    assert item["title"] == "语音自动标题"


@pytest.mark.integration
async def test_list_conversations_has_running_task_api(api_client):
    client, env = api_client
    with_task = (
        await client.post("/api/conversations", json={"title": "有任务", "channel": "text"})
    ).json()["conversationId"]
    without_task = (
        await client.post("/api/conversations", json={"title": "无任务", "channel": "text"})
    ).json()["conversationId"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=with_task,
        goal="做一份报告",
        task_type="report",
    )

    body = (
        await client.get("/api/conversations", params={"has_running_task": "true"})
    ).json()
    ids = [item["conversationId"] for item in body["items"]]
    assert with_task in ids
    assert without_task not in ids

    await env.db.pool.execute(
        "UPDATE task_run SET status = 'completed' WHERE task_id = $1", task["task_id"]
    )
    body = (
        await client.get("/api/conversations", params={"has_running_task": "true"})
    ).json()
    assert with_task not in [item["conversationId"] for item in body["items"]]
