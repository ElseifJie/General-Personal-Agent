"""AgentTransport contract and the in-process implementation (ADR-005).

Only ``InProcessTransport`` is in the MVP. A2A keeps the same envelope but is
deferred until a second agent/process exists.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from general_personal_agent.gpa.ids import new_event_id
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.schemas import AgentEvent, AgentEventType, Channel

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgentSessionRequest:
    conversation_id: str
    tenant_id: str
    app_user_id: str
    channel: Channel = "text"


@dataclass(frozen=True)
class AgentTurnRequest:
    turn_id: str
    conversation_id: str
    user_text: str
    channel: Channel = "text"
    trace_id: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CancelAck:
    turn_id: str
    acked: bool
    latency_ms: int = 0


@dataclass
class AgentSession:
    request: AgentSessionRequest
    runtime: Any
    opened_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    running: int = 0


class AgentRuntimeProtocol(Protocol):
    async def start(self) -> None: ...

    def stream(self, turn: AgentTurnRequest) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, turn_id: str, reason: str) -> None: ...

    async def close(self) -> None: ...


class AgentTransport(Protocol):
    async def open(self, request: AgentSessionRequest) -> AgentSession: ...

    def send(self, session: AgentSession, turn: AgentTurnRequest) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, session: AgentSession, turn_id: str, reason: str) -> CancelAck: ...

    async def close(self, session: AgentSession) -> None: ...

    async def close_all(self) -> None: ...

    async def health(self) -> dict[str, Any]: ...


class EventStream:
    """Per-turn envelope factory: event_id and transport seq (turn-scoped)."""

    def __init__(self, *, conversation_id: str, turn_id: str, trace_id: str = ""):
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.trace_id = trace_id
        self._seq = 0

    def emit(self, type: AgentEventType, payload: dict[str, Any] | None = None) -> AgentEvent:
        self._seq += 1
        return AgentEvent(
            event_id=new_event_id(),
            seq=self._seq,
            type=type,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            payload=payload or {},
            occurred_at=datetime.now(UTC),
        )


RuntimeFactory = Callable[[AgentSessionRequest], Awaitable[AgentRuntimeProtocol]]


class InProcessTransport:
    def __init__(
        self,
        factory: RuntimeFactory,
        *,
        mode: str = "veadk",
        ttl_s: float = 1800.0,
        max_sessions: int = 200,
    ):
        self.factory = factory
        self.mode = mode
        self.ttl_s = max(60.0, float(ttl_s))
        self.max_sessions = max(1, int(max_sessions))
        self._sessions: dict[str, AgentSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _evict_idle(self) -> None:
        now = time.monotonic()
        stale = [
            cid for cid, session in self._sessions.items()
            if session.running == 0 and now - session.last_used > self.ttl_s
        ]
        for cid in stale:
            session = self._sessions.get(cid)
            if session is not None:
                await self.close(session)
        if len(self._sessions) < self.max_sessions:
            return
        idle = sorted(
            (s for s in self._sessions.values() if s.running == 0),
            key=lambda s: s.last_used,
        )
        for session in idle[: max(0, len(self._sessions) - self.max_sessions + 1)]:
            await self.close(session)

    async def open(self, request: AgentSessionRequest) -> AgentSession:
        existing = self._sessions.get(request.conversation_id)
        if existing is not None:
            existing.last_used = time.monotonic()
            return existing
        lock = self._locks.setdefault(request.conversation_id, asyncio.Lock())
        async with lock:
            existing = self._sessions.get(request.conversation_id)
            if existing is not None:
                existing.last_used = time.monotonic()
                return existing
            await self._evict_idle()
            runtime = await self.factory(request)
            await runtime.start()
            session = AgentSession(request=request, runtime=runtime)
            self._sessions[request.conversation_id] = session
            return session

    async def send(self, session: AgentSession, turn: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        session.running += 1
        session.last_used = time.monotonic()
        try:
            async for event in session.runtime.stream(turn):
                session.last_used = time.monotonic()
                yield event
        finally:
            session.running = max(0, session.running - 1)
            session.last_used = time.monotonic()

    async def cancel(self, session: AgentSession, turn_id: str, reason: str) -> CancelAck:
        started = time.monotonic()
        await session.runtime.cancel(turn_id, reason)
        return CancelAck(turn_id=turn_id, acked=True, latency_ms=int((time.monotonic() - started) * 1000))

    async def close(self, session: AgentSession) -> None:
        self._sessions.pop(session.request.conversation_id, None)
        try:
            await session.runtime.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent runtime close failed: %s", exc)

    async def close_conversation(self, conversation_id: str) -> None:
        session = self._sessions.get(conversation_id)
        if session is not None:
            await self.close(session)
        self._locks.pop(conversation_id, None)

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await self.close(session)

    async def health(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sessions": len(self._sessions),
            "ttl_s": self.ttl_s,
            "max_sessions": self.max_sessions,
        }
