"""P4 task tools, MaExecutor and inbox projection tests."""

from __future__ import annotations

import time

import pytest

from general_personal_agent.clients.ma import MAStatusError
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


# ---------------------------------------------------------------------------
# 预算 / 重试 / 产物抢救（2026-11-03 海报任务误杀事故的回归）
# ---------------------------------------------------------------------------


async def test_poll_budget_extends_idle_window_but_keeps_the_hard_cap():
    from general_personal_agent.gpa.task.executor import MaTimeout, _PollBudget

    now = [0.0]
    budget = _PollBudget(100, 10, clock=lambda: now[0])

    now[0] = 9
    assert budget.check() is None
    budget.touch()  # 新事件续期
    now[0] = 15
    assert budget.check() is None

    now[0] = 25  # 距上次活动 10s
    stalled = budget.check()
    assert isinstance(stalled, MaTimeout)
    assert stalled.reason == "idle"

    now[0] = 110  # 超过总时长护栏
    capped = budget.check()
    assert isinstance(capped, MaTimeout)
    assert capped.reason == "deadline"


class SilentMA(FakeMA):
    """Never reports idle, so only the local budget can stop the run."""

    def __init__(self):
        super().__init__()
        self.events = [{"id": "e1", "type": "session.status_running"}]

    async def list_files_all(self, session_id):
        return []


class PosterMA(SilentMA):
    async def list_files_all(self, session_id):
        return [
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
            }
        ]


