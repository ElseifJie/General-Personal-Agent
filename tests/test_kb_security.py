"""KB preview authorization and intent parsing (unit, no database)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.conversation.service import kb_intent_scope
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.kb import KbService, normalize_kb_uri


class _Store:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def get_kb_by_uri(self, tenant_id, uri, *, include_deleted=False):
        return self.docs.get(uri)

    async def list_tombstones(self, tenant_id, scope):
        return []

    async def list_open_invalidations(self, tenant_id, scope):
        return []


class _Identity:
    def __init__(self):
        self.calls: list[str] = []

    async def resolve(self, app_user_id, tenant_id):
        self.calls.append("user")
        return SimpleNamespace(
            api_key="USER_KEY", ov_user="alice", app_user_id="alice",
            tenant_id=tenant_id, role="user", ov_peer=None,
        )

    async def for_admin(self, tenant_id=None):
        self.calls.append("admin")
        return SimpleNamespace(
            api_key="ADMIN_KEY", ov_user="admin", app_user_id="admin",
            tenant_id="demo", role="admin", ov_peer=None,
        )


class _Client:
    def __init__(self, key: str):
        self.key = key

    async def read(self, uri: str) -> str:
        return f"{self.key}:{uri}"

    async def close(self) -> None:
        return None


def _settings():
    return SimpleNamespace(
        ov_url="http://ov", ov_timeout_s=1,
        ov_personal_kb_indexes="", ov_shared_kb_categories="",
        gpa_kb_shared_write_enabled=True,
    )


def _service(store, identity):
    async def factory(key, user):
        return _Client(key)

    return KbService(_settings(), store, None, None, identity, client_factory=factory)


@pytest.mark.asyncio
async def test_shared_read_uses_caller_identity_and_never_admin():
    identity = _Identity()
    service = _service(_Store(), identity)
    text = await service.content(
        tenant_id="demo", app_user_id="alice", scope="shared",
        uri="viking://resources/admin/company.md",
    )
    assert text.startswith("USER_KEY:")
    assert "admin" not in identity.calls


@pytest.mark.asyncio
async def test_shared_scope_cannot_read_admin_private_or_agent_config():
    service = _service(_Store(), _Identity())
    for uri in (
        "viking://user/admin/resources/secret.md",
        "viking://agent/payments/card.json",
        "viking://resourcesx/admin/company.md",
    ):
        with pytest.raises(GpaError) as caught:
            await service.content(
                tenant_id="demo", app_user_id="alice", scope="shared", uri=uri
            )
        assert caught.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_private_doc_of_another_user_is_not_found():
    store = _Store()
    store.docs["viking://user/alice/resources/uploads/a.md"] = {
        "doc_id": "kb_a", "scope": "private", "app_user_id": "bob",
        "index_status": "ready", "reconcile_state": "none", "deleted_at": None,
    }
    service = _service(store, _Identity())
    with pytest.raises(GpaError):
        await service.content(
            tenant_id="demo", app_user_id="alice", scope="private",
            uri="viking://user/alice/resources/uploads/a.md",
        )


def test_uri_normalization_rejects_traversal():
    assert normalize_kb_uri("viking://resources/a//b/") == "viking://resources/a/b"
    with pytest.raises(GpaError):
        normalize_kb_uri("viking://resources/../user/admin")
    with pytest.raises(GpaError):
        normalize_kb_uri("https://example.com/x")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("请把附件保存到知识库", "private"),
        ("把附件保存到共享知识库", "shared"),
        ("不要把附件保存到共享知识库，只做总结", None),
        ("不要保存到知识库", None),
        ("如果保存到知识库会怎样？", None),
        ("“保存到知识库”只是示例", None),
        ("不用把刚才的文件放进知识库", None),
    ],
)
def test_kb_intent_negation_and_quotes(text, expected):
    assert kb_intent_scope(text) == expected
