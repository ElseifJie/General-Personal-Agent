BEGIN;
CREATE TABLE IF NOT EXISTS service_token_revocation (
    jti text PRIMARY KEY,
    expires_at timestamptz,
    revoked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS service_token_revocation_exp_idx
    ON service_token_revocation (expires_at);
INSERT INTO schema_migrations (version, description)
VALUES ('010_service_token_revocation', 'P0: JWT revocation list')
ON CONFLICT (version) DO NOTHING;
COMMIT;
