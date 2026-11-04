-- Security: scope task idempotency keys to tenant/user.
BEGIN;

DROP INDEX IF EXISTS task_run_idempotency_idx;
CREATE UNIQUE INDEX IF NOT EXISTS task_run_owner_idempotency_idx
    ON task_run (tenant_id, app_user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

INSERT INTO schema_migrations (version, description)
VALUES ('011_task_idempotency_scope', 'P0: scope task idempotency keys to tenant/user')
ON CONFLICT (version) DO NOTHING;

COMMIT;
