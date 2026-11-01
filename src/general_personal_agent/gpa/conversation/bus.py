"""In-process conversation fan-out for SSE.

The database is the source of truth: replay comes from ``conversation_event``;
this bus only wakes live subscribers. Single-worker baseline (documented in the
execution plan), so a process-local bus is sufficient.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)


class ConversationBus:
    def __init__(self, max_queue: int = 500):
        self.max_queue = max_queue
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, conversation_id: str) -> tuple[asyncio.Queue, Any]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
        self._subscribers[conversation_id].add(queue)

        def unsubscribe() -> None:
            self._subscribers[conversation_id].discard(queue)
            if not self._subscribers[conversation_id]:
                self._subscribers.pop(conversation_id, None)

        return queue, unsubscribe

    def publish(self, conversation_id: str, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(conversation_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("conversation bus queue full conversation=%s", conversation_id)

    def subscriber_count(self, conversation_id: str) -> int:
        return len(self._subscribers.get(conversation_id, ()))
