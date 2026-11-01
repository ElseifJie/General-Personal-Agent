"""Conversation API + SSE (integration-spec §2)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from general_personal_agent.auth import SessionUser
from general_personal_agent.gpa.api.deps import current_user, gpa_services, tenant_id
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.schemas import (
    ContextTraceView,
    ConversationCreate,
    ConversationCreated,
    ConversationDetail,
    ConversationList,
    ConversationMemoryCommit,
    ConversationSummary,
    ConversationUpdate,
    MessageAccepted,
    MessageSubmit,
    StopAccepted,
    TimelineItem,
    TimelinePage,
    TurnView,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["gpa-conversations"])

HEARTBEAT_S = 15.0
AUTH_RECHECK_S = 60.0


@router.post("", response_model=ConversationCreated)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    conversation = await gpa_services(request).conversations.create(
        tenant_id=tenant_id(request),
        app_user_id=user.app_user_id,
        title=body.title,
        channel=body.channel,
        draft=body.draft,
    )
    return ConversationCreated(
        conversation_id=conversation["conversation_id"],
        active_channel=conversation["active_channel"],
        channels=conversation["channels"],
        title=conversation["title"],
        created_at=conversation["created_at"],
    )


@router.get("", response_model=ConversationList)
async def list_conversations(
    request: Request,
    user: SessionUser = Depends(current_user),
    channel: Literal["text", "voice"] | None = Query(default=None),
    query: str | None = Query(default=None, max_length=200),
    archived: bool = Query(default=False),
    has_running_task: bool = Query(default=False),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
):
    items, next_cursor = await gpa_services(request).conversations.list(
        tenant_id=tenant_id(request),
        app_user_id=user.app_user_id,
        channel=channel,
        query=query,
        archived=archived,
        has_running_task=has_running_task,
        cursor=cursor,
        limit=limit,
    )
    return ConversationList(
        items=[ConversationSummary(**item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    data = await gpa_services(request).conversations.detail(
        conversation_id, tenant_id(request), user.app_user_id
    )
    return ConversationDetail(
        conversation=ConversationSummary(**data["conversation"]),
        turns=[TurnView(**turn) for turn in data["turns"]],
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    conversation = await gpa_services(request).conversations.update(
        conversation_id,
        tenant_id(request),
        user.app_user_id,
        title=body.title,
        pinned=body.pinned,
        archived=body.archived,
    )
    return ConversationSummary(**conversation)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    await gpa_services(request).conversations.delete(
        conversation_id, tenant_id(request), user.app_user_id
    )


@router.post(
    "/{conversation_id}/memory/commit",
    response_model=ConversationMemoryCommit,
    status_code=202,
)
async def commit_conversation_memory(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    data = await gpa_services(request).conversations.commit_memory(
        conversation_id, tenant_id(request), user.app_user_id
    )
    return ConversationMemoryCommit(**data)


@router.get("/{conversation_id}/timeline", response_model=TimelinePage)
async def get_timeline(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    data = await gpa_services(request).conversations.timeline(
        conversation_id,
        tenant_id(request),
        user.app_user_id,
        after_seq=after_seq,
        limit=limit,
    )
    return TimelinePage(
        items=[TimelineItem(**item) for item in data["items"]],
        next_cursor=data.get("next_cursor"),
        has_more=bool(data.get("has_more")),
    )


@router.get("/{conversation_id}/context-traces", response_model=list[ContextTraceView])
async def list_context_traces(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
    limit: int = Query(default=20, ge=1, le=100),
):
    gpa = gpa_services(request)
    await gpa.conversations.require_conversation(conversation_id, tenant_id(request), user.app_user_id)
    traces = await gpa.store.list_context_traces(conversation_id, limit=limit)
    return [ContextTraceView(**trace) for trace in traces]


@router.post("/{conversation_id}/messages", response_model=MessageAccepted, status_code=202)
async def submit_message(
    conversation_id: str,
    body: MessageSubmit,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    gpa = gpa_services(request)
    if not await gpa.rate_limiter.allow(
        f"messages:{user.app_user_id}",
        int(gpa.settings.gpa_rate_limit_messages_per_min),
    ):
        raise GpaError("RATE_LIMITED", "too many messages; retry shortly", retryable=True)
    accepted = await gpa.conversations.submit_text(
        conversation_id,
        tenant_id(request),
        user.app_user_id,
        content=body.content,
        client_submission_id=body.client_submission_id,
        channel=body.channel,
        attachment_ids=body.attachment_ids,
    )
    return MessageAccepted(**accepted)


@router.post("/{conversation_id}/stop", response_model=StopAccepted)
async def stop_turn(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
):
    result = await gpa_services(request).conversations.stop(
        conversation_id, tenant_id(request), user.app_user_id
    )
    return StopAccepted(**result)


# ---------------------------------------------------------------------------
# SSE: replay persisted events, then follow live bus deltas
# ---------------------------------------------------------------------------
def _frame(event_id: str | None, event_type: str, data: dict[str, Any]) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


@router.get("/{conversation_id}/events")
async def conversation_events(
    conversation_id: str,
    request: Request,
    user: SessionUser = Depends(current_user),
    turn_id: str | None = Query(default=None, alias="turnId"),
    after_seq: int = Query(default=0, ge=0),
):
    gpa = gpa_services(request)
    tenant = tenant_id(request)
    await gpa.conversations.require_conversation(conversation_id, tenant, user.app_user_id)

    last_event_id = request.headers.get("Last-Event-ID") or request.query_params.get("lastEventId")
    # On reconnect the browser sends Last-Event-ID; it is newer than the
    # initial after_seq cursor and must win.  On a fresh connection after_seq
    # skips the already-loaded history and avoids a replay-refresh storm.
    cursor = 0
    if last_event_id:
        if last_event_id.isdigit():
            cursor = int(last_event_id)
        else:
            found = await gpa.conversations.store.get_event_seq(last_event_id, conversation_id)
            if found is not None:
                cursor = found
    elif after_seq:
        cursor = after_seq
    user_id = user.app_user_id

    async def stream():
        queue, unsubscribe = gpa.bus.subscribe(conversation_id)
        replayed: set[str] = set()
        last_seq = cursor

        async def durable():
            """Yield persisted events strictly in conversation_event order."""
            nonlocal last_seq
            while True:
                try:
                    events = await gpa.conversations.store.list_events(
                        conversation_id,
                        after_seq=last_seq,
                        turn_id=turn_id,
                        limit=200,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("SSE durable read failed: %s", exc)
                    return
                if not events:
                    return
                for event in events:
                    event_id = event["event_id"]
                    if event_id in replayed:
                        continue
                    replayed.add(event_id)
                    last_seq = int(event.get("seq") or 0)
                    yield _frame(event_id, event["type"], event)
                if len(events) < 200:
                    return

        try:
            yield "retry: 3000\n\n"
            yield _frame(None, "ready", {"conversationId": conversation_id})
            async for frame in durable():
                yield frame
            last_recheck = asyncio.get_running_loop().time()
            last_heartbeat = last_recheck
            while True:
                if await request.is_disconnected():
                    return
                now = asyncio.get_running_loop().time()
                if now - last_recheck >= AUTH_RECHECK_S:
                    last_recheck = now
                    from general_personal_agent.auth import session_user_from_request

                    fresh = await session_user_from_request(
                        request, request.app.state.services.settings, request.app.state.services.db
                    )
                    if fresh is None or fresh.app_user_id != user_id:
                        yield _frame(None, "auth_invalid", {"reason": "AUTH_EXPIRED"})
                        return
                if now - last_heartbeat >= HEARTBEAT_S:
                    last_heartbeat = now
                    yield ": ping\n\n"
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    message = None
                if message is None:
                    # Bus is only a wakeup; the durable log is the source of
                    # truth and may contain a gap the bus never delivered.
                    async for frame in durable():
                        yield frame
                    continue
                event_id = message.get("event_id")
                if message.get("type") == "message.delta":
                    # Deltas are transient (never persisted).  They may be
                    # sent immediately but must not move the durable cursor.
                    if turn_id and message.get("turn_id") and message["turn_id"] != turn_id:
                        continue
                    if event_id:
                        replayed.add(event_id)
                    yield _frame(event_id, "message.delta", message)
                    continue
                # Persisted event: never forward the bus copy directly.
                # Drain from the cursor so a 500-event replay limit or a
                # cross-process publisher cannot skip the events in between.
                async for frame in durable():
                    yield frame
        finally:
            unsubscribe()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
