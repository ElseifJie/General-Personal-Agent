-- P1: artifact retention metadata for GPA-owned copies.
BEGIN;

ALTER TABLE task_artifact ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE task_artifact ADD COLUMN IF NOT EXISTS source_ref text;
ALTER TABLE task_artifact ADD COLUMN IF NOT EXISTS checksum text;

UPDATE task_artifact
   SET expires_at = now() + interval '7 days'
 WHERE expires_at IS NULL;

INSERT INTO schema_migrations (version, description)
VALUES ('009_artifact_retention', 'P1: artifact TTL and source metadata')
ON CONFLICT (version) DO NOTHING;

COMMIT;
