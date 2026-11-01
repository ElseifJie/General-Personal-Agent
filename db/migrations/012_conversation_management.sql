-- Conversation management: pinning for the sidebar plus the archive flag
-- (archive itself already lives in conversation.status).

BEGIN;

ALTER TABLE conversation ADD COLUMN IF NOT EXISTS pinned_at timestamptz;

-- The sidebar reads active conversations ordered by pin then recency; this
-- index matches that exact access path.
CREATE INDEX IF NOT EXISTS conversation_owner_pinned_idx
  ON conversation (tenant_id, app_user_id, pinned_at DESC NULLS LAST, updated_at DESC);

INSERT INTO schema_migrations (version, description)
VALUES ('012_conversation_management', 'Conversation pinning for sidebar management')
ON CONFLICT (version) DO NOTHING;

COMMIT;
