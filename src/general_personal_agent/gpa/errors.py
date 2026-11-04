"""GPA API errors.

The transition baseline middleware emits ``{code, message, requestId}``. New
GPA endpoints need the frozen envelope with ``retryable``/``details``/``trace_id``
so clients can branch on stable codes. ``GpaError`` is mapped by
``install_gpa_error_handlers`` and wins over the generic HTTP handler.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_CODE_STATUS = {
    "AUTH_REQUIRED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VALIDATION_ERROR": 422,
    "CONFLICT": 409,
    "IDEMPOTENCY_MISMATCH": 409,
    "RATE_LIMITED": 429,
    "TIMEOUT": 504,
    "UPSTREAM_UNAVAILABLE": 503,
    "CANCELLED": 409,
    "ARTIFACT_UNAVAILABLE": 410,
    "INTERNAL_ERROR": 500,
}


class GpaError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status_code: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status_code = status_code or _CODE_STATUS.get(code, 400)
        self.retryable = retryable
        self.details = details or {}


def error_envelope(exc: GpaError, request: Request) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "details": exc.details,
        "trace_id": getattr(request.state, "trace_id", "") or getattr(request.state, "request_id", ""),
    }


def install_gpa_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GpaError)
    async def gpa_error_handler(request: Request, exc: GpaError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc, request),
            headers={"X-Request-Id": getattr(request.state, "request_id", "")},
        )
