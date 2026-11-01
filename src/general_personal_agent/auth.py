"""Demo session auth: PIN login + signed HttpOnly cookie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from general_personal_agent.db import Database
from general_personal_agent.settings import Settings


@dataclass(frozen=True)
class SessionUser:
    app_user_id: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(user_id: str, role: str, secret: str, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "role": role, "iat": now, "exp": now + ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def should_refresh_session(payload: dict, ttl_seconds: int) -> bool:
    """滑动会话：距签发超过半个生命周期（或无 iat 的旧 token）→ 续签。

    效果：持续操作永不掉线；停止操作超过 ttl（默认 720 分钟）后才需重登。
    """
    iat = int(payload.get("iat", 0) or 0)
    if iat <= 0:
        return True
    return (time.time() - iat) > ttl_seconds / 2


def refresh_session_token(payload: dict, secret: str, ttl_seconds: int) -> str:
    """按原身份续签（sub/role 保留，iat/exp 重置）。"""
    return sign_session(payload.get("sub", ""), payload.get("role", "user"), secret, ttl_seconds)


def verify_session(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def check_pin(pin: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(pin, expected)


async def session_user_from_request(request: Request, settings: Settings, db: Database) -> SessionUser | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    payload = verify_session(token, settings.app_secret_key)
    if not payload:
        return None
    row = await db.get_app_user(payload["sub"])
    if not row or row["status"] != "active":
        return None
    return SessionUser(app_user_id=row["app_user_id"], role=row["role"])


async def require_user(request: Request, settings: Settings, db: Database) -> SessionUser:
    user = await session_user_from_request(request, settings, db)
    if user is None:
        raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
    return user
