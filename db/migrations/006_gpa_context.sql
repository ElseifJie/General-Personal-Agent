-- C2: conversation-scoped OpenViking session mapping and write-back counters.
BEGIN;

ALTER TABLE conversation ADD COLUMN IF NOT EXISTS ov_session_id text;
ALTER TABLE conversation ADD COLUMN IF NOT EXISTS ov_message_count integer NOT NULL DEFAULT 0;

INSERT INTO schema_migrations (version, description)
VALUES ('006_gpa_context', 'GPA context: OV session mapping and message counter')
ON CONFLICT (version) DO NOTHING;

COMMIT;
