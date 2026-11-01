"""Memory browser API: self + peer memory directories."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api import memories_router
from general_personal_agent.gpa.api.deps import current_user
from general_personal_agent.gpa.errors import install_gpa_error_handlers

pytestmark = pytest.mark.asyncio(loop_scope="session")

USER_ROOT = "viking://user/alice"


class _FakeIdentity:
    async def resolve(self, app_user_id: str):
        return SimpleNamespace(ov_user="alice", ov_peer="device_alice")


class _FakeOV:
    def __init__(self) -> None:
        self.content = "# 偏好\n喜欢蓝色\n"
        self.entries = [
            {"uri": f"{USER_ROOT}/memories", "isDir": True},
            {"uri": f"{USER_ROOT}/memories/preferences", "isDir": True},
            {"uri": f"{USER_ROOT}/memories/preferences/user", "isDir": True},
            {
                "uri": f"{USER_ROOT}/memories/preferences/user/color.md",
                "isDir": False,
                "size": 20,
                "modTime": "2026-10-30T03:00:00Z",
            },
            {"uri": f"{USER_ROOT}/peers", "isDir": True},
            {"uri": f"{USER_ROOT}/peers/device_alice", "isDir": True},
            {"uri": f"{USER_ROOT}/peers/device_alice/memories", "isDir": True},
            {"uri": f"{USER_ROOT}/peers/device_alice/memories/entities", "isDir": True},
            {"uri": f"{USER_ROOT}/peers/device_alice/memories/entities/li", "isDir": True},
            {
                "uri": f"{USER_ROOT}/peers/device_alice/memories/entities/li/wei.md",
                "isDir": False,
                "size": 30,
                "modTime": "2026-10-30T03:00:00Z",
            },
        ]

    async def ls(self, uri: str) -> list[dict]:
        prefix = f"{uri.rstrip('/')}/"
        return [
            entry
            for entry in self.entries
            if entry["uri"].startswith(prefix) and "/" not in entry["uri"][len(prefix) :]
        ]

    async def read(self, uri: str) -> str:
        return self.content


def _services(fake: _FakeOV):
    async def ov_client_for(app_user_id: str):
        assert app_user_id == "alice"
        return fake

    settings = SimpleNamespace(
        ov_personal_kb_indexes="",
        ov_shared_kb_categories="",
        ov_skills_enabled=True,
        ov_tenant_id="demo",
    )
    return SimpleNamespace(settings=settings, identity_service=_FakeIdentity(), ov_client_for=ov_client_for)


@pytest_asyncio.fixture(loop_scope="session")
async def memory_client():
    fake = _FakeOV()
    app = FastAPI()
    install_gpa_error_handlers(app)
    app.include_router(memories_router)
    app.state.services = _services(fake)
    app.dependency_overrides[current_user] = lambda: SessionUser("alice", "user")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, fake


async def test_lists_self_and_peer_memory_with_types(memory_client):
    client, _fake = memory_client

    response = await client.get("/api/memories")

    assert response.status_code == 200
    body = response.json()
    assert body["rootUri"] == f"{USER_ROOT}/memories"
    assert [scope["key"] for scope in body["scopes"]] == ["self", "peer:device_alice"]
    assert body["scopes"][1]["uri"] == f"{USER_ROOT}/peers/device_alice/memories"

    by_name = {item["name"]: item for item in body["items"]}
    self_doc = by_name["color.md"]
    assert self_doc["type"] == "preferences"
    assert self_doc["memoryTypeLabel"] == "偏好"
    assert self_doc["scope"] == "self"
    assert self_doc["path"] == "/memories/preferences/user/color.md"

    peer_doc = by_name["wei.md"]
    assert peer_doc["type"] == "entities"
    assert peer_doc["scope"] == "peer:device_alice"
    assert peer_doc["path"] == "/peers/device_alice/memories/entities/li/wei.md"


async def test_scope_filter_limits_to_peer(memory_client):
    client, _fake = memory_client

    response = await client.get("/api/memories", params={"scope": "peer"})

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert "wei.md" in names
    assert "color.md" not in names


async def test_read_allows_only_memory_paths(memory_client):
    client, fake = memory_client

    ok = await client.get(
        "/api/memories/content",
        params={"uri": f"{USER_ROOT}/peers/device_alice/memories/entities/li/wei.md"},
    )
    assert ok.status_code == 200
    assert ok.json()["content"] == fake.content

    denied = await client.get("/api/memories/content", params={"uri": f"{USER_ROOT}/privacy/keys.json"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"
