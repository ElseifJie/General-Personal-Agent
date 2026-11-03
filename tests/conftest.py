"""Shared fixtures for GPA tests.

PG-backed tests only run against an explicit, separate ``TEST_DATABASE_URL``.
The fixture refuses the application DSN and gives every test session its own
schema, so a stray ``DELETE`` can never touch business data. Without the env var
PG tests are skipped; every migration failure is fatal (no silent skip).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from general_personal_agent.db import Database, _init_connection
from general_personal_agent.settings import get_settings

MIGRATIONS = [
    "db/schema.sql",
    "db/migrations/002_v05.sql",
    "db/migrations/003_wb.sql",
    "db/migrations/004_voice_workbench.sql",
    "db/migrations/005_gpa_core.sql",
    "db/migrations/006_gpa_context.sql",
    "db/migrations/007_drop_legacy.sql",
    "db/migrations/008_identity_convergence.sql",
    "db/migrations/009_artifact_retention.sql",
    "db/migrations/010_service_token_revocation.sql",
    "db/migrations/011_task_idempotency_scope.sql",
    "db/migrations/012_conversation_management.sql",
    "db/migrations/013_voice_session_model.sql",
    "db/migrations/014_files.sql",
    "db/migrations/015_reliability_fixes.sql",
    "db/migrations/016_gc_outbox_recovery.sql",
    "db/migrations/017_task_partial_status.sql",
    "db/migrations/018_task_retry_backoff.sql",
]


def _as_psql_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def _same_database(left: str, right: str) -> bool:
    a, b = urlsplit(_as_psql_dsn(left)), urlsplit(_as_psql_dsn(right))
    return (a.hostname, a.port, a.path) == (b.hostname, b.port, b.path)


def _psql(dsn: str, path: Path, schema: str) -> None:
    env = os.environ.copy()
    existing = env.get("PGOPTIONS", "")
    env["PGOPTIONS"] = f"{existing} -c search_path={schema}".strip()
    result = subprocess.run(
        ["psql", _as_psql_dsn(dsn), "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"migration failed: {path}\nstdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
        )


@pytest_asyncio.fixture(loop_scope="session")
async def wb_db():
    test_dsn = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not test_dsn:
        pytest.skip("TEST_DATABASE_URL is not set; PG integration tests skipped")
    app_dsn = get_settings().db_dsn
    if _same_database(test_dsn, app_dsn):
        pytest.fail(
            "refusing to run tests against the application database; "
            "set TEST_DATABASE_URL to a dedicated test database"
        )
    root = Path(__file__).parent.parent
    schema = f"gpa_test_{uuid.uuid4().hex[:10]}"

    admin = await asyncpg.connect(_as_psql_dsn(test_dsn))
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            for migration in MIGRATIONS:
                _psql(test_dsn, root / migration, schema)
        except Exception:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            raise
    finally:
        await admin.close()

    pool = await asyncpg.create_pool(
        dsn=_as_psql_dsn(test_dsn),
        min_size=1,
        max_size=8,
        init=_init_connection,
        server_settings={"search_path": schema},
    )
    db = Database(pool)
    try:
        yield db
    finally:
        await db.close()
        admin = await asyncpg.connect(_as_psql_dsn(test_dsn))
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()


@pytest_asyncio.fixture(loop_scope="session")
async def gpa_env(wb_db):
    """GPA-native services on the isolated test schema, with a unique user."""
    from types import SimpleNamespace

    from general_personal_agent.gpa.services import build_gpa_services

    user_id = f"gpatest_{uuid.uuid4().hex[:8]}"
    await wb_db.pool.execute(
        "INSERT INTO app_user (app_user_id, tenant_id, display_name, role)"
        " VALUES ($1, 'demo', $1, 'user') ON CONFLICT (app_user_id) DO NOTHING",
        user_id,
    )
    settings = get_settings().model_copy(
        update={
            "gpa_agent_mode": "stub",
            "gpa_files_rollout": "all",
            # Tests never call the real MA control plane, but the gateway and
            # executor require these fields to be present.
            "ma_api_key": "test-ma-key",
            "ma_agent_id": "test-agent",
            "ma_environment_id": "test-environment",
            "app_secret_key": "test-session-secret",
        }
    )
    gpa = build_gpa_services(settings, wb_db)
    env = SimpleNamespace(
        db=wb_db,
        settings=settings,
        gpa=gpa,
        store=gpa.store,
        conversations=gpa.conversations,
        inbox=gpa.inbox,
        tasks=gpa.tasks,
        task_store=gpa.task_store,
        user=user_id,
        tenant="demo",
    )
    try:
        yield env
    finally:
        await gpa.close()
        # Only rows owned by this synthetic user (the schema itself is dropped
        # at session end).  Never a global DELETE on a shared table.
        await wb_db.pool.execute("DELETE FROM kb_tombstone WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute(
            "DELETE FROM kb_uri_lock WHERE uri_prefix LIKE 'viking://user/' || $1 || '/%'", user_id
        )
        await wb_db.pool.execute(
            "DELETE FROM kb_dir_invalidation WHERE dir_uri LIKE 'viking://user/' || $1 || '/%'", user_id
        )
        await wb_db.pool.execute("DELETE FROM file_job WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM file_object WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM context_outbox WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM task_run WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM conversation WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM context_trace WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM audit_log WHERE app_user_id = $1", user_id)
        await wb_db.pool.execute("DELETE FROM app_user WHERE app_user_id = $1", user_id)
