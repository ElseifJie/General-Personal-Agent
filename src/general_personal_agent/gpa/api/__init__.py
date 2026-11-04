"""GPA HTTP/SSE API routers."""

from general_personal_agent.gpa.api.context import router as context_router
from general_personal_agent.gpa.api.conversations import router as conversations_router
from general_personal_agent.gpa.api.files import router as files_router
from general_personal_agent.gpa.api.internal import oauth_router
from general_personal_agent.gpa.api.internal import router as internal_router
from general_personal_agent.gpa.api.memories import router as memories_router
from general_personal_agent.gpa.api.tasks import router as tasks_router
from general_personal_agent.gpa.files.api import (
    conversation_files_router,
    kb_router,
    uploads_router,
)

__all__ = [
    "context_router",
    "conversation_files_router",
    "conversations_router",
    "files_router",
    "kb_router",
    "uploads_router",
    "internal_router",
    "memories_router",
    "oauth_router",
    "tasks_router",
]
