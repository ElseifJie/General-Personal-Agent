"""Pydantic contracts for the GPA conversation API, agent events and context.

Wire format is camelCase (integration-spec §2); internal context packets are
snake_case JSON per context-management §5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Channel = Literal["text", "voice"]
TurnStatus = Literal["running", "completed", "failed", "cancelled"]
TaskStatusName = Literal[
    "queued", "running", "completed", "partial", "failed", "cancelled"
]

AgentEventType = Literal[
    "turn.started",
    "message.delta",
    "message.final",
    "tool.call",
    "tool.result",
    "task.created",
    "task.status",
    "artifact",
    "turn.settled",
    "error",
]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="ignore",
    )

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# Conversation API
# ---------------------------------------------------------------------------
class ConversationCreate(CamelModel):
    title: str = ""
    channel: Channel = "text"
    draft: bool = False


class ConversationCreated(CamelModel):
    conversation_id: str
    active_channel: Channel
    channels: list[Channel] = Field(default_factory=list)
    title: str = ""
    created_at: datetime


class AttachmentView(CamelModel):
    attachment_id: str = ""
    file_id: str
    name: str = ""
    mime: str | None = None
    bytes: int | None = None
    status: str = "stored"
    text_status: str | None = None
    download_url: str | None = None


class MessageSubmit(CamelModel):
    content: str = Field(min_length=1, max_length=32_000)
    client_submission_id: str | None = None
    channel: Channel = "text"
    attachment_ids: list[str] = Field(default_factory=list)


class MessageAccepted(CamelModel):
    turn_id: str
    status: TurnStatus = "running"
    events_url: str


class StopAccepted(CamelModel):
    turn_id: str
    status: Literal["cancelling", "cancelled", "idle"]


class ConversationSummary(CamelModel):
    conversation_id: str
    title: str = ""
    active_channel: Channel
    channels: list[Channel] = Field(default_factory=list)
    status: str = "active"
    pinned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    is_draft: bool = False
    task_count: int = 0
    running_task_count: int = 0
    artifact_count: int = 0
    last_turn_preview: str = ""
    last_turn_channel: Channel | None = None


class ConversationList(CamelModel):
    items: list[ConversationSummary]
    next_cursor: str | None = None


class ConversationUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None
    archived: bool | None = None


class ConversationMemoryCommit(CamelModel):
    status: str
    message: str


class TurnView(CamelModel):
    turn_id: str
    conversation_id: str
    seq: int
    channel: Channel
    user_text: str = ""
    assistant_text: str = ""
    status: TurnStatus
    trace_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AttachmentView] = Field(default_factory=list)
    created_at: datetime
    ended_at: datetime | None = None


class ConversationDetail(CamelModel):
    conversation: ConversationSummary
    turns: list[TurnView]


class ContextTraceView(CamelModel):
    trace_id: str
    turn_id: str | None = None
    conversation_id: str
    query: str = ""
    intent: str | None = None
    sources: Any = Field(default_factory=list)
    degraded: Any = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    channel: Channel | None = None
    created_at: datetime


class TimelineItem(CamelModel):
    kind: Literal["turn", "event"]
    seq: int
    occurred_at: datetime
    turn_id: str | None = None
    task_id: str | None = None
    # turn fields
    channel: Channel | None = None
    user_text: str | None = None
    assistant_text: str | None = None
    status: str | None = None
    attachments: list[AttachmentView] = Field(default_factory=list)
    # event fields
    event_id: str | None = None
    type: str | None = None
    payload: dict[str, Any] | None = None


class TimelinePage(CamelModel):
    items: list[TimelineItem]
    next_cursor: int | None = None
    has_more: bool = False


# ---------------------------------------------------------------------------
# Agent events (integration-spec §3.2)
# ---------------------------------------------------------------------------
class AgentEvent(CamelModel):
    schema_version: str = "1"
    event_id: str
    seq: int
    type: AgentEventType
    conversation_id: str
    turn_id: str | None = None
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


# ---------------------------------------------------------------------------
# Task Gateway (integration-spec §5.2)
# ---------------------------------------------------------------------------
class ArtifactRef(CamelModel):
    artifact_id: str
    task_id: str
    name: str
    mime: str | None = None
    bytes: int | None = None
    created_at: datetime
    expires_at: datetime | None = None
    download_url: str | None = None


class TaskView(CamelModel):
    task_id: str
    conversation_id: str
    status: TaskStatusName
    progress: float = 0.0
    summary: str = ""
    executor: str = "ma"
    goal: str = ""
    task_type: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Context packet (context-management §5)
# ---------------------------------------------------------------------------
class RecentTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    ts: str


class ContextSource(BaseModel):
    kind: Literal["memory", "kb", "skill", "task", "artifact", "turn"]
    ref: str
    score: float = 1.0


class TaskContextItem(BaseModel):
    task_id: str
    status: str
    progress: float = 0.0
    summary: str = ""
    task_type: str = ""
    updated_at: str | None = None


class ArtifactContextItem(BaseModel):
    artifact_id: str
    name: str
    mime: str | None = None


class TokenBudget(BaseModel):
    total: int
    used: int = 0


class ContextPacket(BaseModel):
    schema_version: str = "1"
    turn_id: str
    conversation_id: str
    channel: Channel
    user_text: str = ""
    recent_turns: list[RecentTurn] = Field(default_factory=list)
    session_summary: str = ""
    # Slices map 1:1 to OpenViking directories: memories/ + peers/ -> memory,
    # user resources/ -> personal_kb, viking://resources -> shared_kb,
    # agent/skills + user/skills -> skills.
    long_term_memory: list[dict[str, Any]] = Field(default_factory=list)
    personal_kb: list[dict[str, Any]] = Field(default_factory=list)
    shared_kb: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    task_context: list[TaskContextItem] = Field(default_factory=list)
    attachments: list[ArtifactContextItem] = Field(default_factory=list)
    token_budget: TokenBudget = Field(default_factory=lambda: TokenBudget(total=4000))
    sources: list[ContextSource] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    trace_id: str = ""
