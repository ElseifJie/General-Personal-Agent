"""Test doubles: FakeOVClient (delay/error injection) and FakeDatabase."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4


class FakeOVClient:
    """OpenVikingClient double. Each method can be scripted with delay/error."""

    def __init__(
        self,
        *,
        search_results: dict[str, list[dict]] | None = None,
        search_delay_s: float = 0.0,
        search_error: Exception | None = None,
        session_context_data: dict | None = None,
        session_context_error: Exception | None = None,
        read_text: str = "",
        closed: bool = False,
    ):
        self.search_results = search_results or {"default": []}
        self.search_delay_s = search_delay_s
        self.search_error = search_error
        self.session_context_data = session_context_data or {}
        self.session_context_error = session_context_error
        self.read_text = read_text
        self.calls: list[tuple[str, tuple, dict]] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def _maybe_delay(self) -> None:
        if self.search_delay_s:
            await asyncio.sleep(self.search_delay_s)

    async def search(self, query, target_uri=None, limit=8, context_type=None):
        self.calls.append(("search", (query,), {"target_uri": target_uri, "context_type": context_type}))
        await self._maybe_delay()
        if self.search_error:
            raise self.search_error
        for key, items in self.search_results.items():
            if key in (target_uri or "") or key == "default":
                return {"resources": items, "memories": []}
        return {"resources": [], "memories": []}

    async def session_context(self, session_id: str) -> dict:
        self.calls.append(("session_context", (session_id,), {}))
        if self.session_context_error:
            raise self.session_context_error
        return self.session_context_data

    async def read(self, uri: str) -> str:
        self.calls.append(("read", (uri,), {}))
        return self.read_text

    async def add_message(self, session_id, role, content) -> dict:
        self.calls.append(("add_message", (session_id, role, content), {}))
        return {}

    async def commit_session(self, session_id: str) -> dict:
        self.calls.append(("commit", (session_id,), {}))
        return {}

    async def create_session(self, actor_peer_id=None) -> str:
        return "ovs_fake_1"

    async def import_bytes(self, filename, data, to_uri, content_type="text/markdown", wait=True) -> dict:
        self.calls.append(("import_bytes", (to_uri,), {"filename": filename, "wait": wait}))
        return {"status": "success", "task_id": "task_fake_1"}

    async def delete(self, uri, recursive=True) -> dict:
        self.calls.append(("delete", (uri,), {"recursive": recursive}))
        return {}

    async def task(self, task_id: str) -> dict:
        return {"status": "completed"}


class FakeDatabase:
    """In-memory Database double implementing the methods used by the code."""

    def __init__(self):
        self.outbox: dict[int, dict[str, Any]] = {}
        self._outbox_seq = 0
        self.conversation_seq: dict[str, int] = {}
        self.memory: dict[str, dict[str, Any]] = {}
        self.memory_events: list[dict] = []
        self.traces: list[dict] = []
        self.agent_turns: list[dict] = []
        self.agent_sessions: dict[str, dict] = {}
        self.kb_docs: dict[str, dict[str, Any]] = {}
        self.kb_audit: list[dict] = []
        self.transcripts: list[dict] = []
        self.tasks_active: list[dict] = []
        self.identities: dict[tuple[str, str], dict] = {}

    # identity
    async def get_identity(self, tenant_id, app_user_id):
        return self.identities.get((tenant_id, app_user_id))

    # transcript
    async def add_transcript(self, tenant_id, app_user_id, session_id, role, text, source="voice"):
        self.transcripts.append(
            {"tenant_id": tenant_id, "app_user_id": app_user_id, "session_id": session_id,
             "role": role, "text": text, "source": source}
        )

    async def recent_transcript(self, session_id, limit=20):
        rows = [t for t in self.transcripts if t["session_id"] == session_id]
        return rows[-limit:]

    # agent sessions
    async def get_active_conversation(self, app_user_id, device_id, idle_minutes):
        rows = [
            s for s in self.agent_sessions.values()
            if s["app_user_id"] == app_user_id and s["device_id"] == device_id and s["status"] == "active"
        ]
        return rows[-1] if rows else None

    async def create_agent_session(self, row):
        self.agent_sessions[row["conversation_id"]] = {
            **row, "status": "active", "ov_session_id": row.get("ov_session_id"),
        }

    async def touch_agent_session(self, conversation_id, ov_session_id=None):
        if conversation_id in self.agent_sessions and ov_session_id:
            self.agent_sessions[conversation_id]["ov_session_id"] = ov_session_id

    async def close_agent_session(self, conversation_id):
        if conversation_id in self.agent_sessions:
            self.agent_sessions[conversation_id]["status"] = "closed"

    async def add_agent_turn(self, turn):
        self.agent_turns.append(dict(turn))

    async def add_context_trace(self, trace):
        self.traces.append(dict(trace))

    async def purge_context_traces(self, ttl_days):
        self.traces.clear()

    # outbox
    async def enqueue_outbox(self, item):
        for existing in self.outbox.values():
            if existing["idempotency_key"] == item["idempotency_key"]:
                return False
        conversation_id = item.get("conversation_id", "")
        seq = item.get("seq")
        if not seq:
            seq = 1 + max(
                (row["seq"] for row in self.outbox.values()
                 if row["conversation_id"] == conversation_id),
                default=0,
            )
        self._outbox_seq += 1
        self.outbox[self._outbox_seq] = {
            "id": self._outbox_seq,
            "tenant_id": item["tenant_id"],
            "app_user_id": item["app_user_id"],
            "conversation_id": conversation_id,
            "kind": item["kind"],
            "payload": dict(item.get("payload") or {}),
            "idempotency_key": item["idempotency_key"],
            "seq": seq,
            "status": "pending",
            "attempts": 0,
            "next_at": 0.0,
            "last_error": None,
            "lease_until": None,
            "claim_token": None,
        }
        return True

    async def next_outbox_seq(self, conversation_id):
        self.conversation_seq[conversation_id] = self.conversation_seq.get(conversation_id, 0) + 1
        return self.conversation_seq[conversation_id]

    async def claim_outbox(self, limit):
        now = time.monotonic()
        claimed = []
        token = uuid4().hex
        blocked: set[str] = set()
        claimed_conversations: set[str] = set()
        for row in sorted(self.outbox.values(), key=lambda r: (r["conversation_id"], r["seq"], r["id"])):
            if len(claimed) >= limit:
                break
            conversation_id = row["conversation_id"]
            if conversation_id in blocked or conversation_id in claimed_conversations:
                continue
            if row["status"] in {"done", "dead"}:
                continue
            if row["status"] == "pending" and row["next_at"] <= now:
                row["status"] = "inflight"
                row["claim_token"] = token
                row["lease_until"] = now + 120
                claimed.append(dict(row))
                claimed_conversations.add(conversation_id)
                continue
            if row["status"] == "inflight" and (
                row["lease_until"] is None or row["lease_until"] < now
            ):
                row["status"] = "inflight"
                row["claim_token"] = token
                row["lease_until"] = now + 120
                claimed.append(dict(row))
                claimed_conversations.add(conversation_id)
                continue
            # Earlier pending/inflight row blocks later rows in this conversation.
            blocked.add(conversation_id)
        return claimed

    async def complete_outbox(self, outbox_id, claim_token=None):
        row = self.outbox[outbox_id]
        if claim_token is not None and row.get("claim_token") != claim_token:
            return False
        row["status"] = "done"
        row["lease_until"] = None
        row["claim_token"] = None
        return True

    async def fail_outbox(self, outbox_id, attempts, delay_s, error, dead, claim_token=None):
        row = self.outbox[outbox_id]
        if claim_token is not None and row.get("claim_token") != claim_token:
            return False
        row["attempts"] = attempts
        row["next_at"] = time.monotonic() + delay_s
        row["last_error"] = error
        row["status"] = "dead" if dead else "pending"
        row["lease_until"] = None
        row["claim_token"] = None
        return True

    async def list_outbox(self, conversation_id):
        return [dict(r) for r in sorted(self.outbox.values(), key=lambda r: r["id"])
                if r["conversation_id"] == conversation_id]

    async def pending_outbox_count(self):
        return sum(1 for r in self.outbox.values() if r["status"] in ("pending", "inflight"))

    # memory
    async def upsert_memory_item(self, item):
        for existing in self.memory.values():
            if existing["app_user_id"] == item["app_user_id"] and existing["memory_key"] == item["memory_key"]:
                existing.update({
                    "summary": item["summary"], "type": item["type"],
                    "confidence": item.get("confidence", 1.0), "source": item.get("source", "explicit"),
                    "status": "active", "deleted_at": None,
                    "pinned": existing.get("pinned", False) or item.get("pinned", False),
                    "ov_uri": item.get("ov_uri") or existing.get("ov_uri"),
                })
                return dict(existing)
        memory_id = item["memory_id"]
        self.memory[memory_id] = {
            **item, "status": "active", "deleted_at": None,
            "updated_at": "2026-10-24T00:00:00+00:00",
        }
        return dict(self.memory[memory_id])

    async def tombstone_memory_item(self, app_user_id, memory_id=None):
        count = 0
        for item in self.memory.values():
            if item["app_user_id"] != app_user_id or item["status"] == "deleted":
                continue
            if memory_id is None or item["memory_id"] == memory_id:
                item["status"] = "deleted"
                item["deleted_at"] = "2026-10-24T00:00:00+00:00"
                count += 1
        return count

    async def get_memory_item(self, memory_id):
        item = self.memory.get(memory_id)
        return dict(item) if item else None

    async def update_memory_ov_uri(self, memory_id, ov_uri):
        if memory_id in self.memory:
            self.memory[memory_id]["ov_uri"] = ov_uri

    async def list_memory_items(self, app_user_id, include_deleted=False, limit=200):
        rows = [dict(i) for i in self.memory.values()
                if i["app_user_id"] == app_user_id and (include_deleted or i["status"] != "deleted")]
        rows.sort(key=lambda i: (not i.get("pinned", False),))
        return rows[:limit]

    async def add_memory_event(self, event):
        self.memory_events.append(dict(event))

    # kb
    async def create_kb_doc(self, doc):
        self.kb_docs[doc["doc_id"]] = {
            **doc, "phase": doc.get("phase", "processing"), "tos_key": None,
            "content_hash": None, "deleted_at": None,
            "created_at": "2026-10-24T00:00:00+00:00",
        }

    async def update_kb_doc(self, doc_id, **fields):
        allowed = {"status", "ov_task_id", "error", "tos_key", "content_hash"}
        for key, value in fields.items():
            if key in allowed:
                self.kb_docs[doc_id][key] = value

    async def update_kb_doc_phase(self, doc_id, phase, **fields):
        self.kb_docs[doc_id]["phase"] = phase
        if fields:
            await self.update_kb_doc(doc_id, **fields)

    async def get_kb_doc(self, doc_id):
        doc = self.kb_docs.get(doc_id)
        return dict(doc) if doc else None

    async def find_kb_doc_by_hash(self, app_user_id, content_hash):
        for doc in self.kb_docs.values():
            if doc["app_user_id"] == app_user_id and doc.get("content_hash") == content_hash and doc.get("deleted_at") is None:
                return dict(doc)
        return None

    async def list_kb_docs(self, app_user_id, scope="personal", include_deleted=False):
        return [dict(d) for d in self.kb_docs.values()
                if d["app_user_id"] == app_user_id and d["scope"] == scope
                and (include_deleted or d.get("deleted_at") is None)]

    async def count_kb_docs(self, app_user_id, scope="personal"):
        return sum(1 for d in self.kb_docs.values()
                   if d["app_user_id"] == app_user_id and d["scope"] == scope and d.get("deleted_at") is None)

    async def soft_delete_kb_doc(self, doc_id):
        self.kb_docs[doc_id]["deleted_at"] = "2026-10-24T00:00:00+00:00"
        self.kb_docs[doc_id]["phase"] = "deleted"

    async def add_kb_audit(self, entry):
        self.kb_audit.append(dict(entry))

    # tasks
    async def list_active_tasks(self, app_user_id, limit=2):
        return list(self.tasks_active[:limit])
