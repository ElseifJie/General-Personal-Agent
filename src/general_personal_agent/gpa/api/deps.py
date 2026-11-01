"""Shared API dependencies for GPA routers."""

from __future__ import annotations

from fastapi import Request

from general_personal_agent.auth import SessionUser, session_user_from_request
from general_personal_agent.gpa.errors import GpaError


async def current_user(request: Request) -> SessionUser:
    services = request.app.state.services
    user = await session_user_from_request(request, services.settings, services.db)
    if user is None:
        raise GpaError("AUTH_REQUIRED", "login required", retryable=False)
    if not getattr(request.state, "trace_id", ""):
        request.state.trace_id = request.headers.get("X-Trace-Id") or getattr(request.state, "request_id", "")
    return user


def tenant_id(request: Request) -> str:
    return request.app.state.services.settings.ov_tenant_id or "demo"


def gpa_services(request: Request):
    return request.app.state.gpa
