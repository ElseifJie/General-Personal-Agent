"""CamelCase DTOs for the file/knowledge API."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from general_personal_agent.gpa.schemas import CamelModel


class FileObjectView(CamelModel):
    file_id: str
    name: str
    mime: str | None = None
    bytes: int | None = None
    status: str
    text_status: str | None = None
    text_chars: int = 0
    text_truncated: bool = False
    page_count: int | None = None
    error: str | None = None
    created_at: datetime
    download_url: str


class ConversationFileView(CamelModel):
    conversation_file_id: str
    file_id: str
    name: str = ""
    mime: str | None = None
    bytes: int | None = None
    status: str = "active"
    text_status: str | None = None
    text_chars: int = 0
    text_truncated: bool = False
    page_count: int | None = None
    error: str | None = None
    download_url: str | None = None


class ConversationFileList(CamelModel):
    items: list[ConversationFileView] = Field(default_factory=list)


class ConversationFileBind(CamelModel):
    file_ids: list[str] = Field(default_factory=list)


class KbEntryView(CamelModel):
    uri: str
    path: str = ""
    name: str = ""
    is_dir: bool = False
    size: int | None = None
    updated_at: datetime | None = None
    doc_id: str | None = None
    status: str = "ready"
    scope: str = "private"
    mime: str | None = None
    download_url: str | None = None
    error: str | None = None
    actions: list[str] = Field(default_factory=list)


class KbEntryList(CamelModel):
    scope: str
    root_uri: str
    items: list[KbEntryView] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


class KbContent(CamelModel):
    uri: str
    content: str
    truncated: bool = False


class KbDeletionList(CamelModel):
    items: list[KbEntryView] = Field(default_factory=list)


class PromotionResult(CamelModel):
    doc_id: str
    dedupe: bool = False


