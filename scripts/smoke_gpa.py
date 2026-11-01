"""GPA P1 smoke: conversation -> text turn -> events/timeline/trace -> task inbox.

Run against the local database (schema/migrations applied):

    python -m scripts.smoke_gpa

Uses the deterministic stub agent so it never calls a model. The demo ``alice``
identity must exist. Created rows are removed at the end.
"""

from __future__ import annotations

import asyncio

from general_personal_agent.db import Database
from general_personal_agent.gpa.services import build_gpa_services
from general_personal_agent.settings import get_settings


async def _wait_turn(gpa, cid: str, turn_id: str, tenant: str, user: str) -> dict:
    for _ in range(200):
        detail = await gpa.conversations.detail(cid, tenant, user)
        for turn in detail["turns"]:
            if turn["turn_id"] == turn_id and turn["status"] != "running":
                return turn
        await asyncio.sleep(0.02)
    raise RuntimeError(f"turn {turn_id} did not settle")


async def main() -> None:
    settings = get_settings().model_copy(update={"gpa_agent_mode": "stub"})
    db = await Database.connect(settings)
    gpa = build_gpa_services(settings, db)
    tenant, user = "demo", "alice"
    conversation_id = None
    task_id = None
    try:
        conversation = await gpa.conversations.create(
            tenant_id=tenant, app_user_id=user, title="P1 smoke", channel="text"
        )
        conversation_id = conversation["conversation_id"]
        accepted = await gpa.conversations.submit_text(
            conversation_id,
            tenant,
            user,
            content="你好，这是一次 P1 冒烟测试",
            client_submission_id="smoke-gpa-1",
        )
        turn = await _wait_turn(gpa, conversation_id, accepted["turn_id"], tenant, user)
        assert turn["status"] == "completed", turn["status"]
        assert turn["assistant_text"], "assistant text is empty"

        events = await gpa.store.list_events(conversation_id)
        types = [event["type"] for event in events]
        assert types[0] == "turn.started", types
        assert "message.final" in types, types
        assert types[-1] == "turn.settled", types

        timeline, _, _ = await gpa.store.list_timeline(conversation_id)
        assert any(item["kind"] == "turn" for item in timeline), timeline

        trace = await db.pool.fetchrow(
            "SELECT * FROM context_trace WHERE conversation_id = $1", conversation_id
        )
        assert trace is not None, "context_trace missing"

        task = await gpa.tasks.submit(
            tenant_id=tenant,
            app_user_id=user,
            conversation_id=conversation_id,
            goal="P1 冒烟任务",
            task_type="smoke",
            idempotency_key=f"smoke-gpa-{conversation_id}",
        )
        task_id = task["task_id"]
        await gpa.inbox.run_once()
        events = await gpa.store.list_events(conversation_id)
        assert "task.created" in [event["type"] for event in events], "inbox projection missing"

        print("P1 smoke PASS")
        print(f"  conversation: {conversation_id}")
        print(f"  turn:         {turn['turn_id']} ({turn['status']})")
        print(f"  events:       {[event['type'] for event in events]}")
        print(f"  task:         {task_id} ({task['status']})")
        print(f"  trace:        {trace['trace_id']}")
    finally:
        if task_id:
            await db.pool.execute("DELETE FROM task_run WHERE task_id = $1", task_id)
        if conversation_id:
            await db.pool.execute(
                "DELETE FROM conversation WHERE conversation_id = $1", conversation_id
            )
            await db.pool.execute(
                "DELETE FROM context_trace WHERE conversation_id = $1", conversation_id
            )
        await gpa.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
