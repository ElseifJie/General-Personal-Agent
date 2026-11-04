-- Converge app_user/ov_identity on (tenant_id, app_user_id).
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS app_user_tenant_user_key
    ON app_user (tenant_id, app_user_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ov_identity_app_user_fk'
    ) THEN
        ALTER TABLE ov_identity
            ADD CONSTRAINT ov_identity_app_user_fk
            FOREIGN KEY (tenant_id, app_user_id)
            REFERENCES app_user (tenant_id, app_user_id)
            ON DELETE CASCADE;
    END IF;
END $$;

INSERT INTO schema_migrations (version, description)
VALUES ('008_identity_convergence', 'P0: tenant/user identity convergence')
ON CONFLICT (version) DO NOTHING;

COMMIT;
