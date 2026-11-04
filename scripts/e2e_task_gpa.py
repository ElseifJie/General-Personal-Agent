"""End-to-end smoke: task_run -> MaExecutor -> task_event -> inbox.

Requires real MA credentials in .env. Runs one small MA session, then asserts
the task reaches a terminal state and the inbox projects it into the
conversation timeline. Not dependent on the HTTP server or model.

    python -m scripts.e2e_task_gpa
"""

from __future__ import annotations

import asyncio
import time

from general_personal_agent.clients.ma import MAClient
from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.db import Database
from general_personal_agent.gpa.services import build_gpa_services
from general_personal_agent.settings import get_settings


async def main() -> None:
    settings = get_settings()
    db = await Database.connect(settings)
    ma = MAClient(settings.ma_base_url, settings.ma_key)
    tos = TOSClient(
        settings.tos_endpoint,
        settings.database_tos_region,
        settings.volcengine_access_key,
        settings.volcengine_secret_key,
        settings.database_tos_bucket_name,
        session_token=settings.volcengine_session_token,
        prefix=settings.tos_prefix,
    )
    gpa = build_gpa_services(settings, db, ma=ma, tos=tos)
    conversation_id = ""
    task_id = ""
    try:
        conversation = await gpa.conversations.create(
            tenant_id="demo", app_user_id="alice", title="P4 task e2e", channel="text"
        )
        conversation_id = conversation["conversation_id"]
        task = await gpa.tasks.submit(
            tenant_id="demo",
            app_user_id="alice",
            conversation_id=conversation_id,
            goal="请只回复一句话：P4 task e2e ok。不要生成任何文件。",
            task_type="general",
            idempotency_key=f"p4-e2e-{conversation_id}",
        )
        task_id = task["task_id"]
        print("submitted", task_id)
        started = time.perf_counter()
        assert gpa.ma_executor is not None
        assert await asyncio.wait_for(gpa.ma_executor.run_once(), 180) is True
        view = await gpa.tasks.get(task_id, "demo", "alice")
        print("elapsed", int(time.perf_counter() - started), "status", view["status"])
        print("summary", view["summary"][:300])
        print("artifacts", len(view["artifacts"]))
        assert view["status"] in {"completed", "failed"}, view
        await gpa.inbox.run_once()
        events = await gpa.store.list_events(conversation_id)
        types = [event["type"] for event in events]
        print("events", types)
        assert "task.created" in types and "task.status" in types
        print("P4 task e2e PASS")
    finally:
        if task_id:
            await db.pool.execute("DELETE FROM task_run WHERE task_id = $1", task_id)
        if conversation_id:
            await db.pool.execute("DELETE FROM conversation WHERE conversation_id = $1", conversation_id)
            await db.pool.execute("DELETE FROM context_trace WHERE conversation_id = $1", conversation_id)
        await gpa.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
