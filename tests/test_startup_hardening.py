"""Startup hardening: refuse empty signing key."""

from __future__ import annotations

import pytest

from general_personal_agent.main import validate_startup_settings
from general_personal_agent.settings import Settings


def test_empty_secret_key_rejected() -> None:
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        validate_startup_settings(Settings(app_secret_key=""))


def test_non_empty_secret_key_accepted() -> None:
    validate_startup_settings(Settings(app_secret_key="test-secret"))
