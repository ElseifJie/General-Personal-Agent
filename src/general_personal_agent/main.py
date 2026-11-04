"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from general_personal_agent.api import auth as auth_api
from general_personal_agent.api import system, ws
from general_personal_agent.api.middleware import RequestContextMiddleware, install_error_handlers
from general_personal_agent.clients.ma import MAClient
from general_personal_agent.clients.search import SearchService
from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.clients.tts import TTSClient
from general_personal_agent.db import Database
from general_personal_agent.gpa.api import (
    context_router,
    conversation_files_router,
    conversations_router,
    files_router,
    internal_router,
    kb_router,
    memories_router,
    oauth_router,
    tasks_router,
    uploads_router,
)
from general_personal_agent.gpa.errors import install_gpa_error_handlers
from general_personal_agent.gpa.services import build_gpa_services
from general_personal_agent.logging import configure_logging, get_logger
from general_personal_agent.services import build_services
from general_personal_agent.settings import get_settings

WEB_DIR = Path(__file__).parent / "web"
SPA_DIST_DIR = Path(__file__).parent.parent.parent / "web-workbench" / "dist"
logger = get_logger(__name__)


def _safe_dist_file(relative: str) -> Path | None:
    """Resolve a built SPA file without allowing path traversal."""
    if not relative:
        return None
    base = SPA_DIST_DIR.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _spa_not_built() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>GPA 前端未构建</title>"
        "<main style='font:16px/1.6 system-ui;padding:48px;max-width:720px;margin:auto'>"
        "<h1>交互工作台尚未构建</h1>"
        "<p>构建前端后刷新：<code>cd web-workbench &amp;&amp; npm ci &amp;&amp; npm run build</code></p>"
        "<p>开发模式请运行 <code>npm run dev</code> 并使用 http://127.0.0.1:5173/。</p>"
        "</main>",
        status_code=503,
    )


def validate_startup_settings(settings) -> None:
    """Fail closed: never sign sessions with an empty key."""
    if not settings.app_secret_key.strip():
        raise RuntimeError("APP_SECRET_KEY must be set; refusing to start with an empty signing key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    validate_startup_settings(settings)
    configure_logging(settings)
    settings.apply_veadk_env()

    db = await Database.connect(settings)
    tos = TOSClient(
        settings.tos_endpoint,
        settings.database_tos_region,
        settings.volcengine_access_key,
        settings.volcengine_secret_key,
        settings.database_tos_bucket_name,
        session_token=settings.volcengine_session_token,
        prefix=settings.tos_prefix,
    )
    ma = MAClient(settings.ma_base_url, settings.ma_key)
    search = SearchService(settings)
    tts = TTSClient(
        settings.tool_vespeech_app_id,
        settings.tool_vespeech_api_key,
        settings.tool_vespeech_speaker,
        cluster=settings.tts_cluster,
        speed_ratio=settings.tool_vespeech_speed_ratio,
    )
    try:
        await tts.synthesize_pcm("好的，语音服务已就绪。")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts warmup failed: %s", exc)
    services = build_services(settings, db, tos, ma, search, tts)
    app.state.services = services
    # GPA-native core: conversation/agent/context + task inbox.
    gpa = build_gpa_services(
        settings,
        db,
        ma=ma,
        tos=tos,
        identity_service=services.identity_service,
        search=search,
    )
    app.state.gpa = gpa
    await gpa.start()
    # In-process outbox sweeper (state in PG; survives restarts).
    if services.outbox is not None:
        services.outbox.start()
    logger.info(
        "services ready (env=%s, stm=%s, memory=%s)",
        settings.app_env,
        settings.agent_stm_backend,
        settings.memory_enabled,
    )
    try:
        yield
    finally:
        if getattr(app.state, "gpa", None) is not None:
            await app.state.gpa.close()
        if services.outbox is not None:
            await services.outbox.stop()
        try:
            await db.purge_context_traces(settings.context_trace_ttl_days)
        except Exception:  # noqa: BLE001
            pass
        await services.close()
        await tts.close()
        await db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="General Personal Agent", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.app_cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    install_gpa_error_handlers(app)
    app.include_router(system.router)
    app.include_router(auth_api.router)
    app.include_router(ws.router)
    # ---- GPA-native conversation/task API (/api/conversations, /api/tasks) ----
    app.include_router(conversations_router)
    app.include_router(tasks_router)
    app.include_router(files_router)
    app.include_router(uploads_router)
    app.include_router(kb_router)
    app.include_router(conversation_files_router)
    app.include_router(context_router)
    app.include_router(memories_router)
    app.include_router(internal_router)
    app.include_router(oauth_router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    if (SPA_DIST_DIR / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(SPA_DIST_DIR / "assets"), html=False),
            name="spa-assets",
        )

    @app.middleware("http")
    async def spa_html_revalidate(request, call_next):
        """HTML shells must revalidate; hashed /assets can stay cacheable."""
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith(("/interact/", "/history", "/tasks/", "/context", "/files")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.get("/", include_in_schema=False, response_model=None)
    async def index() -> FileResponse | HTMLResponse:
        candidate = SPA_DIST_DIR / "index.html"
        if candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": "no-cache, must-revalidate"})
        return _spa_not_built()

    # The workbench owns root routing; unknown paths fall back to the SPA shell.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa_fallback(full_path: str) -> FileResponse | HTMLResponse:
        if full_path in {"api", "ws", "openapi.json", "docs", "redoc"} or full_path.startswith(
            ("api/", "ws/")
        ):
            raise HTTPException(status_code=404, detail="NOT_FOUND")
        candidate = _safe_dist_file(full_path)
        if candidate is not None:
            return FileResponse(candidate)
        shell = SPA_DIST_DIR / "index.html"
        if shell.is_file():
            return FileResponse(shell, headers={"Cache-Control": "no-cache, must-revalidate"})
        return _spa_not_built()

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("general_personal_agent.main:app", host=settings.app_host, port=settings.app_port)
