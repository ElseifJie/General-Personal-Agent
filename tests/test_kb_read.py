"""Reading the body behind a knowledge-base retrieval hit (unit, no database).

Regression for the "the PDF was only indexed with its title and author" report:
OpenViking splits an imported deck into one markdown file per detected section,
and a cover page becomes its own section that holds nothing but the title and
the author line.  Retrieval ranks that fragment first for a query about the
chapter, and the agent used to have no way to see the real body.

Every URI, name and size below is synthetic: the fixtures must not carry a real
customer's document, author or byline into the repository.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.files.kb import KbService

pytestmark = pytest.mark.asyncio(loop_scope="session")

DOC_URI = "viking://resources/示例智能硬件AI开发指南-0f3c9a21-7d4e5b88.pdf"
FOLDER = f"{DOC_URI}/示例智能硬件AI开发指南"
COVER_SECTION = f"{FOLDER}/基于示例框架的嵌入式应用开发实践_1.md"
BODY_SECTION = f"{FOLDER}/示例智能硬件_从无线连接到端侧_AI_2.md"
BODY_TEXT = "把本地能力变成 示例 IDE 可调用的服务：固件烧录、串口控制、日志读取。" * 40

DOC = {
    "doc_id": "doc_0123456789abcdef0123456789abcdef",
    "tenant_id": "demo",
    "app_user_id": "admin",
    "scope": "shared",
    "target_uri": DOC_URI,
    "title": "示例智能硬件AI开发指南.pdf",
    "index_status": "ready",
    "reconcile_state": "confirmed",
    "deleted_at": None,
    "file_mime": "application/pdf",
    "file_bytes": 1048576,
}


class _Store:
    def __init__(self, docs: list[dict] | None = None):
        self.docs = docs if docs is not None else [DOC]

    async def list_kb_documents(self, tenant_id, app_user_id, scope, **kwargs):
        return [doc for doc in self.docs if doc["scope"] == scope]

    async def get_kb_by_uri(self, tenant_id, uri, *, include_deleted=False):
        return next((doc for doc in self.docs if doc["target_uri"] == uri), None)

    async def list_tombstones(self, tenant_id, scope):
        return []

    async def list_open_invalidations(self, tenant_id, scope):
        return []


class _Client:
    def __init__(self, files: dict[str, str], listing: dict[str, list[dict]]):
        self.files = files
        self.listing = listing
        self.reads: list[str] = []

    async def ls(self, uri):
        return self.listing.get(uri.rstrip("/"), [])

    async def read(self, uri):
        self.reads.append(uri)
        if uri not in self.files:
            raise RuntimeError("not found")
        return self.files[uri]

    async def abstract(self, uri):
        return "整份文档的摘要"

    async def close(self):
        return None


class _Identity:
    ov_user = "admin"
    ov_peer = None
    app_user_id = "admin"
    api_key = "ADMIN_KEY"
    role = "admin"


class _IdentityService:
    async def resolve(self, app_user_id, tenant_id):
        return _Identity()

    async def for_admin(self, tenant_id=None):
        return _Identity()


def _listing() -> dict[str, list[dict]]:
    return {
        DOC_URI: [
            {"uri": FOLDER, "isDir": True, "size": 4096},
        ],
        FOLDER: [
            {"uri": COVER_SECTION, "isDir": False, "size": 194},
            {"uri": BODY_SECTION, "isDir": False, "size": 6274},
            {"uri": f"{FOLDER}/.abstract.md", "isDir": False, "size": 300},
            {"uri": f"{FOLDER}/cover_img1.jpg", "isDir": False, "size": 1000},
        ],
    }


def _service(store=None, files=None, listing=None) -> tuple[KbService, _Client]:
    client = _Client(
        files
        if files is not None
        else {
            COVER_SECTION: "基于示例框架的嵌入式应用开发实践\n张三 · 示例科技生态软件主管工程师\n",
            BODY_SECTION: BODY_TEXT,
        },
        listing if listing is not None else _listing(),
    )
    settings = SimpleNamespace(
        ov_url="http://ov",
        ov_timeout_s=1,
        ov_personal_kb_indexes="",
        ov_shared_kb_categories="",
        gpa_kb_shared_write_enabled=True,
    )

    async def factory(key, user):
        return client

    service = KbService(
        settings, store or _Store(), None, None, _IdentityService(), client_factory=factory
    )
    return service, client


async def test_document_uri_returns_section_outline_not_text():
    service, client = _service()

    result = await service.read_uri(
        tenant_id="demo", app_user_id="admin", scope="shared", uri=DOC_URI
    )

    assert result["kind"] == "document"
    assert result["document"]["docId"] == DOC["doc_id"]
    assert result["abstract"] == "整份文档的摘要"
    # Only markdown sections are listed, largest first, generated files dropped.
    assert [row["uri"] for row in result["sections"]] == [BODY_SECTION, COVER_SECTION]
    assert result["sections"][0]["bytes"] == 6274
    assert "text" not in result
    assert client.reads == []


async def test_heading_only_section_points_at_the_real_body():
    service, _client = _service()

    result = await service.read_uri(
        tenant_id="demo", app_user_id="admin", scope="shared", uri=COVER_SECTION
    )

    assert result["kind"] == "section"
    assert result["contentStatus"] == "thin"
    assert result["document"]["uri"] == DOC_URI
    assert "张三" in result["text"]
    # The body lives in a sibling section; the agent is told where to look.
    assert [row["uri"] for row in result["sections"]] == [BODY_SECTION]
    # The text is the slide itself, not a serialized outline of the document.
    assert result["text"].strip() == (
        "基于示例框架的嵌入式应用开发实践\n张三 · 示例科技生态软件主管工程师"
    )
    assert "其他分节" in result["hint"]


@pytest.mark.parametrize(
    ("body", "thin"),
    [
        # Slide-deck cover split into its own section: title + byline.
        ("基于示例框架的嵌入式应用开发实践\n张三 · 示例科技生态软件主管工程师\n", True),
        ("", True),
        ("一、测评目的\n", True),
        # Short but real prose (a report's two-line paragraph) is content.
        ("三、客户能获得什么\n共建测评集，客户拿到的不只是一份测评报告，还有三个方面的长期收益。\n", False),
        ("一、测评目的\n在客户的真实业务场景下开展测评，要达成三件事：\n1. 真实体现模型能力\n2. 暴露模型缺陷\n", False),
        ("- 支持修改人物属性\n- 支持多轮指代\n", False),
        # Long enough to be prose even without punctuation.
        ("示例 IDE 需要从会写代码走向能控设备懂开发流程持续沿用已确认配置" * 6, False),
    ],
)
async def test_thin_classification_uses_structure_not_only_length(body, thin):
    from general_personal_agent.gpa.files.kb import _heading_only

    assert _heading_only(body) is thin


async def test_section_body_is_paginated_from_the_offset():
    service, _client = _service()

    result = await service.read_uri(
        tenant_id="demo",
        app_user_id="admin",
        scope="shared",
        uri=BODY_SECTION,
        offset=10,
        limit=100,
    )

    assert result["contentStatus"] == "ok"
    assert result["text"] == BODY_TEXT[10:110]
    assert result["offset"] == 10
    assert result["nextOffset"] == 110
    assert result["truncated"] is True
    assert result["totalChars"] == len(BODY_TEXT)
    assert "sections" not in result


async def test_reading_a_page_beyond_the_end_is_empty_but_valid():
    service, _client = _service()

    result = await service.read_uri(
        tenant_id="demo", app_user_id="admin", scope="shared", uri=BODY_SECTION, offset=10**9
    )

    assert result["text"] == ""
    assert result["truncated"] is False


async def test_document_still_indexing_is_reported_not_read():
    pending = {**DOC, "index_status": "indexing"}
    service, client = _service(store=_Store([pending]))

    result = await service.read_uri(
        tenant_id="demo", app_user_id="admin", scope="shared", uri=COVER_SECTION
    )

    assert result["status"] == "indexing"
    assert "仍在索引" in result["message"]
    assert client.reads == []


async def test_uri_outside_the_scope_root_is_rejected():
    service, client = _service()

    with pytest.raises(GpaError) as excinfo:
        await service.read_uri(
            tenant_id="demo",
            app_user_id="admin",
            scope="shared",
            uri="viking://user/Alice/resources/uploads/private.md",
        )

    assert excinfo.value.code == "NOT_FOUND"
    assert client.reads == []


async def test_private_document_of_another_user_is_not_readable():
    foreign = {**DOC, "scope": "private", "app_user_id": "bob", "target_uri": DOC_URI}
    service, _client = _service(store=_Store([foreign]))

    with pytest.raises(GpaError) as excinfo:
        await service.read_uri(
            tenant_id="demo", app_user_id="alice", scope="private", uri=COVER_SECTION
        )

    assert excinfo.value.code == "NOT_FOUND"


async def test_parsed_images_are_stripped_but_ocr_text_is_kept():
    """OV's parse output is full of viking:// image links the agent cannot open."""
    noisy = (
        "采集 Log · 分析问题\n"
        "![image](viking://resources/deck.pdf/deck/cover_img1.jpg)\n"
        '<KBImage position="1" uuid="">02</KBImage>\n'
        "真实开发板 · 运行固件 · 执行命令\n"
        "![image](viking://resources/deck.pdf/deck/cover_img2.jpg)\n"
    )
    service, _client = _service(files={BODY_SECTION: noisy})

    result = await service.read_uri(
        tenant_id="demo", app_user_id="admin", scope="shared", uri=BODY_SECTION
    )

    assert "viking://" not in result["text"]
    assert "<KBImage" not in result["text"]
    assert "采集 Log" in result["text"]
    assert "02" in result["text"]  # OCR text inside the tag survives
    assert "真实开发板" in result["text"]
    # Pagination counts what the agent receives, not the raw OV markdown.
    assert result["totalChars"] < len(noisy)
    assert result["truncated"] is False


async def test_scope_is_inferred_from_the_uri():
    service, _client = _service()
    assert (
        await service.scope_for_uri(tenant_id="demo", app_user_id="admin", uri=COVER_SECTION)
        == "shared"
    )
    assert (
        await service.scope_for_uri(
            tenant_id="demo",
            app_user_id="admin",
            uri="viking://user/admin/resources/uploads/notes.md",
        )
        == "private"
    )
    with pytest.raises(GpaError):
        await service.scope_for_uri(
            tenant_id="demo", app_user_id="admin", uri="viking://user/admin/memories/profile/a.md"
        )
