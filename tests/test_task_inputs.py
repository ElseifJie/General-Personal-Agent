"""MA task input attachment tests."""

from __future__ import annotations

import pytest

from general_personal_agent.gpa.task.executor import MaExecutor

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeTOS:
    def __init__(self):
        self.objects = {}

    async def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = (data, content_type)
        return key

    async def get_bytes(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)


class FakeMA:
    def __init__(self):
        self.uploaded = []
        self.deleted = []
        self.created = []
        self.sent = []
        self.sessions = 0

    async def get_agent(self, agent_id):
        return {"id": agent_id, "version": 7}

    async def upload_file(self, name, data, content_type="application/octet-stream", purpose="agent"):
        file_id = f"ma_file_{len(self.uploaded) + 1}"
        self.uploaded.append((name, data, content_type, file_id))
        return {"id": file_id}

    async def delete_file(self, file_id):
        self.deleted.append(file_id)

    async def create_session(self, agent_id, environment_id, **kwargs):
        self.sessions += 1
        self.created.append(kwargs)
        return f"ma_session_{self.sessions}"

    async def send_message(self, session_id, text):
        self.sent.append((session_id, text))

    async def fetch_events_all(self, session_id):
        return [
            {"type": "agent.message", "content": [{"type": "text", "text": "done"}]},
            {"type": "session.status_idle"},
        ]

    async def list_files_all(self, session_id):
        return [
            {
                "id": self.uploaded[0][3],
                "filename": "input.md",
                "mime_type": "text/markdown",
                "bytes": 10,
                "description": "/workspace/inputs/input.md",
            },
            {
                "id": "out1",
                "filename": "result.md",
                "mime_type": "text/markdown",
                "bytes": 20,
                "description": "/workspace/outputs/result.md",
            },
        ]

    async def submit_event(self, session_id, event):
        return {}


@pytest.mark.integration
async def test_task_inputs_are_mounted_and_filtered_from_artifacts(gpa_env):
    env = gpa_env
    tos = FakeTOS()
    env.gpa.files.tos = tos
    env.gpa.file_jobs.register("extract", env.gpa.file_jobs._handlers["extract"])
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="inputs", channel="text"
    )
    uploaded = await env.gpa.files.upload_conversation(
        tenant_id=env.tenant, app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        name="input.md", mime="text/markdown", data=b"# input\nhello",
    )
    file_id = uploaded["file"]["file_id"]
    task = await env.tasks.submit(
        tenant_id=env.tenant, app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="use the file", task_type="general",
        idempotency_key="task-input-1", attachment_ids=[file_id],
    )
    inputs = await env.gpa.files.store.list_task_inputs(task["task_id"])
    assert len(inputs) == 1
    assert inputs[0]["mount_path"].startswith("/workspace/inputs/")

    fake = FakeMA()
    executor = MaExecutor(env.settings, env.task_store, fake, tos=tos, files=env.gpa.files)
    assert await executor.run_once() is True
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"
    assert fake.created[0]["resources"][0]["file_id"] == "ma_file_1"
    assert "输入文件" in fake.sent[0][1]
    assert [a["name"] for a in view["artifacts"]] == ["result.md"]
    assert fake.deleted == ["ma_file_1"]
