-- Remove legacy Voice Demo / Wb / KB / memory / MA monitor tables.
-- GPA-native tables (conversation*, task_*, app_user, ov_identity, audit_log,
-- context_outbox, context_trace, schema_migrations) are intentionally kept.
BEGIN;

DROP TABLE IF EXISTS artifacts CASCADE;
DROP TABLE IF EXISTS agent_session CASCADE;
DROP TABLE IF EXISTS agent_turn CASCADE;
DROP TABLE IF EXISTS agent_turn_event CASCADE;
DROP TABLE IF EXISTS ma_session_event CASCADE;
DROP TABLE IF EXISTS ma_session_input CASCADE;
DROP TABLE IF EXISTS ma_session_task_group CASCADE;
DROP TABLE IF EXISTS ma_session_turn CASCADE;
DROP TABLE IF EXISTS ma_session CASCADE;
DROP TABLE IF EXISTS memory_event CASCADE;
DROP TABLE IF EXISTS memory_item CASCADE;
DROP TABLE IF EXISTS task_events CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS transcript CASCADE;
DROP TABLE IF EXISTS kb_audit CASCADE;
DROP TABLE IF EXISTS kb_docs CASCADE;
DROP TABLE IF EXISTS wb_automation_occurrence CASCADE;
DROP TABLE IF EXISTS wb_automation CASCADE;
DROP TABLE IF EXISTS wb_change CASCADE;
DROP TABLE IF EXISTS wb_mcp_server CASCADE;
DROP TABLE IF EXISTS wb_notification CASCADE;
DROP TABLE IF EXISTS wb_personal_agent CASCADE;
DROP TABLE IF EXISTS wb_skill CASCADE;
DROP TABLE IF EXISTS wb_submission CASCADE;
DROP TABLE IF EXISTS idempotency CASCADE;

-- The legacy outbox handlers for memory.sync / wb.summary are gone.
DELETE FROM context_outbox WHERE kind NOT IN ('ov.message', 'ov.commit');

INSERT INTO schema_migrations (version, description)
VALUES ('007_drop_legacy', 'P5: drop legacy Voice Demo/Wb/KB/memory tables')
ON CONFLICT (version) DO NOTHING;

COMMIT;
