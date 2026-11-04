"""Single-active voice session per conversation."""

from __future__ import annotations

from typing import Any


class VoiceSessionRegistry:
    """In-process guard against two live voice sockets for one conversation.

    The registry is intentionally per-process (like the in-process rate
    limiter). Multi-process deployments need a shared lease (Redis) before
    scaling the WS tier horizontally.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    def acquire(self, conversation_id: str, session: Any) -> bool:
        if conversation_id in self._sessions:
            return False
        self._sessions[conversation_id] = session
        return True

    def release(self, conversation_id: str, session: Any) -> None:
        if self._sessions.get(conversation_id) is session:
            self._sessions.pop(conversation_id, None)

    def get(self, conversation_id: str) -> Any | None:
        return self._sessions.get(conversation_id)

    def __len__(self) -> int:
        return len(self._sessions)
