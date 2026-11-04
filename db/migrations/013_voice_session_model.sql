-- Voice/Text session model.
--
-- A conversation is created as a draft so an abandoned voice/text entry does
-- not leave an empty row in the sidebar. The first turn promotes it and may
-- auto-title it from the user's first line. A user rename locks the title.

BEGIN;

ALTER TABLE conversation ADD COLUMN IF NOT EXISTS is_draft boolean NOT NULL DEFAULT false;
ALTER TABLE conversation ADD COLUMN IF NOT EXISTS title_locked boolean NOT NULL DEFAULT false;

-- Pre-013 leftovers: empty, default-titled, unpinned conversations were never
-- useful history entries. Hide them instead of deleting data.
UPDATE conversation c
   SET is_draft = true
 WHERE c.is_draft = false
   AND c.pinned_at IS NULL
   AND COALESCE(c.title, '') IN ('', '新交互', '语音交互')
   AND NOT EXISTS (
       SELECT 1 FROM conversation_turn t WHERE t.conversation_id = c.conversation_id
   );

-- Sidebar/history only list visible conversations; this partial index matches
-- the exact ORDER BY used by list_conversations.
CREATE INDEX IF NOT EXISTS conversation_owner_visible_idx
  ON conversation (tenant_id, app_user_id, pinned_at DESC NULLS LAST, updated_at DESC)
  WHERE NOT is_draft;

INSERT INTO schema_migrations (version, description)
VALUES ('013_voice_session_model', 'Voice session drafts, title lock and visible list index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
