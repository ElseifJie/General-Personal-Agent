"""Internal service-token API (MCP gateway precursor).

External MCP transport is still deferred; this endpoint gives internal workers
a minimal authenticated task-gateway surface with rotating static tokens.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request

from general_personal_agent.gpa.api.deps import gpa_services, tenant_id
from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.schemas import CamelModel, TaskView
from general_personal_agent.gpa.service_token import (
    ALL_SCOPES,
    issue_service_token,
    verify_service_token_async,
)

SERVICE_TOKEN_HEADER = "X-GPA-Service-Token"


def _require_user(request: Request, user_id: str) -> None:
    claims = getattr(request.state, "service_claims", None)
    if claims is None:
        raise GpaError("AUTH_REQUIRED", "invalid service token")
    if claims.allowed_users is not None and user_id not in claims.allowed_users:
        raise GpaError("FORBIDDEN", "service token is not allowed to act for this user")


class InternalTaskSubmit(CamelModel):
    tenant_id: str = "demo"
    app_user_id: str
    conversation_id: str
    goal: str
    task_type: str = "general"
    brief: dict[str, Any] | None = None
    origin_turn_id: str | None = None
    idempotency_key: str | None = None
    executor: str = "ma"


async def require_service_token(request: Request) -> None:
    settings = request.app.state.services.settings
    provided = request.headers.get(SERVICE_TOKEN_HEADER, "")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:]
    claims = await verify_service_token_async(settings, provided, request.app.state.services.db)
    if claims is None:
        if not (
            getattr(settings, "gpa_service_token", "") or getattr(settings, "gpa_service_jwt_secret", "")
        ):
            raise GpaError("UPSTREAM_UNAVAILABLE", "internal service token is not configured")
        raise GpaError("AUTH_REQUIRED", "invalid service token")
    request.state.service_claims = claims


def _require_scope(request: Request, scope: str) -> None:
    claims = getattr(request.state, "service_claims", None)
    if claims is None or (scope not in claims.scopes and "*" not in claims.scopes):
        raise GpaError("FORBIDDEN", f"service token missing scope {scope}")


router = APIRouter(
    prefix="/api/internal",
    tags=["gpa-internal"],
    dependencies=[Depends(require_service_token)],
)

oauth_router = APIRouter(prefix="/api/internal/oauth", tags=["gpa-internal"])


@oauth_router.post("/token")
async def oauth_token(
    request: Request,
    grant_type: str = Form(default="client_credentials"),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    scope: str = Form(default=""),
    tenant: str = Form(default=""),
    users: str = Form(default=""),
):
    settings = request.app.state.services.settings
    expected_id = getattr(settings, "gpa_oauth_client_id", "")
    expected_secret = getattr(settings, "gpa_oauth_client_secret", "")
    if not expected_id or not expected_secret:
        raise GpaError("UPSTREAM_UNAVAILABLE", "OAuth client is not configured")
    if grant_type != "client_credentials":
        raise GpaError("VALIDATION_ERROR", "unsupported grant_type")
    gpa = getattr(request.app.state, "gpa", None)
    if gpa is not None:
        ip = request.client.host if request.client else "unknown"
        if not await gpa.rate_limiter.allow(
            f"oauth:{client_id}:{ip}",
            int(getattr(settings, "gpa_rate_limit_oauth_per_min", 10)),
        ):
            await record_audit(
                request.app.state.services.db,
                tenant_id=getattr(settings, "ov_tenant_id", "demo"),
                app_user_id=client_id or "unknown-client",
                action="service.oauth",
                result="denied",
                ip=ip,
            )
            raise GpaError("RATE_LIMITED", "too many token requests", retryable=True)
    if not (
        secrets.compare_digest(client_id, expected_id)
        and secrets.compare_digest(client_secret, expected_secret)
    ):
        raise GpaError("AUTH_REQUIRED", "invalid client credentials")
    requested = tuple(item for item in scope.split() if item)
    scopes = requested or ALL_SCOPES
    if any(item not in ALL_SCOPES for item in scopes):
        raise GpaError("FORBIDDEN", "unknown scope")
    allowed_users = tuple(item for item in users.replace(",", " ").split() if item)
    if not allowed_users:
        allowed_users = tuple(
            item
            for item in (getattr(settings, "gpa_oauth_allowed_users", "") or "").replace(",", " ").split()
            if item
        )
    result = issue_service_token(
        settings,
        tenant_id=tenant or getattr(settings, "ov_tenant_id", "demo"),
        subject=client_id,
        scopes=scopes,
        allowed_users=allowed_users,
    )
    return {
        "access_token": result["token"],
        "token_type": "Bearer",
        "expires_in": result["expires_in"],
        "scope": " ".join(scopes),
    }


@oauth_router.post("/revoke")
async def oauth_revoke(
    request: Request,
    token: str = Form(default=""),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
):
    settings = request.app.state.services.settings
    expected_id = getattr(settings, "gpa_oauth_client_id", "")
    expected_secret = getattr(settings, "gpa_oauth_client_secret", "")
    if not (
        expected_id
        and expected_secret
        and secrets.compare_digest(client_id, expected_id)
        and secrets.compare_digest(client_secret, expected_secret)
    ):
        raise GpaError("AUTH_REQUIRED", "invalid client credentials")
    claims = await verify_service_token_async(settings, token, request.app.state.services.db)
    if claims is None or not claims.jti:
        raise GpaError("NOT_FOUND", "token not found")
    await request.app.state.services.db.pool.execute(
        """
        INSERT INTO service_token_revocation (jti, expires_at)
        VALUES ($1, $2) ON CONFLICT (jti) DO NOTHING
        """,
        claims.jti,
        claims.expires_at,
    )
    return {"revoked": True, "jti": claims.jti}


@router.post("/tasks", response_model=TaskView)
async def submit_internal_task(body: InternalTaskSubmit, request: Request):
    _require_scope(request, "task:submit")
    _require_user(request, body.app_user_id)
    gpa = gpa_services(request)
    if body.tenant_id != tenant_id(request):
        raise GpaError("FORBIDDEN", "tenant mismatch")
    task = await gpa.tasks.submit(
        tenant_id=body.tenant_id,
        app_user_id=body.app_user_id,
        conversation_id=body.conversation_id,
        goal=body.goal,
        task_type=body.task_type,
        brief=body.brief,
        origin_turn_id=body.origin_turn_id,
        idempotency_key=body.idempotency_key,
        executor=body.executor,
    )
    return TaskView(**task)


@router.get("/tasks/{task_id}", response_model=TaskView)
async def get_internal_task(
    task_id: str,
    request: Request,
    tenant: str = Query(default="demo"),
    user_id: str = Query(alias="userId"),
):
    _require_scope(request, "task:read")
    _require_user(request, user_id)
    if tenant != tenant_id(request):
        raise GpaError("FORBIDDEN", "tenant mismatch")
    task = await gpa_services(request).tasks.get(task_id, tenant, user_id)
    return TaskView(**task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskView)
async def cancel_internal_task(
    task_id: str,
    request: Request,
    tenant: str = Query(default="demo"),
    user_id: str = Query(alias="userId"),
    reason: str = "",
):
    _require_scope(request, "task:cancel")
    _require_user(request, user_id)
    if tenant != tenant_id(request):
        raise GpaError("FORBIDDEN", "tenant mismatch")
    task = await gpa_services(request).tasks.cancel(task_id, tenant, user_id, reason)
    return TaskView(**task)
