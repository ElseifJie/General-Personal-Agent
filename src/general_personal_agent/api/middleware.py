"""Request id, sliding session cookie and unified HTTP error envelope."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        self._maybe_slide_session(request, response)
        return response

    @staticmethod
    def _maybe_slide_session(request: Request, response) -> None:
        """滑动会话：有效 token 过半生命周期即续签 Cookie（闲置超 TTL 自然过期重登）。"""
        from general_personal_agent.auth import (
            refresh_session_token,
            should_refresh_session,
            verify_session,
        )

        settings = getattr(getattr(request.app.state, "services", None), "settings", None)
        token = request.cookies.get(settings.session_cookie_name) if settings else None
        if not token or not settings or not settings.app_secret_key:
            return
        payload = verify_session(token, settings.app_secret_key)
        if not payload:
            return
        ttl = settings.session_ttl_min * 60
        if not should_refresh_session(payload, ttl):
            return
        response.set_cookie(
            settings.session_cookie_name,
            refresh_session_token(payload, settings.app_secret_key, ttl),
            max_age=ttl,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
        )


def _error_body(code: str, message: str, request: Request) -> dict:
    return {"code": code, "message": message, "requestId": getattr(request.state, "request_id", "")}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(detail, detail, request),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "请求参数校验失败", request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", str(exc)[:300], request),
        )
