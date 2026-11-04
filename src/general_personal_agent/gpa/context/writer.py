"""Context write-back: GPA turn -> OV session/outbox -> long-term memory.

Writes are asynchronous and idempotent. The outbox is the retry boundary; this
class only resolves the conversation's OV session and enqueues work.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from general_personal_agent.clients.ov import OpenVikingClient
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.outbox import enqueue

logger = get_logger(__name__)


class GpaContextWriter:
    def __init__(self, settings, db, store, identity_service):
        self.settings = settings
        self.db = db
        self.store = store
        self.identity_service = identity_service
        self._clients: dict[str, OpenVikingClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.identity_service is not None and getattr(self.settings, "memory_enabled", True))

    def schedule_turn(self, conversation_id: str, turn_id: str) -> None:
        if not self.enabled:
            return
        task = asyncio.create_task(
            self._record(conversation_id, turn_id),
            name=f"gpa-context-write-{turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _client(self, app_user_id: str, identity) -> OpenVikingClient:
        client = self._clients.get(app_user_id)
        if client is None:
            client = OpenVikingClient(self.settings.ov_url, identity.api_key, self.settings.ov_timeout_s)
            self._clients[app_user_id] = client
        return client

    async def _record(self, conversation_id: str, turn_id: str) -> None:
        try:
            state = await self.store.get_ov_state(conversation_id)
            if state is None:
                return
            turn = await self.db.pool.fetchrow(
                """
                SELECT turn_id, seq, status, user_text, assistant_text
                  FROM conversation_turn
                 WHERE turn_id = $1 AND conversation_id = $2
                """,
                turn_id,
                conversation_id,
            )
            if turn is None or turn["status"] != "completed":
                return
            identity = await self.identity_service.resolve(state["app_user_id"], state["tenant_id"])
            client = await self._client(state["app_user_id"], identity)
            session_id = state.get("ov_session_id")
            if not session_id:
                lock = self._locks.setdefault(conversation_id, asyncio.Lock())
                async with lock:
                    state = await self.store.get_ov_state(conversation_id)
                    session_id = state.get("ov_session_id") if state else None
                    if not session_id:
                        session_id = await client.create_session(identity.ov_peer)
                        session_id = await self.store.set_ov_session_id(conversation_id, session_id)
            user_text = (turn["user_text"] or "").strip()
            assistant_text = (turn["assistant_text"] or "").strip()
            if user_text:
                await self._enqueue(
                    state,
                    "ov.message",
                    {
                        "app_user_id": state["app_user_id"],
                        "session_id": session_id,
                        "role": "user",
                        "content": user_text[:4_000],
                        "turn_id": turn_id,
                    },
                    f"gpa.ov.message:{turn_id}:user",
                )
            if assistant_text:
                await self._enqueue(
                    state,
                    "ov.message",
                    {
                        "app_user_id": state["app_user_id"],
                        "session_id": session_id,
                        "role": "assistant",
                        "content": assistant_text[:4_000],
                        "turn_id": turn_id,
                    },
                    f"gpa.ov.message:{turn_id}:assistant",
                )
            count = await self.store.bump_ov_message_count(conversation_id, 2 if assistant_text else 1)
            commit_every = max(2, int(self.settings.memory_commit_every) * 2)
            if count >= commit_every:
                enqueued = await self._enqueue(
                    state,
                    "ov.commit",
                    {
                        "app_user_id": state["app_user_id"],
                        "session_id": session_id,
                        "turn_id": turn_id,
                    },
                    f"gpa.ov.commit:{turn_id}",
                )
                if enqueued:
                    await self.store.reset_ov_message_count(conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - memory must never break a turn
            logger.warning(
                "gpa context write skipped conversation=%s turn=%s: %s", conversation_id, turn_id, exc
            )

    async def _enqueue(self, state: dict[str, Any], kind: str, payload: dict, key: str) -> bool:
        return await enqueue(
            self.db,
            state["tenant_id"],
            state["app_user_id"],
            state["conversation_id"],
            kind,
            payload,
            key,
        )

    async def commit_now(self, conversation_id: str) -> bool:
        """Queue an explicit commit for the conversation's OV session."""
        if not self.enabled:
            return False
        state = await self.store.get_ov_state(conversation_id)
        if state is None or not state.get("ov_session_id"):
            return False
        enqueued = await self._enqueue(
            state,
            "ov.commit",
            {
                "app_user_id": state["app_user_id"],
                "session_id": state["ov_session_id"],
                "turn_id": None,
            },
            f"gpa.ov.commit:manual:{conversation_id}:{uuid4().hex}",
        )
        if enqueued:
            await self.store.reset_ov_message_count(conversation_id)
        return enqueued

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
