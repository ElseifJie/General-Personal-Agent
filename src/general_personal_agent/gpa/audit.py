"""Best-effort audit writes for GPA actions (P0/P5 security baseline)."""

from __future__ import annotations

from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)


async def record_audit(
    db,
    *,
    tenant_id: str,
    app_user_id: str,
    action: str,
    resource_id: str | None = None,
    result: str = "ok",
    ip: str | None = None,
    trace_id: str | None = None,
) -> None:
    try:
        await db.pool.execute(
            """
            INSERT INTO audit_log
              (tenant_id, app_user_id, action, resource_id, result, ip, trace_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            tenant_id,
            app_user_id,
            action,
            resource_id,
            result,
            ip,
            trace_id,
        )
    except Exception as exc:  # noqa: BLE001 - audit must not break the request
        logger.warning("audit write failed action=%s user=%s: %s", action, app_user_id, exc)
