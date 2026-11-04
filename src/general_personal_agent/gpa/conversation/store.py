"""Conversation persistence and the single timeline event writer.

The persisted timeline order is ``conversation_event.seq``. Both writers —
ConversationService and TaskEventInbox — allocate through ``next_seq`` on the
``conversation_seq`` counters, inside the same transaction that inserts the
event.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from general_personal_agent.gpa.ids import new_conversation_id, new_event_id, new_turn_id
from general_personal_agent.gpa.util import to_iso


class ConversationNotFound(LookupError):
    pass


class TurnConflict(RuntimeError):
    pass


def encode_cursor(updated_at: datetime, conversation_id: str, pinned: bool = False) -> str:
    raw = json.dumps(
        {"p": bool(pinned), "u": to_iso(updated_at), "id": conversation_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str | None) -> tuple[bool | None, datetime | None, str | None]:
    if not cursor:
        return None, None, None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        pinned = data.get("p")
        if not isinstance(pinned, bool):
            return None, None, None
        return (
            pinned,
            datetime.fromisoformat(data["u"].replace("Z", "+00:00")),
            data.get("id"),
        )
    except Exception:  # noqa: BLE001 - a malformed cursor is simply the first page
        return None, None, None


def _summary(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["channels"] = list(item.get("channels") or [])
    item["is_draft"] = bool(item.get("is_draft"))
    assistant_preview = item.pop("last_assistant_text", "") or ""
    user_preview = item.pop("last_user_text", "") or ""
    item["last_turn_preview"] = (assistant_preview or user_preview).strip()[:160]
    item["last_turn_channel"] = item.pop("last_turn_channel", None)
    for key in ("task_count", "running_task_count", "artifact_count"):
        item[key] = int(item.get(key) or 0)
    return item


def event_envelope(row: Any, *, conversation_id: str | None = None) -> dict[str, Any]:
    item = dict(row)
    return {
        "schema_version": "1",
        "event_id": item["event_id"],
        "seq": int(item["seq"]),
        "type": item["type"],
        "conversation_id": conversation_id or item["conversation_id"],
        "turn_id": item.get("turn_id"),
        "task_id": item.get("task_id"),
        "trace_id": item.get("trace_id"),
        "payload": item.get("payload") or {},
        "occurred_at": to_iso(item.get("occurred_at")),
    }


SUMMARY_SELECT = """
SELECT c.conversation_id, c.tenant_id, c.app_user_id, c.title, c.active_channel,
       c.channels, c.status, c.pinned_at, c.created_at, c.updated_at, c.is_draft,
       COALESCE(t.task_count, 0) AS task_count,
       COALESCE(t.running_task_count, 0) AS running_task_count,
       COALESCE(a.artifact_count, 0) AS artifact_count,
       lt.assistant_text AS last_assistant_text,
       lt.user_text AS last_user_text,
       lt.channel AS last_turn_channel
  FROM conversation c
  LEFT JOIN LATERAL (
      SELECT count(*)::int AS task_count,
             count(*) FILTER (WHERE status IN ('queued','running'))::int AS running_task_count
        FROM task_run tr
       WHERE tr.conversation_id = c.conversation_id
  ) t ON true
  LEFT JOIN LATERAL (
      SELECT count(*)::int AS artifact_count
        FROM task_artifact ta
        JOIN task_run tr2 ON tr2.task_id = ta.task_id
       WHERE tr2.conversation_id = c.conversation_id
  ) a ON true
  LEFT JOIN LATERAL (
      SELECT assistant_text, user_text, channel
        FROM conversation_turn
       WHERE conversation_id = c.conversation_id
       ORDER BY seq DESC
       LIMIT 1
  ) lt ON true
