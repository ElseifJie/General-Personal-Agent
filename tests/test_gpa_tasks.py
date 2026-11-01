"""P4 task tools, MaExecutor and inbox projection tests."""

from __future__ import annotations

import pytest

from general_personal_agent.gpa.task.artifacts import ArtifactJanitor
from general_personal_agent.gpa.task.executor import MaExecutor
from general_personal_agent.gpa.task.tools import TaskToolState, build_task_tools

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeTOS:
    prefix = "general-personal-agent"

    def __init__(self):
        self.copied: list[tuple[str, str, str | None]] = []
        self.deleted: list[str] = []

    def object_key(self, *parts):
        return "/".join(["general-personal-agent", *map(str, parts)])

    async def copy_object(self, source_key, target_key, source_bucket=None):
        self.copied.append((source_key, target_key, source_bucket))
        return target_key

    async def delete(self, key):
        self.deleted.append(key)


class FakeMA:
    def __init__(self):
        self.created: list[str] = []
        self.versions: list[int | None] = []
        self.sent: list[tuple[str, str]] = []
        self.interrupted: list[str] = []
        self.events = [
            {
                "id": "e1",
                "type": "agent.message",
                "content": [{"type": "text", "text": "任务完成，海报已生成。"}],
            },
            {"id": "e2", "type": "session.status_idle"},
        ]

    async def get_agent(self, agent_id):
        return {"id": agent_id, "version": 7}

    async def create_session(self, agent_id, environment_id, agent_version=None):
        self.versions.append(agent_version)
        session = f"ma_{len(self.created) + 1}"
        self.created.append(session)
        return session

    async def send_message(self, session_id, text):
        self.sent.append((session_id, text))
        return {}

    async def fetch_events_all(self, session_id):
        return list(self.events)

    async def list_files_all(self, session_id):
        return [
            {
                "id": "file_ref",
                "filename": "reference.jpg",
                "mime_type": "image/jpeg",
                "bytes": 64,
                "description": "/mnt/session/outputs/.staging/reference.jpg",
                "tos": {
                    "object_key": "general-personal-agent/task/reference.jpg",
                    "bucket": "ma-source-bucket",
                },
            },
            {
                "id": "file_1",
                "filename": "poster.png",
                "mime_type": "image/png",
                "bytes": 128,
                "description": "/mnt/session/outputs/poster.png",
                "tos": {
                    "object_key": "general-personal-agent/task/poster.png",
                    "bucket": "ma-source-bucket",
                },
            },
        ]

    async def submit_event(self, session_id, event):
        self.interrupted.append(session_id)
        return {}

    async def delete_session(self, session_id):
        return None


@pytest.mark.integration
async def test_task_tools_contract_and_idempotency(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="任务工具", channel="text"
    )
    cid = conversation["conversation_id"]
    state = TaskToolState(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        turn_id="turn_tools",
    )
    tools = {tool.__name__: tool for tool in build_task_tools(state, env.tasks)}

    submitted = await tools["submit_task"](
        "做一张 AI 眼镜海报", "image", facts="标题：看见未来", constraints="蓝色科技感"
    )
    assert submitted["status"] == "queued"
    task_id = submitted["task_id"]
    again = await tools["submit_task"](
        "做一张 AI 眼镜海报", "image", facts="标题：看见未来", constraints="蓝色科技感"
    )
    assert again["task_id"] == task_id

    current = await tools["get_task"](task_id)
    assert current["status"] == "queued"
    assert current["artifacts"] == 0
    listed = await tools["list_tasks"]()
    assert any(item["task_id"] == task_id for item in listed)

    artifact = await env.task_store.add_artifact(
        task_id=task_id,
        name="preview.png",
        mime="image/png",
        bytes=10,
        storage_ref="general-personal-agent/preview.png",
    )
    artifacts = await tools["list_artifacts"](task_id)
    assert artifacts[0]["artifact_id"] == artifact["artifact_id"]
    read = await tools["read_artifact"](artifact["artifact_id"])
    assert read["download_url"].endswith("/download")

    other = TaskToolState(
        tenant_id=env.tenant,
        app_user_id="bob",
        conversation_id=cid,
        turn_id="turn_other",
    )
    other_tools = {tool.__name__: tool for tool in build_task_tools(other, env.tasks)}
    assert (await other_tools["read_artifact"](artifact["artifact_id"]))["error"] == "NOT_FOUND"

    cancelled = await tools["cancel_task"](task_id, "用户取消")
    assert cancelled["status"] == "cancelled"


