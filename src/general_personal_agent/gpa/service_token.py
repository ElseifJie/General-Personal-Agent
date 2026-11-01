"""Short-lived service tokens for the internal task gateway.

The signing secret is expected to be injected by KMS/Secrets Manager in
production; this module only owns claims/verification and the rotation window.
Static tokens remain accepted for the local baseline.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

ALL_SCOPES = ("task:submit", "task:read", "task:cancel")


@dataclass
class ServiceClaims:
    subject: str
    tenant_id: str
    scopes: frozenset[str]
    expires_at: datetime | None = None
    static: bool = False
    jti: str = ""
    allowed_users: frozenset[str] | None = None


def _secret(settings, previous: bool = False) -> str:
    if previous:
        value = getattr(settings, "gpa_service_jwt_previous_secret", "") or ""
        path = getattr(settings, "gpa_service_jwt_previous_secret_file", "") or ""
    else:
        value = getattr(settings, "gpa_service_jwt_secret", "") or ""
        path = getattr(settings, "gpa_service_jwt_secret_file", "") or ""
    if value:
        return value
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    return ""


def issue_service_token(
    settings,
    *,
    tenant_id: str,
    subject: str,
    scopes: tuple[str, ...] = ALL_SCOPES,
    ttl_s: int | None = None,
    allowed_users: tuple[str, ...] = (),
) -> dict[str, Any]:
    secret = _secret(settings)
    if not secret:
        raise ValueError("GPA_SERVICE_JWT_SECRET is not configured")
    now = datetime.now(UTC)
    ttl = int(ttl_s or getattr(settings, "gpa_service_token_ttl_s", 900))
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "scope": " ".join(scopes),
        "iss": getattr(settings, "gpa_service_jwt_issuer", "gpa"),
        "aud": getattr(settings, "gpa_service_jwt_audience", "gpa-internal"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": secrets.token_hex(12),
        "users": list(allowed_users),
    }
    token = jwt.encode(payload, secret, algorithm="HS256", headers={"kid": "current"})
    return {"token": token, "token_type": "Bearer", "expires_in": ttl, "jti": payload["jti"]}


def verify_service_token(settings, token: str) -> ServiceClaims | None:
    if not token:
        return None
    for candidate in (
        getattr(settings, "gpa_service_token", "") or "",
        getattr(settings, "gpa_service_token_previous", "") or "",
    ):
        if candidate and secrets.compare_digest(token, candidate):
            return ServiceClaims(
                subject="static-service-token",
                tenant_id=getattr(settings, "ov_tenant_id", "demo") or "demo",
                scopes=frozenset(ALL_SCOPES),
                static=True,
                allowed_users=None,
            )
    issuer = getattr(settings, "gpa_service_jwt_issuer", "gpa")
    audience = getattr(settings, "gpa_service_jwt_audience", "gpa-internal")
    for previous in (False, True):
        secret = _secret(settings, previous=previous)
        if not secret:
            continue
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=audience,
                issuer=issuer,
            )
        except Exception:  # noqa: BLE001
            continue
        ttl = int(payload.get("exp") or 0)
        return ServiceClaims(
            subject=str(payload.get("sub") or "service"),
            tenant_id=str(payload.get("tenant_id") or "demo"),
            scopes=frozenset(str(payload.get("scope") or "").split()),
            expires_at=datetime.fromtimestamp(ttl, UTC) if ttl else None,
            jti=str(payload.get("jti") or ""),
            allowed_users=frozenset(str(item) for item in (payload.get("users") or []) if item),
        )
    return None


async def verify_service_token_async(settings, token: str, db=None) -> ServiceClaims | None:
    claims = verify_service_token(settings, token)
    if claims is None or claims.static or not claims.jti or db is None:
        return claims
    row = await db.pool.fetchrow("SELECT 1 FROM service_token_revocation WHERE jti = $1", claims.jti)
    return None if row else claims
