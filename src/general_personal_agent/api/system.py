"""Health, users and metrics routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from general_personal_agent.auth import require_user

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    services = request.app.state.services
    settings = services.settings
    db_ok = True
    try:
        await services.db.pool.fetchval("SELECT 1")
    except Exception:
        db_ok = False
    gpa = getattr(request.app.state, "gpa", None)
    return {
        "ok": True,
        "env": settings.app_env,
        "db_ready": db_ok,
        "gpa": (await gpa.health()) if gpa is not None else None,
        "configured": {
            "ark": bool(settings.ark_api_key and settings.model_agent_name),
            "speech": bool(settings.tool_vespeech_app_id and settings.tool_vespeech_api_key),
            "openviking": bool(settings.ov_user_key_alice and settings.ov_user_key_bob),
            "ma": bool(settings.ma_agent_id and settings.ma_environment_id),
            "tos": bool(settings.volcengine_access_key and settings.database_tos_bucket_name),
        },
    }


@router.get("/api/demo/users")
async def demo_users(request: Request) -> list[dict]:
    return await request.app.state.services.db.list_app_users()


@router.get("/api/metrics/summary")
async def metrics_summary(request: Request) -> dict:
    services = request.app.state.services
    user = await require_user(request, services.settings, services.db)
    pool = services.db.pool
    conversations = await pool.fetchval(
        "SELECT count(*) FROM conversation WHERE app_user_id = $1", user.app_user_id
    )
    tasks = await pool.fetch(
        "SELECT status, count(*) AS n FROM task_run WHERE app_user_id = $1 GROUP BY status",
        user.app_user_id,
    )
    return {
        "user_id": user.app_user_id,
        "conversations": conversations,
        "tasks": {row["status"]: row["n"] for row in tasks},
    }