"""


class ConversationStore:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ reads
    async def get(self, conversation_id: str, tenant_id: str, app_user_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            SUMMARY_SELECT + " WHERE c.conversation_id = $1 AND c.tenant_id = $2 AND c.app_user_id = $3",
            conversation_id,
            tenant_id,
            app_user_id,
        )
        return _summary(row) if row else None

    async def get_for_update(self, conn, conversation_id: str, tenant_id: str, app_user_id: str):
        return await conn.fetchrow(
            "SELECT * FROM conversation WHERE conversation_id = $1"
            " AND tenant_id = $2 AND app_user_id = $3 FOR UPDATE",
            conversation_id,
            tenant_id,
            app_user_id,
        )

    async def list_conversations(
        self,
        tenant_id: str,
        app_user_id: str,
        *,
        channel: str | None = None,
        query: str | None = None,
        archived: bool = False,
        has_running_task: bool = False,
        cursor: str | None = None,
        limit: int = 30,
    ) -> tuple[list[dict], str | None]:
        after_pinned, after_updated, after_id = decode_cursor(cursor)
        rows = await self.db.pool.fetch(
            f"""
            WITH base AS (
                {SUMMARY_SELECT}
                 WHERE c.tenant_id = $1 AND c.app_user_id = $2
                   AND ($3::text IS NULL OR $3 = ANY(c.channels))
                   AND (c.status = 'archived') = $5::boolean
                   AND NOT c.is_draft
            )
            SELECT * FROM base
             WHERE ($4::text IS NULL OR title ILIKE '%' || $4 || '%'
                    OR COALESCE(last_assistant_text, '') ILIKE '%' || $4 || '%'
                    OR COALESCE(last_user_text, '') ILIKE '%' || $4 || '%')
               AND (NOT $10::boolean OR running_task_count > 0)
               AND ($6::boolean IS NULL
                    OR ((pinned_at IS NOT NULL), updated_at, conversation_id)
                       < ($6::boolean, $7::timestamptz, $8::text))
             ORDER BY (pinned_at IS NOT NULL) DESC, updated_at DESC, conversation_id DESC
             LIMIT $9
            """,
            tenant_id,
            app_user_id,
            channel,
            query or None,
            archived,
            after_pinned,
            after_updated,
            after_id,
            limit + 1,
            has_running_task,
        )
        items = [_summary(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = encode_cursor(
                last["updated_at"],
                last["conversation_id"],
                bool(last.get("pinned_at")),
            )
        return items, next_cursor

    async def rename_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        title: str,
    ) -> None:
        await self.db.pool.execute(
            "UPDATE conversation SET title = $4, title_locked = true"
            " WHERE conversation_id = $1 AND tenant_id = $2 AND app_user_id = $3",
            conversation_id,
            tenant_id,
            app_user_id,
            title,
        )

    async def set_pinned(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        pinned: bool,
    ) -> None:
        await self.db.pool.execute(
            "UPDATE conversation SET pinned_at = CASE WHEN $4 THEN now() ELSE NULL END"
            " WHERE conversation_id = $1 AND tenant_id = $2 AND app_user_id = $3",
            conversation_id,
            tenant_id,
            app_user_id,
            pinned,
        )

    async def set_archived(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        archived: bool,
    ) -> None:
        await self.db.pool.execute(
            "UPDATE conversation SET status = CASE WHEN $4 THEN 'archived' ELSE 'active' END"
            " WHERE conversation_id = $1 AND tenant_id = $2 AND app_user_id = $3",
            conversation_id,
            tenant_id,
            app_user_id,
            archived,
        )

    async def has_running_work(self, conversation_id: str) -> bool:
        value = await self.db.pool.fetchval(
            """
            SELECT EXISTS(
                     SELECT 1 FROM task_run
                      WHERE conversation_id = $1 AND status IN ('queued','running')
                   )
                OR EXISTS(
                     SELECT 1 FROM conversation_turn
                      WHERE conversation_id = $1 AND status = 'running'
                   )
            """,
            conversation_id,
        )
        return bool(value)

    async def conversation_artifacts(self, conversation_id: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT ta.artifact_id, ta.storage_ref, ta.source_ref
              FROM task_artifact ta
              JOIN task_run tr ON tr.task_id = ta.task_id
             WHERE tr.conversation_id = $1
            """,
            conversation_id,
        )
        return [dict(row) for row in rows]

    async def delete_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
    ) -> bool:
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM task_run WHERE conversation_id = $1", conversation_id
                )
                await conn.execute(
                    "DELETE FROM context_trace WHERE conversation_id = $1", conversation_id
                )
                await conn.execute(
                    "DELETE FROM context_outbox WHERE conversation_id = $1", conversation_id
                )
                status = await conn.execute(
                    "DELETE FROM conversation"
                    " WHERE conversation_id = $1 AND tenant_id = $2 AND app_user_id = $3",
                    conversation_id,
                    tenant_id,
                    app_user_id,
                )
        return status != "DELETE 0"

    async def list_turns(self, conversation_id: str, limit: int = 100) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT * FROM conversation_turn WHERE conversation_id = $1 ORDER BY seq DESC LIMIT $2",
            conversation_id,
            limit,
        )
        return [dict(r) for r in reversed(rows)]

    async def get_turn(self, turn_id: str) -> dict | None:
        row = await self.db.pool.fetchrow("SELECT * FROM conversation_turn WHERE turn_id = $1", turn_id)
        return dict(row) if row else None

    async def get_turn_by_submission(self, conversation_id: str, submission_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            "SELECT * FROM conversation_turn WHERE conversation_id = $1 AND client_submission_id = $2",
            conversation_id,
            submission_id,
        )
        return dict(row) if row else None

    async def current_running_turn(self, conversation_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            "SELECT * FROM conversation_turn WHERE conversation_id = $1 AND status = 'running'"
            " ORDER BY seq DESC LIMIT 1",
            conversation_id,
        )
        return dict(row) if row else None

    async def list_events(
        self,
        conversation_id: str,
        *,
        after_seq: int = 0,
        turn_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT * FROM conversation_event
             WHERE conversation_id = $1 AND seq > $2
               AND ($3::text IS NULL OR turn_id = $3)
             ORDER BY seq
             LIMIT $4
            """,
            conversation_id,
            after_seq,
            turn_id,
            limit,
        )
        return [event_envelope(r) for r in rows]

    async def get_task_context(self, conversation_id: str, limit: int = 5) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT task_id, status, progress, summary, task_type, updated_at
              FROM task_run
             WHERE conversation_id = $1
             ORDER BY updated_at DESC
             LIMIT $2
            """,
            conversation_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def get_artifacts(self, conversation_id: str, limit: int = 5) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT a.artifact_id, a.name, a.mime
              FROM task_artifact a
              JOIN task_run t ON t.task_id = a.task_id
             WHERE t.conversation_id = $1
             ORDER BY a.created_at DESC
             LIMIT $2
            """,
            conversation_id,
            limit,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ writes
    async def next_seq(self, conn, conversation_id: str, scope: str) -> int:
        value = await conn.fetchval(
            """
            INSERT INTO conversation_seq (conversation_id, scope, last_seq)
            VALUES ($1, $2, 1)
            ON CONFLICT (conversation_id, scope)
            DO UPDATE SET last_seq = conversation_seq.last_seq + 1
            RETURNING last_seq
            """,
            conversation_id,
            scope,
        )
        return int(value)

    async def create_conversation(
        self,
        *,
        tenant_id: str,
        app_user_id: str,
        title: str,
        channel: str,
        draft: bool = False,
    ) -> dict:
        conversation_id = new_conversation_id()
        row = await self.db.pool.fetchrow(
            """
            INSERT INTO conversation
              (conversation_id, tenant_id, app_user_id, title, active_channel, channels, is_draft)
            VALUES ($1, $2, $3, $4, $5, ARRAY[$5]::text[], $6)
            RETURNING conversation_id, title, active_channel, channels, status, created_at, updated_at, is_draft
            """,
            conversation_id,
            tenant_id,
            app_user_id,
            title or "新交互",
            channel,
            draft,
        )
        result = dict(row)
        result.update(
            task_count=0,
            running_task_count=0,
            artifact_count=0,
            last_turn_preview="",
            last_turn_channel=None,
        )
        return result

    async def create_turn(
        self,
        conn,
        *,
        conversation_id: str,
        tenant_id: str,
        app_user_id: str,
        channel: str,
        user_text: str,
        client_submission_id: str | None,
        trace_id: str,
        attachment_fingerprint: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[dict, dict]:
        conversation = await self.get_for_update(conn, conversation_id, tenant_id, app_user_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        has_prior_turns = bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM conversation_turn WHERE conversation_id = $1)",
                conversation_id,
            )
        )
        title = conversation["title"] or ""
        if (
            not has_prior_turns
            and not conversation["title_locked"]
            and title.strip() in ("", "新交互", "语音交互")
        ):
            first_line = next(
                (line.strip() for line in (user_text or "").splitlines() if line.strip()),
                "",
            )
            if first_line:
                title = first_line[:20]
        turn_id = turn_id or new_turn_id()
        turn_seq = await self.next_seq(conn, conversation_id, "turn")
        turn = await conn.fetchrow(
            """
            INSERT INTO conversation_turn
              (turn_id, conversation_id, seq, channel, user_text, status,
               client_submission_id, trace_id, attachment_fingerprint)
            VALUES ($1, $2, $3, $4, $5, 'running', $6, $7, $8)
            RETURNING *
            """,
            turn_id,
            conversation_id,
            turn_seq,
            channel,
            user_text,
            client_submission_id,
            trace_id,
            attachment_fingerprint,
        )
        started = await self.append_event(
            conn,
            conversation_id=conversation_id,
            type="turn.started",
            payload={"channel": channel, "user_text": user_text},
            turn_id=turn_id,
            trace_id=trace_id,
        )
        await conn.execute(
            """
            UPDATE conversation
               SET active_channel = $2,
                   channels = ARRAY(SELECT DISTINCT unnest(channels || ARRAY[$2]::text[])),
                   is_draft = false,
                   title = $3
             WHERE conversation_id = $1
            """,
            conversation_id,
            channel,
            title,
        )
        return dict(turn), started

    async def append_event(
        self,
        conn,
        *,
        conversation_id: str,
        type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict:
        event_id = event_id or new_event_id()
        existing = await conn.fetchrow("SELECT * FROM conversation_event WHERE event_id = $1", event_id)
        if existing is not None:
            return event_envelope(existing, conversation_id=conversation_id)
        seq = await self.next_seq(conn, conversation_id, "event")
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_event
              (event_id, conversation_id, turn_id, task_id, seq, type, payload, trace_id, occurred_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, COALESCE($9, now()))
            RETURNING *
            """,
            event_id,
            conversation_id,
            turn_id,
            task_id,
            seq,
            type,
            payload,
            trace_id,
            occurred_at,
        )
        return event_envelope(row, conversation_id=conversation_id)

    async def update_turn(self, conn, turn_id: str, **fields: Any) -> dict | None:
        allowed = {"assistant_text", "status", "metrics", "ended_at", "trace_id"}
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key in allowed:
                values.append(value)
                sets.append(f"{key} = ${len(values)}")
        if not sets:
            return await self.get_turn(turn_id)
        values.append(turn_id)
        row = await conn.fetchrow(
            f"UPDATE conversation_turn SET {', '.join(sets)} WHERE turn_id = ${len(values)} RETURNING *",
            *values,
        )
        return dict(row) if row else None

    async def list_timeline(
        self, conversation_id: str, *, after_seq: int = 0, limit: int = 50
    ) -> tuple[list[dict], int | None, bool]:
        # Filter hidden lifecycle events in SQL so they never consume a page slot.
        event_rows = await self.db.pool.fetch(
            """
            SELECT * FROM conversation_event
             WHERE conversation_id = $1 AND seq > $2
               AND type NOT IN ('turn.started', 'turn.settled', 'message.final')
             ORDER BY seq
             LIMIT $3
            """,
            conversation_id,
            after_seq,
            limit + 1,
        )
        events = [event_envelope(row) for row in event_rows]
        turn_rows = await self.db.pool.fetch(
            """
            SELECT t.*, e.seq AS started_seq
              FROM conversation_turn t
              JOIN conversation_event e
                ON e.turn_id = t.turn_id AND e.type = 'turn.started'
             WHERE t.conversation_id = $1 AND e.seq > $2
             ORDER BY e.seq
             LIMIT $3
            """,
            conversation_id,
            after_seq,
            limit + 1,
        )
        items: list[dict] = []
        for event in events:
            items.append(
                {
                    "kind": "event",
                    "seq": event["seq"],
                    "occurred_at": event["occurred_at"],
                    "turn_id": event.get("turn_id"),
                    "task_id": event.get("task_id"),
                    "event_id": event["event_id"],
                    "type": event["type"],
                    "payload": event.get("payload") or {},
                }
            )
        for turn in turn_rows:
            items.append(
                {
                    "kind": "turn",
                    "seq": int(turn["started_seq"]),
                    "occurred_at": to_iso(turn["created_at"]),
                    "turn_id": turn["turn_id"],
                    "channel": turn["channel"],
                    "user_text": turn["user_text"],
                    "assistant_text": turn["assistant_text"],
                    "status": turn["status"],
                }
            )
        items.sort(key=lambda item: item["seq"])
        has_more = len(items) > limit
        items = items[:limit]
        next_cursor = items[-1]["seq"] if items and has_more else None
        return items, next_cursor, has_more

    async def get_running_turn(self, conn, conversation_id: str) -> dict | None:
        row = await conn.fetchrow(
            "SELECT * FROM conversation_turn WHERE conversation_id = $1 AND status = 'running'"
            " ORDER BY seq DESC LIMIT 1 FOR UPDATE",
            conversation_id,
        )
        return dict(row) if row else None

    async def get_turn_by_submission_conn(
        self, conn, conversation_id: str, submission_id: str
    ) -> dict | None:
        row = await conn.fetchrow(
            "SELECT * FROM conversation_turn WHERE conversation_id = $1 AND client_submission_id = $2",
            conversation_id,
            submission_id,
        )
        return dict(row) if row else None

    async def get_event_seq(self, event_id: str, conversation_id: str) -> int | None:
        value = await self.db.pool.fetchval(
            "SELECT seq FROM conversation_event WHERE event_id = $1 AND conversation_id = $2",
            event_id,
            conversation_id,
        )
        return int(value) if value is not None else None

    async def purge_drafts(self, ttl_days: int = 7) -> int:
        """Delete abandoned drafts that never produced a turn."""
        status = await self.db.pool.execute(
            """
            DELETE FROM conversation c
             WHERE c.is_draft
               AND c.created_at < now() - ($1::int * interval '1 day')
               AND NOT EXISTS (
                   SELECT 1 FROM conversation_turn t WHERE t.conversation_id = c.conversation_id
               )
               AND NOT EXISTS (
                   SELECT 1 FROM task_run tr WHERE tr.conversation_id = c.conversation_id
               )
            """,
            max(1, int(ttl_days)),
        )
        try:
            return int(status.split()[-1])
        except (AttributeError, IndexError, ValueError):
            return 0

    async def stale_running_turns(self, limit: int = 100) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT * FROM conversation_turn WHERE status = 'running' ORDER BY created_at LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def get_turn_for_update(self, conn, turn_id: str) -> dict | None:
        row = await conn.fetchrow("SELECT * FROM conversation_turn WHERE turn_id = $1 FOR UPDATE", turn_id)
        return dict(row) if row else None

    async def list_context_traces(self, conversation_id: str, limit: int = 20) -> list[dict]:
        rows = await self.db.pool.fetch(
            """
            SELECT trace_id, turn_id, conversation_id, query, intent,
                   sources, degraded, budget, channel, created_at
              FROM context_trace
             WHERE conversation_id = $1
             ORDER BY created_at DESC
             LIMIT $2
            """,
            conversation_id,
            max(1, min(limit, 100)),
        )
        return [dict(row) for row in rows]

    async def set_ov_session_id(self, conversation_id: str, session_id: str) -> str:
        row = await self.db.pool.fetchrow(
            """
            UPDATE conversation
               SET ov_session_id = COALESCE(ov_session_id, $2), updated_at = now()
             WHERE conversation_id = $1
             RETURNING ov_session_id
            """,
            conversation_id,
            session_id,
        )
        return row["ov_session_id"] if row else session_id

    async def bump_ov_message_count(self, conversation_id: str, delta: int = 2) -> int:
        row = await self.db.pool.fetchrow(
            """
            UPDATE conversation
               SET ov_message_count = ov_message_count + $2
             WHERE conversation_id = $1
             RETURNING ov_message_count
            """,
            conversation_id,
            delta,
        )
        return int(row["ov_message_count"]) if row else 0

    async def reset_ov_message_count(self, conversation_id: str) -> None:
        await self.db.pool.execute(
            "UPDATE conversation SET ov_message_count = 0 WHERE conversation_id = $1",
            conversation_id,
        )

    async def get_ov_state(self, conversation_id: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            """
            SELECT conversation_id, tenant_id, app_user_id, title, ov_session_id,
                   ov_message_count, active_channel
              FROM conversation
             WHERE conversation_id = $1
            """,
            conversation_id,
        )
        return dict(row) if row else None
