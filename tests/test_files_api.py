"""File upload, conversation attachment and KB API tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from general_personal_agent.api.middleware import RequestContextMiddleware, install_error_handlers
from general_personal_agent.auth import sign_session
from general_personal_agent.gpa.api import (
    conversation_files_router,
    conversations_router,
    kb_router,
    uploads_router,
)
from general_personal_agent.gpa.errors import install_gpa_error_handlers
from general_personal_agent.gpa.files.jobs import ExtractHandler

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeTOS:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    async def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = (data, content_type)
        return key

    async def get_bytes(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeIdentityService:
    async def resolve(self, app_user_id, tenant_id=None):
        return SimpleNamespace(
            app_user_id=app_user_id,
            ov_user=app_user_id,
            api_key="fake-key",
            ov_peer=None,
            tenant_id=tenant_id or "demo",
            role="user",
        )

    async def for_admin(self, tenant_id=None):
        return SimpleNamespace(
            app_user_id="admin",
            ov_user="default",
            api_key="admin-key",
            ov_peer=None,
            tenant_id="demo",
            role="admin",
        )


class FakeOV:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.deleted: list[str] = []
        self.task_status = "completed"
        self.defer_import = False
        self.pending_imports: dict[str, str] = {}

    async def mkdir(self, uri):
        self.dirs.add(uri)

    async def import_bytes(self, filename, data, to_uri, content_type="text/markdown", wait=True):
        text = data.decode("utf-8", errors="replace")
        if self.defer_import:
            self.pending_imports[to_uri] = text
        else:
            self.files[to_uri] = text
        return {"status": "success", "task_id": "t1"}

    def complete_import(self, uri):
        self.files[uri] = self.pending_imports.pop(uri, "")

    async def task(self, task_id):
        return {"status": self.task_status}

    async def stat(self, uri):
        return {"uri": uri} if uri in self.files or uri in self.dirs else None

    async def ls(self, uri):
        prefix = uri.rstrip("/") + "/"
        names: dict[str, dict] = {}
        for key in list(self.files) + list(self.dirs):
            if key.startswith(prefix):
                rest = key[len(prefix) :]
                if not rest:
                    continue
                name = rest.split("/", 1)[0]
                names[name] = {
                    "uri": prefix + name,
                    "name": name,
                    "isDir": (prefix + name) in self.dirs,
                }
        return list(names.values())

    async def read(self, uri):
        return self.files[uri]

    async def search(self, query, target_uri=None, limit=8, context_type=None):
        return {"resources": [{"uri": uri, "name": uri.rsplit("/", 1)[-1]} for uri in self.files]}

    async def delete(self, uri, recursive=True):
        if getattr(self, "fail_delete", False):
            raise RuntimeError("ov delete failed")
        self.deleted.append(uri)
        for key in list(self.files):
            if key == uri or (recursive and key.startswith(uri.rstrip("/") + "/")):
                self.files.pop(key, None)
        return {}

    async def close(self):
        return None


@pytest_asyncio.fixture(loop_scope="session")
async def file_api(gpa_env):
    env = gpa_env
    fake_tos = FakeTOS()
    fake_ov = FakeOV()
    env.gpa.files.tos = fake_tos
    env.gpa.kb.tos = fake_tos
    env.gpa.kb.identity_service = FakeIdentityService()

    async def factory(_key, _user):
        return fake_ov

    env.gpa.kb._client_factory = factory
    env.gpa.file_jobs.register("extract", ExtractHandler(env.gpa.files.store, fake_tos))
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    install_gpa_error_handlers(app)
    app.include_router(conversations_router)
    app.include_router(uploads_router)
    app.include_router(kb_router)
    app.include_router(conversation_files_router)
    app.state.services = SimpleNamespace(settings=env.settings, db=env.db, tos=fake_tos)
    app.state.gpa = env.gpa
    token = sign_session(env.user, "user", env.settings.app_secret_key, 3600)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Cookie": f"{env.settings.session_cookie_name}={token}"},
    ) as client:
        yield client, env, fake_tos, fake_ov


async def _run_jobs(env) -> None:
    for _ in range(10):
        if not await env.gpa.file_jobs.run_once():
            return


@pytest.mark.integration
async def test_conversation_upload_and_attachment(file_api):
    client, env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "files", "channel": "text"})).json()[
        "conversationId"
    ]
    response = await client.post(
        "/api/files",
        data={"purpose": "conversation", "conversationId": cid},
        files={"file": ("notes.md", b"# hello\n\nworld", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cf_id = body["conversationFileId"]
    assert cf_id
    await _run_jobs(env)
    detail = await client.get(f"/api/files/{body['fileId']}")
    assert detail.json()["textStatus"] == "ready"
    response = await client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "summarize", "clientSubmissionId": "s1", "attachmentIds": [cf_id]},
    )
    assert response.status_code == 202, response.text
    response.json()["turnId"]
    for _ in range(100):
        turns = (await client.get(f"/api/conversations/{cid}")).json()["turns"]
        if turns and turns[0]["status"] != "running":
            break
        await asyncio.sleep(0.02)
    turns = (await client.get(f"/api/conversations/{cid}")).json()["turns"]
    assert turns[0]["attachments"][0]["fileId"] == body["fileId"]
    # idempotency includes the attachment fingerprint
    replay = await client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "different", "clientSubmissionId": "s1", "attachmentIds": [cf_id]},
    )
    assert replay.status_code == 409


@pytest.mark.integration
async def test_kb_private_upload_list_content_and_delete(file_api):
    client, _env, _tos, _ov = file_api
    response = await client.post(
        "/api/files",
        data={"purpose": "private_kb", "categoryId": "policy"},
        files={"file": ("policy.md", b"# policy\n\nbe kind", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    await _run_jobs(_env)
    entries = (await client.get("/api/kb/entries?scope=private")).json()
    entry = next(item for item in entries["items"] if item["docId"] == doc["docId"])
    assert entry["size"] == len(b"# policy\n\nbe kind")
    assert not any(item["name"] == "uploads" for item in entries["items"])
    content = await client.get(
        "/api/kb/content",
        params={
            "scope": "private",
            "uri": next(item["uri"] for item in entries["items"] if item["docId"] == doc["docId"]),
        },
    )
    assert content.status_code == 200
    assert "be kind" in content.json()["content"]
    uri = next(item["uri"] for item in entries["items"] if item["docId"] == doc["docId"])
    deleted = await client.delete("/api/kb/entries", params={"scope": "private", "uri": uri})
    assert deleted.status_code == 202
    await _run_jobs(_env)
    content = await client.get("/api/kb/content", params={"scope": "private", "uri": uri})
    assert content.status_code == 404


@pytest.mark.integration
async def test_download_and_bind_existing_file(file_api):
    client, _env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "bind", "channel": "text"})).json()[
        "conversationId"
    ]
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "conversation"},
        files={"file": ("bind.md", b"# bind", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["fileId"]
    downloaded = await client.get(f"/api/files/{file_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"# bind"
    bound = await client.post(f"/api/conversations/{cid}/files", json={"fileIds": [file_id]})
    assert bound.status_code == 200, bound.text
    assert bound.json()["items"][0]["fileId"] == file_id
    listed = await client.get(f"/api/conversations/{cid}/files")
    assert listed.json()["items"][0]["conversationFileId"] == bound.json()["items"][0]["conversationFileId"]


@pytest.mark.integration
async def test_promote_conversation_file_to_private_kb(file_api):
    client, env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "promote", "channel": "text"})).json()[
        "conversationId"
    ]
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "conversation", "conversationId": cid},
        files={"file": ("promote.md", b"# promote me", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["fileId"]
    promoted = await client.post(f"/api/files/{file_id}/promote", params={"scope": "private"})
    assert promoted.status_code == 200, promoted.text
    doc_id = promoted.json()["docId"]
    await _run_jobs(env)
    entries = (await client.get("/api/kb/entries?scope=private")).json()
    assert any(item["docId"] == doc_id for item in entries["items"])


@pytest.mark.integration
async def test_shared_kb_requires_admin(file_api):
    client, env, _tos, _ov = file_api
    denied = await client.post(
        "/api/files",
        data={"purpose": "shared_kb"},
        files={"file": ("team.md", b"# team", "text/markdown")},
    )
    assert denied.status_code == 403
    # The service layer is where the API maps the same check; exercise admin
    # through a direct call because the fixture client is a normal user.
    result = await env.gpa.kb.upload(
        tenant_id=env.tenant,
        app_user_id="admin",
        role="admin",
        scope="shared",
        name="team.md",
        mime="text/markdown",
        data=b"# team",
    )
    assert result["doc"]["scope"] == "shared"
    # This record is owned by the fixed admin identity, so the normal
    # gpatest_* fixture teardown would not remove it.  It points at FakeTOS,
    # and leaving it behind makes the real app show a permanently failed doc.
    await env.db.pool.execute("DELETE FROM file_job WHERE payload->>'doc_id'=$1", result["doc"]["doc_id"])
    await env.db.pool.execute("DELETE FROM file_object WHERE file_id=$1", result["file"]["file_id"])


@pytest.mark.integration
async def test_delete_failure_is_visible_and_retryable(file_api):
    client, env, _tos, ov = file_api
    response = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("retry.md", b"# retry", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    await _run_jobs(env)
    entries = (await client.get("/api/kb/entries?scope=private")).json()["items"]
    entry = next(item for item in entries if item["docId"] == response.json()["docId"])
    ov.fail_delete = True
    await client.delete("/api/kb/entries", params={"scope": "private", "uri": entry["uri"]})
    await _run_jobs(env)
    deletions = (await client.get("/api/kb/deletions")).json()["items"]
    failed = next(item for item in deletions if item["docId"] == response.json()["docId"])
    assert failed["status"] == "delete_failed"
    ov.fail_delete = False
    retried = await client.post(f"/api/kb/documents/{response.json()['docId']}/retry-delete")
    assert retried.status_code == 200
    await _run_jobs(env)
    deletions = (await client.get("/api/kb/deletions")).json()["items"]
    assert not any(item["docId"] == response.json()["docId"] for item in deletions)


@pytest.mark.integration
async def test_kb_intent_auto_promotes_attachment(file_api):
    client, env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "kb intent", "channel": "text"})).json()[
        "conversationId"
    ]
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "conversation", "conversationId": cid},
        files={"file": ("intent.md", b"# intent\nkeep me", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    cf_id = uploaded.json()["conversationFileId"]
    await _run_jobs(env)
    response = await client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "请把附件保存到知识库", "attachmentIds": [cf_id]},
    )
    assert response.status_code == 202, response.text
    for _ in range(100):
        turns = (await client.get(f"/api/conversations/{cid}")).json()["turns"]
        if turns and turns[0]["status"] != "running":
            break
        await asyncio.sleep(0.02)
    await _run_jobs(env)
    entries = (await client.get("/api/kb/entries?scope=private")).json()["items"]
    assert any(item["name"] == "intent.md" for item in entries)


@pytest.mark.integration
async def test_kb_negative_intent_does_not_promote(file_api):
    client, env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "no kb", "channel": "text"})).json()[
        "conversationId"
    ]
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "conversation", "conversationId": cid},
        files={"file": ("private.md", b"# private\nsecret", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    cf_id = uploaded.json()["conversationFileId"]
    await _run_jobs(env)
    response = await client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "不要把附件保存到共享知识库，只做总结", "attachmentIds": [cf_id]},
    )
    assert response.status_code == 202, response.text
    for _ in range(100):
        turns = (await client.get(f"/api/conversations/{cid}")).json()["turns"]
        if turns and turns[0]["status"] != "running":
            break
        await asyncio.sleep(0.02)
    await _run_jobs(env)
    entries = (await client.get("/api/kb/entries?scope=private")).json()["items"]
    assert not any(item["name"] == "private.md" for item in entries)


@pytest.mark.integration
async def test_bind_rejects_file_in_gc_deleting(file_api):
    client, env, _tos, _ov = file_api
    cid = (await client.post("/api/conversations", json={"title": "gc", "channel": "text"})).json()[
        "conversationId"
    ]
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "conversation"},
        files={"file": ("race.md", b"# race", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["fileId"]
    await env.db.pool.execute("UPDATE file_object SET gc_state='deleting' WHERE file_id=$1", file_id)
    bound = await client.post(f"/api/conversations/{cid}/files", json={"fileIds": [file_id]})
    assert bound.status_code == 409, bound.text


@pytest.mark.integration
async def test_stale_kb_generation_cannot_publish(file_api):
    client, env, _tos, _ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("gen.md", b"# generation", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    await env.gpa.kb.reindex(doc_id, tenant_id=env.tenant, app_user_id=env.user, role="user")
    await env.gpa.kb.index_document(doc_id, expected_generation=0)
    row = await env.db.pool.fetchrow(
        "SELECT index_status, index_generation FROM kb_document WHERE doc_id=$1", doc_id
    )
    assert row["index_generation"] == 1
    assert row["index_status"] != "ready"


@pytest.mark.integration
async def test_download_rejects_soft_deleted_kb_document(file_api):
    client, env, _tos, _ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("gone.md", b"# gone", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    await _run_jobs(env)
    await env.db.pool.execute(
        "UPDATE kb_document SET deleted_at=now(), index_status='ready' WHERE doc_id=$1", doc_id
    )
    response = await client.get(f"/api/kb/documents/{doc_id}/download")
    assert response.status_code == 404, response.text


@pytest.mark.integration
async def test_reconcile_missing_target_requeues_with_new_generation(file_api):
    client, env, _tos, _ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("missing.md", b"# missing", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    await env.db.pool.execute(
        "UPDATE kb_document SET index_status='reconciling', index_generation=0 WHERE doc_id=$1",
        doc_id,
    )
    await env.gpa.kb.reconcile_document(doc_id, expected_generation=0)
    row = await env.db.pool.fetchrow(
        "SELECT index_status, index_generation FROM kb_document WHERE doc_id=$1", doc_id
    )
    assert row["index_status"] == "pending"
    assert row["index_generation"] == 1
    jobs = await env.db.pool.fetch(
        "SELECT payload FROM file_job WHERE type='kb_index' AND payload->>'doc_id'=$1",
        doc_id,
    )
    assert any(int(job["payload"].get("generation", -1)) == 1 for job in jobs)


@pytest.mark.integration
async def test_delete_invalidates_ancestors_up_to_kb_root(file_api):
    client, env, _tos, _ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb", "categoryId": "cat"},
        files={"file": ("deep.md", b"# deep", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    await env.gpa.kb.delete_document(doc_id, app_user_id=env.user, tenant_id=env.tenant, role="user")
    rows = await env.db.pool.fetch(
        "SELECT dir_uri FROM kb_dir_invalidation WHERE tenant_id=$1 AND scope='private' AND dir_uri LIKE $2",
        env.tenant,
        f"viking://user/{env.user}/%",
    )
    dirs = {row["dir_uri"] for row in rows}
    root = f"viking://user/{env.user}/resources"
    assert root in dirs
    assert f"{root}/uploads" in dirs
    assert f"{root}/uploads/cat" in dirs
    target = await env.db.pool.fetchval("SELECT target_uri FROM kb_document WHERE doc_id=$1", doc_id)
    tombstone = await env.db.pool.fetchrow(
        "SELECT recursive FROM kb_tombstone WHERE tenant_id=$1 AND scope='private' AND uri=$2",
        env.tenant,
        target,
    )
    assert tombstone["recursive"] is True
    assert await env.gpa.kb.guard.is_allowed(env.tenant, "private", f"{target}/chapter.md") is False
    # Retrieval keeps the invalidated directory hidden, but the context
    # browser may keep the node itself so the tree keeps the hierarchy.
    assert await env.gpa.kb.guard.is_allowed(env.tenant, "private", f"{root}/uploads") is False
    assert (
        await env.gpa.kb.guard.is_allowed(
            env.tenant, "private", f"{root}/uploads", allow_invalidated_dirs=True
        )
        is True
    )
    assert await env.gpa.kb.guard.is_allowed(env.tenant, "private", f"{root}/.abstract.md") is False


@pytest.mark.integration
async def test_import_timeout_enters_reconcile_not_failed(file_api):
    client, env, _tos, ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("slow.md", b"# slow", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]

    async def timeout_import(*args, **kwargs):
        raise TimeoutError("ov import response timed out")

    ov.import_bytes = timeout_import
    await env.gpa.kb.index_document(doc_id, expected_generation=0)
    row = await env.db.pool.fetchrow(
        "SELECT index_status, reconcile_state FROM kb_document WHERE doc_id=$1", doc_id
    )
    assert row["index_status"] == "reconciling"
    assert row["reconcile_state"] == "unknown"
    jobs = await env.db.pool.fetch(
        "SELECT payload FROM file_job WHERE type='kb_reconcile' AND payload->>'doc_id'=$1",
        doc_id,
    )
    assert jobs


@pytest.mark.integration
async def test_late_import_is_compensated_after_delete(file_api):
    client, env, _tos, ov = file_api
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("late.md", b"# late", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    target = await env.db.pool.fetchval("SELECT target_uri FROM kb_document WHERE doc_id=$1", doc_id)
    await _run_jobs(env)
    await env.gpa.kb.delete_document(doc_id, app_user_id=env.user, tenant_id=env.tenant, role="user")
    await _run_jobs(env)
    generation = await env.db.pool.fetchval(
        "SELECT index_generation FROM kb_document WHERE doc_id=$1", doc_id
    )
    verify_jobs = await env.db.pool.fetch(
        "SELECT 1 FROM file_job WHERE type='kb_delete'"
        " AND payload->>'doc_id'=$1 AND (payload->>'verify')::boolean",
        doc_id,
    )
    assert verify_jobs, "delete must always leave a late-import compensation job"
    # Simulate a late OV import after the local delete already succeeded.
    ov.files[target] = "late content"
    await env.gpa.kb.verify_deleted(doc_id, generation, 0)
    assert target not in ov.files


@pytest.mark.integration
async def test_delete_defers_until_running_import_finishes(file_api):
    client, env, _tos, ov = file_api
    ov.defer_import = True
    ov.task_status = "running"
    uploaded = await client.post(
        "/api/files",
        data={"purpose": "private_kb"},
        files={"file": ("slow.md", b"# slow", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["docId"]
    target = await env.db.pool.fetchval("SELECT target_uri FROM kb_document WHERE doc_id=$1", doc_id)
    await _run_jobs(env)
    row = await env.db.pool.fetchrow("SELECT index_status FROM kb_document WHERE doc_id=$1", doc_id)
    assert row["index_status"] == "reconciling"

    await env.gpa.kb.delete_document(doc_id, app_user_id=env.user, tenant_id=env.tenant, role="user")
    await _run_jobs(env)
    row = await env.db.pool.fetchrow(
        "SELECT index_status, deleted_at FROM kb_document WHERE doc_id=$1", doc_id
    )
    assert row["index_status"] == "deleting"
    assert row["deleted_at"] is not None
    wait_jobs = await env.db.pool.fetch(
        "SELECT 1 FROM file_job WHERE type='kb_delete'"
        " AND payload->>'doc_id'=$1 AND status='queued' AND payload->>'verify' IS NULL",
        doc_id,
    )
    assert wait_jobs, "delete must be retried instead of trusting a 404"
    assert target not in ov.files

    # The OV task finishes after the delete request; the deferred job must
    # remove the tree that has just materialized.
    ov.task_status = "completed"
    ov.complete_import(target)
    await env.db.pool.execute(
        "UPDATE file_job SET run_after=now() WHERE type='kb_delete'"
        " AND payload->>'doc_id'=$1 AND status='queued' AND payload->>'verify' IS NULL",
        doc_id,
    )
    await _run_jobs(env)
    row = await env.db.pool.fetchrow("SELECT index_status FROM kb_document WHERE doc_id=$1", doc_id)
    assert row["index_status"] == "deleted"
    assert target not in ov.files
