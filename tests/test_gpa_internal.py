"""P0 internal service-token gateway tests."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from general_personal_agent.gpa.api.internal import oauth_router
from general_personal_agent.gpa.api.internal import router as internal_router
from general_personal_agent.gpa.errors import install_gpa_error_handlers
from general_personal_agent.gpa.service_token import issue_service_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def internal_client(gpa_env):
    settings = gpa_env.settings.model_copy(
        update={
            "gpa_service_token": "current-token",
            "gpa_service_token_previous": "old-token",
            "gpa_service_jwt_secret": "jwt-test-secret-0123456789abcdef",
            "gpa_oauth_client_id": "mcp-client",
            "gpa_oauth_client_secret": "mcp-secret-0123456789abcdef",
            "gpa_rate_limit_oauth_per_min": 1,
        }
    )
    app = FastAPI()
    install_gpa_error_handlers(app)
    app.include_router(internal_router)
    app.include_router(oauth_router)
    app.state.services = SimpleNamespace(settings=settings, db=gpa_env.db)
    app.state.gpa = gpa_env.gpa
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, gpa_env


async def test_internal_task_requires_and_rotates_service_token(internal_client):
    client, env = internal_client
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="internal", channel="text"
    )
    body = {
        "tenantId": env.tenant,
        "appUserId": env.user,
        "conversationId": conversation["conversation_id"],
        "goal": "内部任务",
        "taskType": "general",
    }
    assert (await client.post("/api/internal/tasks", json=body)).status_code == 401
    headers = {"X-GPA-Service-Token": "current-token"}
    response = await client.post("/api/internal/tasks", json=body, headers=headers)
    assert response.status_code == 200, response.text
    task_id = response.json()["taskId"]
    old_headers = {"X-GPA-Service-Token": "old-token"}
    response = await client.get(
        f"/api/internal/tasks/{task_id}",
        params={"tenant": env.tenant, "userId": env.user},
        headers=old_headers,
    )
    assert response.status_code == 200
    assert response.json()["taskId"] == task_id

    read_token = issue_service_token(
        settings=client._transport.app.state.services.settings,
        tenant_id=env.tenant,
        subject="read-worker",
        scopes=("task:read",),
        allowed_users=(env.user,),
    )["token"]
    read_headers = {"X-GPA-Service-Token": read_token}
    response = await client.get(
        f"/api/internal/tasks/{task_id}",
        params={"tenant": env.tenant, "userId": env.user},
        headers=read_headers,
    )
    assert response.status_code == 200
    response = await client.post("/api/internal/tasks", json=body, headers=read_headers)
    assert response.status_code == 403

    oauth = await client.post(
        "/api/internal/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "mcp-client",
            "client_secret": "mcp-secret-0123456789abcdef",
            "scope": "task:read",
            "tenant": env.tenant,
            "users": env.user,
        },
    )
    assert oauth.status_code == 200
    access = oauth.json()["access_token"]
    limited = await client.post(
        "/api/internal/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "mcp-client",
            "client_secret": "mcp-secret-0123456789abcdef",
            "scope": "task:read",
        },
    )
    assert limited.status_code == 429
    response = await client.get(
        f"/api/internal/tasks/{task_id}",
        params={"tenant": env.tenant, "userId": env.user},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 200
    response = await client.get(
        f"/api/internal/tasks/{task_id}",
        params={"tenant": env.tenant, "userId": "bob"},
        headers=read_headers,
    )
    assert response.status_code == 403
    revoke = await client.post(
        "/api/internal/oauth/revoke",
        data={
            "token": access,
            "client_id": "mcp-client",
            "client_secret": "mcp-secret-0123456789abcdef",
        },
    )
    assert revoke.status_code == 200
    response = await client.get(
        f"/api/internal/tasks/{task_id}",
        params={"tenant": env.tenant, "userId": env.user},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 401


async def test_kms_file_key_provider(tmp_path, gpa_env):
    from general_personal_agent.gpa.service_token import issue_service_token, verify_service_token_async

    key_file = tmp_path / "jwt.key"
    key_file.write_text("file-secret-0123456789abcdef0123456789", encoding="utf-8")
    settings = gpa_env.settings.model_copy(
        update={
            "gpa_service_jwt_secret": "",
            "gpa_service_jwt_secret_file": str(key_file),
        }
    )
    issued = issue_service_token(settings, tenant_id="demo", subject="kms-worker")
    claims = await verify_service_token_async(settings, issued["token"], gpa_env.db)
    assert claims is not None
    assert claims.subject == "kms-worker"
