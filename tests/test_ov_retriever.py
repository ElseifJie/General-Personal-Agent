"""Context retrieval scoped to OpenViking's native directories."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.context.retriever import GpaContextRetriever

pytestmark = pytest.mark.asyncio(loop_scope="session")

USER_ROOT = "viking://user/alice"


class _Identity:
    ov_user = "alice"
    ov_peer = "device_alice"
    api_key = "test"


class _IdentityService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def resolve(self, app_user_id: str, tenant_id: str | None = None):
        self.calls.append((app_user_id, tenant_id))
        return _Identity()


class _SearchClient:
    """Fake OV client: returns canned hits per directory scope."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[str]]] = []
        self.fail: set[str] = set()

    async def search(self, query, target_uri=None, limit=8, context_type=None):
        targets = target_uri if isinstance(target_uri, list) else [target_uri]
        types = context_type if isinstance(context_type, list) else [context_type]
        self.calls.append((targets, types))
        if "memory" in types:
            if "memory" in self.fail:
                raise RuntimeError("memory down")
            return {
                "memories": [
                    {
                        "uri": f"{USER_ROOT}/memories/preferences/user/color.md",
                        "abstract": "用户喜欢蓝色",
                        "score": 0.9,
                    },
                    {
                        "uri": f"{USER_ROOT}/peers/device_alice/memories/entities/li/wei.md",
                        "abstract": "李伟负责周报",
                        "score": 0.8,
                    },
                    {
                        "uri": f"{USER_ROOT}/memories/.overview.md",
                        "abstract": "noise",
                        "score": 0.99,
                    },
                ]
            }
        if "skill" in types:
            return {
                "skills": [
                    {"uri": "viking://agent/skills/weekly-report", "abstract": "生成周报", "score": 0.7}
                ]
            }
        if "viking://resources" in targets:
            return {
                "resources": [
                    {"uri": "viking://resources/admin/policy.md", "abstract": "公司流程", "score": 0.6},
                    {
                        "uri": "viking://resources/admin/.abstract.md",
                        "abstract": "管理员资料合集",
                        "score": 0.55,
                    },
                    {
                        "uri": "viking://resources/.abstract.md",
                        "abstract": "整个共享库",
                        "score": 0.99,
                    },
                ]
            }
        return {
            "resources": [
                {"uri": f"{USER_ROOT}/resources/uploads/brand.md", "abstract": "品牌规范", "score": 0.5}
            ]
        }

    async def close(self):
        return None


def _retriever(fake: _SearchClient) -> GpaContextRetriever:
    settings = SimpleNamespace(
        ov_url="http://ov.test",
        ov_timeout_s=1.0,
        ov_search_top_k=8,
        ov_skills_enabled=True,
        ov_personal_kb_indexes="",
        ov_shared_kb_categories="",
    )
    retriever = GpaContextRetriever(settings, _IdentityService())

    async def client(app_user_id, identity):
        return fake

    retriever._client = client
    return retriever


async def test_retrieve_uses_each_openviking_directory():
    fake = _SearchClient()
    result = await _retriever(fake).retrieve("偏好", "alice", "demo")

    searched = {tuple(targets) for targets, _types in fake.calls}
    assert (f"{USER_ROOT}/memories", f"{USER_ROOT}/peers") in searched
    assert (f"{USER_ROOT}/resources",) in searched
    assert ("viking://resources",) in searched
    assert ("viking://agent/skills", f"{USER_ROOT}/skills") in searched

    memories = result["long_term_memory"]
    assert [(item["scope"], item["memoryType"]) for item in memories] == [
        ("self", "preferences"),
        ("peer", "entities"),
    ]
    assert memories[1]["peer"] == "device_alice"
    assert result["personal_kb"][0]["scope"] == "personal"
    assert result["personal_kb"][0]["category"] == "uploads"
    assert result["shared_kb"][0]["category"] == "admin"
    shared_uris = {item["uri"] for item in result["shared_kb"]}
    # Folder-level `.abstract.md` hits describe the directory, so they are
    # normalized to it; the scope root itself is noise and is dropped.
    assert "viking://resources/admin" in shared_uris
    assert "viking://resources" not in shared_uris
    assert result["skills"][0]["name"] == "weekly-report"
    assert result["degraded"] == []


async def test_retrieve_degrades_only_the_failed_slice():
    fake = _SearchClient()
    fake.fail = {"memory"}
    result = await _retriever(fake).retrieve("偏好", "alice", "demo")

    assert result["long_term_memory"] == []
    assert "memory_unavailable" in result["degraded"]
    assert result["personal_kb"][0]["snippet"] == "品牌规范"
    assert result["shared_kb"][0]["snippet"] == "公司流程"


async def test_search_knowledge_switches_scope():
    fake = _SearchClient()
    retriever = _retriever(fake)

    shared_only = await retriever.search_knowledge("政策", "alice", "demo", scope="shared")
    assert {item["scope"] for item in shared_only} == {"shared"}
    assert "viking://resources/admin/policy.md" in {item["uri"] for item in shared_only}

    legacy = await retriever.search_knowledge("政策", "alice", "demo", scope="admin")
    assert {item["scope"] for item in legacy} == {"shared"}

    personal_only = await retriever.search_knowledge("品牌", "alice", "demo", scope="personal")
    assert {item["scope"] for item in personal_only} == {"personal"}


async def test_search_memory_returns_typed_hits():
    fake = _SearchClient()
    hits = await _retriever(fake).search_memory("颜色", "alice", "demo", limit=2)
    assert hits[0]["memoryTypeLabel"] == "偏好"
    assert len(hits) == 2