@pytest.mark.integration
async def test_ma_executor_runs_task_and_inbox_projects_artifacts(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="MA 执行", channel="text"
    )
    cid = conversation["conversation_id"]
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid,
        goal="生成一张海报",
        task_type="image",
        brief={"facts": ["标题：看见未来"]},
        idempotency_key="ma-exec-1",
    )
    fake = FakeMA()
    fake_tos = FakeTOS()
    executor = MaExecutor(env.settings, env.task_store, fake, tos=fake_tos)
    assert await executor.run_once() is True

    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"
    assert view["progress"] == 1.0
    assert "任务完成" in view["summary"]
    assert len(view["artifacts"]) == 1
    stored = await env.db.pool.fetchrow(
        "SELECT storage_ref, source_ref, expires_at FROM task_artifact WHERE task_id = $1",
        task["task_id"],
    )
    assert stored["storage_ref"].startswith("general-personal-agent/tasks/")
    assert stored["source_ref"] == "general-personal-agent/task/poster.png"
    assert stored["expires_at"] is not None
    assert fake_tos.copied and fake_tos.copied[0][1].startswith("general-personal-agent/tasks/")
    assert fake_tos.copied[0][2] == "ma-source-bucket"
    assert fake.created and fake.sent
    assert fake.versions == [7]
    assert "生成一张海报" in fake.sent[0][1]

    await env.inbox.run_once()
    events = await env.store.list_events(cid)
    types = [event["type"] for event in events]
    assert "task.created" in types
    assert "task.status" in types
    assert "artifact" in types
    timeline, _, _ = await env.store.list_timeline(cid)
    assert any(item["kind"] == "event" and item["type"] == "artifact" for item in timeline)


async def test_artifact_janitor_removes_expired_copy_only(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="artifact ttl", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="ttl",
        task_type="general",
    )
    artifact = await env.task_store.add_artifact(
        task_id=task["task_id"],
        name="old.txt",
        mime="text/plain",
        bytes=3,
        storage_ref="general-personal-agent/tasks/old.txt",
        source_ref="ma/source/old.txt",
        ttl_days=1,
    )
    await env.db.pool.execute(
        "UPDATE task_artifact SET expires_at = now() - interval '1 hour' WHERE artifact_id = $1",
        artifact["artifact_id"],
    )
    fake_tos = FakeTOS()
    janitor = ArtifactJanitor(env.settings, env.task_store, fake_tos)
    assert await janitor.run_once() == 1
    assert fake_tos.deleted == ["general-personal-agent/tasks/old.txt"]
    assert await env.task_store.get_artifact(artifact["artifact_id"]) is None


async def test_task_idempotency_is_owner_scoped(gpa_env):
    env = gpa_env
    key = f"shared-{env.user}"
    cid_a = (
        await env.conversations.create(
            tenant_id=env.tenant, app_user_id=env.user, title="owner a", channel="text"
        )
    )["conversation_id"]
    cid_b = (
        await env.conversations.create(
            tenant_id=env.tenant, app_user_id="bob", title="owner b", channel="text"
        )
    )["conversation_id"]
    task_a = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=cid_a,
        goal="same goal",
        task_type="general",
        idempotency_key=key,
    )
    task_b = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id="bob",
        conversation_id=cid_b,
        goal="same goal",
        task_type="general",
        idempotency_key=key,
    )
    assert task_a["task_id"] != task_b["task_id"]
    assert task_b["task_id"]
    await env.db.pool.execute("DELETE FROM task_run WHERE conversation_id = $1", cid_b)
    await env.db.pool.execute("DELETE FROM conversation WHERE conversation_id = $1", cid_b)
    await env.db.pool.execute("DELETE FROM audit_log WHERE app_user_id = 'bob'")


@pytest.mark.integration
async def test_ma_executor_honours_pinned_agent_version(gpa_env):
    """MA_AGENT_VERSION>0 pins the build; 0 follows the agent's current one."""
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="MA 版本钉住", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-exec-version",
    )
    fake = FakeMA()
    pinned = env.settings.model_copy(update={"ma_agent_version": 3})
    executor = MaExecutor(pinned, env.task_store, fake, tos=FakeTOS())
    assert await executor.run_once() is True
    assert fake.versions == [3]
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"


async def test_is_artifact_ignores_staged_inputs_and_meta():
    from general_personal_agent.gpa.task.executor import is_artifact

    assert is_artifact(
        {
            "filename": "out.jpg",
            "mime_type": "image/jpeg",
            "description": "/mnt/session/outputs/out.jpg",
        }
    )
    assert not is_artifact(
        {
            "filename": "ref.jpg",
            "mime_type": "image/jpeg",
            "description": "/mnt/session/outputs/.staging/ref.jpg",
        }
    )
    assert not is_artifact({"filename": "out.jpg_meta.json", "mime_type": "application/json"})
    assert not is_artifact({"filename": "notes.txt", "mime_type": "text/plain"})