class LatePosterMA(PosterMA):
    """Registers the poster only on the second file listing."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def list_files_all(self, session_id):
        self.calls += 1
        if self.calls == 1:
            return []
        return await super().list_files_all(session_id)


async def test_artifact_grace_waits_for_late_registration():
    from types import SimpleNamespace

    executor = MaExecutor.__new__(MaExecutor)
    executor.settings = SimpleNamespace(
        task_artifact_grace_s=1, task_artifact_ttl_days=7, ma_tos_bucket=""
    )
    executor.ma = LatePosterMA()
    executor.tos = None

    started = time.monotonic()
    artifacts = await executor._artifacts_with_grace("task_1", "session_1", [])
    elapsed = time.monotonic() - started

    assert [item["name"] for item in artifacts] == ["poster.png"]
    assert executor.ma.calls >= 2
    assert elapsed >= 0.5


async def test_artifact_grace_zero_returns_without_waiting():
    from types import SimpleNamespace

    executor = MaExecutor.__new__(MaExecutor)
    executor.settings = SimpleNamespace(
        task_artifact_grace_s=0, task_artifact_ttl_days=7, ma_tos_bucket=""
    )
    executor.ma = LatePosterMA()
    executor.tos = None

    assert await executor._artifacts_with_grace("task_1", "session_1", []) == []
    assert executor.ma.calls == 1


def _fast_budget(env, **overrides):
    values = {
        "task_idle_timeout_s": 1,
        "task_timeout_s": 60,
        "ma_poll_interval_s": 0.5,
        "task_max_retry": 0,
        "task_retry_backoff_s": 0,
        "task_artifact_grace_s": 0,
        "task_progress_interval_s": 0,
    }
    values.update(overrides)
    return env.settings.model_copy(update=values)


@pytest.mark.integration
async def test_ma_executor_salvages_artifacts_when_budget_expires(gpa_env):
    """超时也要交付：已产出的文件以 partial 收尾，而不是整单丢弃。"""
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="超时抢救", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张展会海报",
        task_type="design_poster",
        idempotency_key="ma-timeout-salvage",
    )
    fake = PosterMA()
    executor = MaExecutor(_fast_budget(env), env.task_store, fake, tos=FakeTOS())
    assert await executor.run_once() is True

    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "partial"
    assert view["error"]["code"] == "TASK_TIMEOUT"
    assert "已回收 1 个" in view["summary"]
    assert [artifact["name"] for artifact in view["artifacts"]] == ["poster.png"]
    assert fake.interrupted == ["ma_1"]

    attempt = await env.db.pool.fetchrow(
        "SELECT status, error FROM task_attempt WHERE task_id = $1", task["task_id"]
    )
    assert attempt["status"] == "failed"
    assert attempt["error"]["code"] == "TASK_TIMEOUT"


@pytest.mark.integration
async def test_ma_executor_timeout_without_outputs_fails_with_timeout_code(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="超时无产物", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-timeout-empty",
    )
    executor = MaExecutor(_fast_budget(env), env.task_store, SilentMA(), tos=FakeTOS())
    assert await executor.run_once() is True

    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "failed"
    assert view["error"]["code"] == "TASK_TIMEOUT"
    assert view["artifacts"] == []


class FlakyMA(FakeMA):
    """First ``failures`` session creations hit a transient 503."""

    def __init__(self, failures: int = 1):
        super().__init__()
        self.failures = failures

    async def create_session(self, *args, **kwargs):
        if self.failures:
            self.failures -= 1
            raise MAStatusError("POST", "/sessions", 503, "upstream unavailable")
        return await super().create_session(*args, **kwargs)


@pytest.mark.integration
async def test_ma_executor_retries_transient_upstream_errors(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="重试", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-retry-1",
    )
    fake = FlakyMA(failures=1)
    settings = env.settings.model_copy(
        update={"task_max_retry": 1, "task_retry_backoff_s": 0, "task_artifact_grace_s": 0}
    )
    executor = MaExecutor(settings, env.task_store, fake, tos=FakeTOS())

    assert await executor.run_once() is True
    queued = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert queued["status"] == "queued"
    assert queued["summary"] == "第 2 次尝试排队中"
    assert queued["error"]["code"] == "UPSTREAM_UNAVAILABLE"

    assert await executor.run_once() is True
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"
    attempts = await env.db.pool.fetch(
        "SELECT seq, status FROM task_attempt WHERE task_id = $1 ORDER BY seq",
        task["task_id"],
    )
    assert [row["seq"] for row in attempts] == [1, 2]
    assert attempts[0]["status"] == "failed"
    assert attempts[1]["status"] == "completed"


async def test_retryable_failures_are_only_transient_upstream_errors():
    from general_personal_agent.gpa.task.executor import MaSessionFailed, MaTimeout

    def classify(exc):
        return MaExecutor._classify_failure(None, exc)

    assert classify(MAStatusError("GET", "/events", 429, "rate limited"))[2] is True
    assert classify(MAStatusError("GET", "/events", 503, "down"))[2] is True
    assert classify(MAStatusError("GET", "/events", 400, "bad request"))[2] is False
    assert classify(MaSessionFailed("session failed"))[2] is True
    assert classify(MaTimeout("idle", "stalled"))[2] is False
    assert classify(ValueError("boom"))[2] is False


async def test_retry_delay_is_exponential_and_capped():
    from types import SimpleNamespace

    settings = SimpleNamespace(task_retry_backoff_s=30, task_retry_backoff_max_s=100)
    executor = SimpleNamespace(settings=settings)

    assert MaExecutor._retry_delay(executor, 1) == 30
    assert MaExecutor._retry_delay(executor, 2) == 60
    assert MaExecutor._retry_delay(executor, 3) == 100
    assert MaExecutor._retry_delay(executor, 5) == 100

    settings.task_retry_backoff_s = 0
    assert MaExecutor._retry_delay(executor, 2) == 0


class BrokenEventsMA(SilentMA):
    """The event endpoint is down; every poll raises 503 and no outputs exist."""

    async def fetch_events_all(self, session_id):
        raise MAStatusError("GET", "/events", 503, "down")


@pytest.mark.integration
async def test_ma_executor_gives_up_after_consecutive_poll_failures(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="轮询失败", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-poll-1",
    )
    executor = MaExecutor(
        _fast_budget(env, task_idle_timeout_s=60), env.task_store, BrokenEventsMA(), tos=FakeTOS()
    )

    assert await executor.run_once() is True
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "failed"
    assert view["error"]["code"] == "UPSTREAM_UNAVAILABLE"


@pytest.mark.integration
async def test_requeue_clears_the_dead_session_and_honours_the_backoff(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="退避", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-backoff-1",
    )
    claimed = await env.task_store.claim_next("ma", owner_id="worker_a")
    assert claimed["task"]["task_id"] == task["task_id"]
    await env.task_store.attach_attempt(
        task["task_id"], claimed["attempt"]["attempt_id"], "sess_old"
    )
    assert await env.task_store.requeue(
        task["task_id"],
        owner_id="worker_a",
        error={"code": "UPSTREAM_UNAVAILABLE", "message": "MA HTTP 503"},
        delay_s=60,
    )

    row = await env.db.pool.fetchrow(
        "SELECT status, executor_ref, next_retry_at FROM task_run WHERE task_id = $1",
        task["task_id"],
    )
    assert row["status"] == "queued"
    # The failed attempt's session must not be reused by the retry.
    assert row["executor_ref"] is None
    assert row["next_retry_at"] is not None

    # The window blocks an immediate reclaim, so retries cannot burn in seconds.
    assert await env.task_store.claim_next("ma", owner_id="worker_b") is None

    await env.db.pool.execute(
        "UPDATE task_run SET next_retry_at = now() - interval '1 second' WHERE task_id = $1",
        task["task_id"],
    )
    reclaimed = await env.task_store.claim_next("ma", owner_id="worker_b")
    assert reclaimed["task"]["task_id"] == task["task_id"]
    assert reclaimed["attempt"]["seq"] == 2


class ProgressMA(FakeMA):
    """Reports one tool call, then idle; enough to project a progress event."""

    def __init__(self):
        super().__init__()
        self.polls = 0

    async def fetch_events_all(self, session_id):
        self.polls += 1
        events = [
            {"id": "e0", "type": "session.status_running"},
            {"id": "e1", "type": "agent.tool_use", "name": "bash"},
        ]
        if self.polls >= 2:
            events.append({"id": "e2", "type": "session.status_idle"})
            events.append(
                {
                    "id": "e3",
                    "type": "agent.message",
                    "content": [{"type": "text", "text": "海报已生成"}],
                }
            )
        return events


@pytest.mark.integration
async def test_ma_executor_projects_progress_events(gpa_env):
    env = gpa_env
    conversation = await env.conversations.create(
        tenant_id=env.tenant, app_user_id=env.user, title="进度", channel="text"
    )
    task = await env.tasks.submit(
        tenant_id=env.tenant,
        app_user_id=env.user,
        conversation_id=conversation["conversation_id"],
        goal="生成一张海报",
        task_type="image",
        idempotency_key="ma-progress-1",
    )
    executor = MaExecutor(
        _fast_budget(env, task_idle_timeout_s=30), env.task_store, ProgressMA(), tos=FakeTOS()
    )
    assert await executor.run_once() is True

    rows = await env.db.pool.fetch(
        "SELECT payload FROM task_event WHERE task_id = $1 AND type = 'progress' ORDER BY seq",
        task["task_id"],
    )
    assert rows
    payload = rows[0]["payload"]
    assert payload["status"] == "running"
    assert payload["summary"] == "执行中 · 第 1 步 · bash"
    assert payload["progress"] > 0.02
    view = await env.tasks.get(task["task_id"], env.tenant, env.user)
    assert view["status"] == "completed"
