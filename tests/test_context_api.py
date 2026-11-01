"""Context browser API contract tests (no database required)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from general_personal_agent.auth import SessionUser
from general_personal_agent.clients.ov import OVError
from general_personal_agent.gpa.api import context_router
from general_personal_agent.gpa.api.deps import current_user
from general_personal_agent.gpa.errors import install_gpa_error_handlers

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeIdentity:
    async def resolve(self, app_user_id: str):
        return SimpleNamespace(ov_user="alice")


class _FakeOV:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.content = ""
        self.error: Exception | None = None

    async def tree(self, uri: str, **kwargs):
        if self.error:
            raise self.error
        return self.entries

    async def read(self, uri: str) -> str:
        if self.error:
            raise self.error
        return self.content


def _services(fake: _FakeOV):
    async def ov_client_for(app_user_id: str):
        assert app_user_id == "alice"
        return fake

    return SimpleNamespace(identity_service=_FakeIdentity(), ov_client_for=ov_client_for)


@pytest_asyncio.fixture(loop_scope="session")
async def context_client():
    fake = _FakeOV()
    app = FastAPI()
    install_gpa_error_handlers(app)
    app.include_router(context_router)
    app.state.services = _services(fake)
    app.dependency_overrides[current_user] = lambda: SessionUser("alice", "user")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, fake


async def test_tree_lists_only_visible_entries(context_client):
    client, fake = context_client
    fake.entries = [
        {
            "uri": "viking://user/alice",
            "rel_path": "user/alice",
            "name": "alice",
            "isDir": True,
            "size": 4096,
            "modTime": "2026-10-26T15:06:15.000Z",
            "abstract": "",
        },
        {
            "uri": "viking://user/alice/memories/identity.md",
            "rel_path": "user/alice/memories/identity.md",
            "name": "identity.md",
            "isDir": False,
            "size": 218,
            "modTime": "2026-10-26T14:24:18.000Z",
            "abstract": "",
        },
        {
            "uri": "viking://resources/admin/README.md/GPA.md",
            "rel_path": "resources/admin/README.md/GPA.md",
            "name": "GPA.md",
            "isDir": False,
            "size": 324,
            "modTime": "2026-10-26T15:06:06.000Z",
            "abstract": "",
        },
    ]

    response = await client.get("/api/context/tree")

    assert response.status_code == 200
    body = response.json()
    assert body["rootUri"] == "viking://"
    assert body["userUri"] == "viking://user/alice"
    assert body["truncated"] is False
    assert [item["path"] for item in body["items"]] == [
        "resources/admin/README.md/GPA.md",
        "user/alice",
        "user/alice/memories/identity.md",
    ]
    identity = next(item for item in body["items"] if item["name"] == "identity.md")
    assert identity["isDir"] is False
    assert identity["size"] == 218
    assert identity["updatedAt"] == "2026-10-26T14:24:18.000Z"


async def test_content_reads_and_truncates(context_client):
    client, fake = context_client
    fake.content = "x" * 400_001

    response = await client.get(
        "/api/context/content",
        params={"uri": "viking://user/alice/sessions/messages.jsonl"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["uri"] == "viking://user/alice/sessions/messages.jsonl"
    assert body["truncated"] is True
    assert len(body["content"]) == 400_000


async def test_content_rejects_non_viking_uri(context_client):
    client, _fake = context_client

    response = await client.get("/api/context/content", params={"uri": "/etc/passwd"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_content_maps_permission_denied(context_client):
    client, fake = context_client
    fake.error = OVError(403, "PERMISSION_DENIED", "Access denied")

    response = await client.get(
        "/api/context/content", params={"uri": "viking://user/alice/privacy/key"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_tree_maps_upstream_failure(context_client):
    client, fake = context_client
    fake.error = OVError(500, "INTERNAL", "boom")

    response = await client.get("/api/context/tree")

    assert response.status_code == 503
    assert response.json()["retryable"] is True
