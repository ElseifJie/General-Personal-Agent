"""Auth routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from general_personal_agent.auth import check_pin, require_user, sign_session
from general_personal_agent.gpa.audit import record_audit
from general_personal_agent.identity import IdentityError, IdentityService

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    user_id: str
    pin: str


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    services = request.app.state.services
    settings = services.settings
    ip = request.client.host if request.client else None
    gpa = getattr(request.app.state, "gpa", None)
    if gpa is not None and not await gpa.rate_limiter.allow(
        f"login:{ip or 'unknown'}", int(settings.gpa_rate_limit_login_per_min)
    ):
        await record_audit(
            services.db,
            tenant_id=settings.ov_tenant_id or "demo",
            app_user_id=body.user_id,
            action="auth.login",
            result="denied",
            ip=ip,
        )
        raise HTTPException(status_code=429, detail="RATE_LIMITED")
    user = await services.db.get_app_user(body.user_id)
    if not user or user["status"] != "active" or not check_pin(body.pin, settings.demo_login_pin):
        await record_audit(
            services.db,
            tenant_id=settings.ov_tenant_id or "demo",
            app_user_id=body.user_id,
            action="auth.login",
            result="denied",
            ip=ip,
        )
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
    token = sign_session(
        user["app_user_id"], user["role"], settings.app_secret_key, settings.session_ttl_min * 60
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_min * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    try:
        identity = await IdentityService(settings, services.db).resolve(user["app_user_id"])
    except IdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_audit(
        services.db,
        tenant_id=settings.ov_tenant_id or "demo",
        app_user_id=user["app_user_id"],
        action="auth.login",
        result="ok",
        ip=ip,
    )
    return {
        "user_id": user["app_user_id"],
        "role": user["role"],
        "ov_user": identity.ov_user,
    }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(request.app.state.services.settings.session_cookie_name)
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict:
    services = request.app.state.services
    user = await require_user(request, services.settings, services.db)
    return {"user_id": user.app_user_id, "role": user.role}
