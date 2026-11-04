-- GPA-native core schema
--
-- Adds the conversation/ task core tables:
--   conversation, conversation_turn, conversation_event,
--   task_run, task_event, task_attempt, task_artifact,
--   audit_log, and the conversation_seq allocator.
--
-- This migration is additive: it does not create or touch the Voice Demo
-- legacy tables (ma_session/tasks/agent_session/...). Those stay dormant
-- until the legacy cleanup.
--
-- Apply:
--   psql "$DB" -f db/migrations/005_gpa_core.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- Migration bookkeeping (Alembic or equivalent schema_migrations)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     text PRIMARY KEY,
  description text NOT NULL DEFAULT '',
  applied_at  timestamptz NOT NULL DEFAULT now()
);

-- The demo identity table predates GPA-native schema. Add the tenant dimension
-- so conversation/task queries can be scoped consistently. Single-tenant for
-- now; the composite PK arrives with the identity consolidation.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'demo';

-- ---------------------------------------------------------------------------
-- Conversation (conversation is the primary model)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation (
  conversation_id text PRIMARY KEY,
  tenant_id       text NOT NULL,
  app_user_id     text NOT NULL,
  title           text NOT NULL DEFAULT '',
  active_channel  text NOT NULL CHECK (active_channel IN ('text','voice')),
  channels        text[] NOT NULL DEFAULT '{}'::text[],
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_owner_idx
  ON conversation (tenant_id, app_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS conversation_channels_idx
  ON conversation USING GIN (channels);

-- Atomic per-conversation sequence allocator. Both writers (ConversationService
-- and TaskEventInbox) go through the same counters, so the persisted timeline
-- has one total order.
CREATE TABLE IF NOT EXISTS conversation_seq (
  conversation_id text NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
  scope           text NOT NULL CHECK (scope IN ('turn','event')),
  last_seq        bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (conversation_id, scope)
);

CREATE TABLE IF NOT EXISTS conversation_turn (
  turn_id              text PRIMARY KEY,
  conversation_id      text NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
  seq                  bigint NOT NULL,
  channel              text NOT NULL CHECK (channel IN ('text','voice')),
  user_text            text NOT NULL DEFAULT '',
  assistant_text       text NOT NULL DEFAULT '',
  status               text NOT NULL CHECK (status IN ('running','completed','failed','cancelled')),
  client_submission_id text,
  trace_id             text,
  metrics              jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at           timestamptz NOT NULL DEFAULT now(),
  ended_at             timestamptz,
  UNIQUE (conversation_id, seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS conversation_turn_submission_idx
  ON conversation_turn (conversation_id, client_submission_id)
  WHERE client_submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS conversation_turn_recent_idx
  ON conversation_turn (conversation_id, seq DESC);

CREATE TABLE IF NOT EXISTS conversation_event (
  event_id        text PRIMARY KEY,
  conversation_id text NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
  turn_id         text REFERENCES conversation_turn(turn_id) ON DELETE CASCADE,
  task_id         text,
  seq             bigint NOT NULL,
  type            text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  trace_id        text,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);

CREATE INDEX IF NOT EXISTS conversation_event_turn_idx
  ON conversation_event (turn_id, seq);
CREATE INDEX IF NOT EXISTS conversation_event_task_idx
  ON conversation_event (task_id);

-- Additive column for DBs that applied an earlier revision of this migration.
ALTER TABLE conversation_event ADD COLUMN IF NOT EXISTS trace_id text;

-- ---------------------------------------------------------------------------
-- Task (task is a bounded child resource of a conversation)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_run (
  task_id         text PRIMARY KEY,
  tenant_id       text NOT NULL,
  app_user_id     text NOT NULL,
  conversation_id text NOT NULL REFERENCES conversation(conversation_id) ON DELETE RESTRICT,
  origin_turn_id  text,
  goal            text NOT NULL,
  task_type       text NOT NULL,
  brief           jsonb NOT NULL DEFAULT '{}'::jsonb,
  executor        text NOT NULL DEFAULT 'ma',
  executor_ref    text,
  executor_meta   jsonb NOT NULL DEFAULT '{}'::jsonb,
  status          text NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
  progress        double precision NOT NULL DEFAULT 0,
  summary         text NOT NULL DEFAULT '',
  error           jsonb,
  idempotency_key text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS task_run_idempotency_idx
  ON task_run (idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS task_run_conversation_idx
  ON task_run (conversation_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS task_run_owner_idx
  ON task_run (tenant_id, app_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_event (
  event_id     text PRIMARY KEY,
  task_id      text NOT NULL REFERENCES task_run(task_id) ON DELETE CASCADE,
  seq          bigint NOT NULL,
  type         text NOT NULL,
  payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
  projected_at timestamptz,
  occurred_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (task_id, seq)
);

CREATE INDEX IF NOT EXISTS task_event_projection_idx
  ON task_event (occurred_at) WHERE projected_at IS NULL;

CREATE TABLE IF NOT EXISTS task_attempt (
  attempt_id   uuid PRIMARY KEY,
  task_id      text NOT NULL REFERENCES task_run(task_id) ON DELETE CASCADE,
  seq          integer NOT NULL,
  status       text NOT NULL CHECK (status IN ('running','completed','failed','cancelled')),
  executor_ref text,
  error        jsonb,
  started_at   timestamptz NOT NULL DEFAULT now(),
  ended_at     timestamptz,
  UNIQUE (task_id, seq)
);

CREATE TABLE IF NOT EXISTS task_artifact (
  artifact_id text PRIMARY KEY,
  task_id     text NOT NULL REFERENCES task_run(task_id) ON DELETE CASCADE,
  name        text NOT NULL,
  mime        text,
  bytes       bigint,
  storage_ref text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS task_artifact_task_idx ON task_artifact (task_id, created_at);

-- ---------------------------------------------------------------------------
-- Context / audit (context_trace and context_outbox are shared with the
-- transition baseline; add the columns without destructive changes)
-- ---------------------------------------------------------------------------
ALTER TABLE context_trace ADD COLUMN IF NOT EXISTS budget jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE context_trace ADD COLUMN IF NOT EXISTS channel text;

CREATE TABLE IF NOT EXISTS audit_log (
  id          bigserial PRIMARY KEY,
  tenant_id   text NOT NULL,
  app_user_id text NOT NULL,
  action      text NOT NULL,
  resource_id text,
  result      text NOT NULL CHECK (result IN ('ok','denied','error')),
  ip          text,
  trace_id    text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_owner_idx
  ON audit_log (tenant_id, app_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION gpa_touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversation_touch_updated_at ON conversation;
CREATE TRIGGER conversation_touch_updated_at
  BEFORE UPDATE ON conversation
  FOR EACH ROW EXECUTE FUNCTION gpa_touch_updated_at();

DROP TRIGGER IF EXISTS task_run_touch_updated_at ON task_run;
CREATE TRIGGER task_run_touch_updated_at
  BEFORE UPDATE ON task_run
  FOR EACH ROW EXECUTE FUNCTION gpa_touch_updated_at();

INSERT INTO schema_migrations (version, description)
VALUES ('005_gpa_core', 'GPA-native conversation/task core schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
