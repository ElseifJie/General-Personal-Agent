"""Postgres-backed short-term memory for the GPA VeADK runtime."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)


def build_short_term_memory(settings):
    """Build ADK STM in its own schema; fall back to None on any wiring error."""
    if settings.agent_stm_backend != "postgresql":
        return None
    try:
        parsed = urlparse(settings.db_dsn)
        env = {
            "DATABASE_POSTGRESQL_HOST": parsed.hostname or "127.0.0.1",
            "DATABASE_POSTGRESQL_PORT": str(parsed.port or 5432),
            "DATABASE_POSTGRESQL_USER": parsed.username or "demo",
            "DATABASE_POSTGRESQL_PASSWORD": parsed.password or "",
            "DATABASE_POSTGRESQL_DATABASE": (parsed.path or "/general_personal_agent").lstrip("/"),
            "DATABASE_POSTGRESQL_SCHEMA": settings.agent_stm_schema,
        }
        for key, value in env.items():
            os.environ.setdefault(key, value)
        from veadk.memory import ShortTermMemory

        return ShortTermMemory(backend="postgresql")
    except Exception as exc:  # noqa: BLE001 - never block startup on STM wiring
        logger.warning("postgres STM unavailable, falling back to local: %s", exc)
        return None
