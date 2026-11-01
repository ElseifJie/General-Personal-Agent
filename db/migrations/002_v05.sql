-- V0.5 上下文管理迁移（实施方案 A1/A4/A6/B1 + kb_docs 增列）
-- 原则：只增不改；每个语句可重复执行（幂等）。
-- 用法：psql "$DATABASE_URL" -f db/migrations/002_v05.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- A1 稳定会话：conversation_id 30 分钟活跃窗口；ov_session_id 与 OV 会话 1:1
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_session (
  conversation_id text PRIMARY KEY,
  tenant_id       text NOT NULL,
  app_user_id     text NOT NULL,
  device_id       text NOT NULL DEFAULT 'web',
  ov_session_id   text,
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  started_at      timestamptz NOT NULL DEFAULT now(),
  last_active_at  timestamptz NOT NULL DEFAULT now(),
  ended_at        timestamptz
);
CREATE INDEX IF NOT EXISTS agent_session_active_idx
  ON agent_session (app_user_id, device_id, status, last_active_at DESC);

-- 轮次事实（评测与归档对账用）
CREATE TABLE IF NOT EXISTS agent_turn (
  turn_id         text PRIMARY KEY,
  conversation_id text NOT NULL,
  app_user_id     text NOT NULL,
  user_text       text NOT NULL,
  assistant_text  text,
  intent          text,
  trace_id        text,
  context_ms      integer,
  tokens_in       integer,
  tokens_out      integer,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_turn_conv_idx ON agent_turn (conversation_id, created_at);

-- ---------------------------------------------------------------------------
-- A6 Context Trace（脱敏；保留 7 天，由 worker 清理）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_trace (
  trace_id        text PRIMARY KEY,
  turn_id         text,
  conversation_id text,
  app_user_id     text,
  query           text,
  intent          text,
  sources         jsonb NOT NULL DEFAULT '{}'::jsonb,
  tokens          jsonb NOT NULL DEFAULT '{}'::jsonb,
  degraded        jsonb NOT NULL DEFAULT '[]'::jsonb,
  shadow_diff     jsonb,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS context_trace_turn_idx ON context_trace (turn_id);
CREATE INDEX IF NOT EXISTS context_trace_created_idx ON context_trace (created_at);

-- ---------------------------------------------------------------------------
-- A4 Outbox：可靠投递（ov.message / ov.commit / memory.sync / kb.invalidate）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_outbox (
  id               bigserial PRIMARY KEY,
  tenant_id        text NOT NULL,
  app_user_id      text NOT NULL,
  conversation_id  text NOT NULL DEFAULT '',
  kind             text NOT NULL,
  payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key  text NOT NULL,
  seq              integer NOT NULL DEFAULT 0,
  status           text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','inflight','done','dead')),
  attempts         integer NOT NULL DEFAULT 0,
  next_at          timestamptz NOT NULL DEFAULT now(),
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS context_outbox_dispatch_idx
  ON context_outbox (status, next_at, id);
CREATE INDEX IF NOT EXISTS context_outbox_conv_idx
  ON context_outbox (conversation_id, seq);

-- ---------------------------------------------------------------------------
-- B1/B5 记忆目录与审计（OV 是语义内容，PG 是控制面）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_item (
  memory_id      text PRIMARY KEY,
  tenant_id      text NOT NULL,
  app_user_id    text NOT NULL,
  type           text NOT NULL
                 CHECK (type IN ('profile','preferences','entities','events')),
  memory_key     text NOT NULL,
  ov_uri         text,
  summary        text NOT NULL,
  confidence     real NOT NULL DEFAULT 1.0,
  source         text NOT NULL DEFAULT 'explicit' CHECK (source IN ('explicit','extracted')),
  source_turn_id text,
  sensitivity    text NOT NULL DEFAULT 'low' CHECK (sensitivity IN ('low','medium','high')),
  status         text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','superseded','deleted')),
  pinned         boolean NOT NULL DEFAULT false,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  deleted_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (app_user_id, memory_key)
);
CREATE INDEX IF NOT EXISTS memory_item_user_idx
  ON memory_item (app_user_id, status, type, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_event (
  id          bigserial PRIMARY KEY,
  memory_id   text NOT NULL,
  app_user_id text NOT NULL,
  action      text NOT NULL CHECK (action IN ('create','update','delete','clear','diff_apply','ov_write_fail')),
  actor       text NOT NULL DEFAULT 'system',
  before_ref  jsonb,
  after_ref   jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memory_event_user_idx ON memory_event (app_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- C1/C2 个人 KB：kb_docs 增列（保留旧 status 约束，细化状态进 phase）
-- A5 共享 KB 写审计
-- ---------------------------------------------------------------------------
ALTER TABLE kb_docs ADD COLUMN IF NOT EXISTS phase text NOT NULL DEFAULT 'processing';
ALTER TABLE kb_docs ADD COLUMN IF NOT EXISTS tos_key text;
ALTER TABLE kb_docs ADD COLUMN IF NOT EXISTS content_hash text;
ALTER TABLE kb_docs ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE INDEX IF NOT EXISTS kb_docs_hash_idx ON kb_docs (app_user_id, content_hash);

CREATE TABLE IF NOT EXISTS kb_audit (
  id          bigserial PRIMARY KEY,
  actor       text NOT NULL,
  action      text NOT NULL,
  scope       text NOT NULL,
  target_uri  text,
  doc_id      text,
  detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_audit_actor_idx ON kb_audit (actor, created_at DESC);

-- ADK STM 独立 schema（应用启动时由 veadk 建表，这里只保证 schema 存在）
CREATE SCHEMA IF NOT EXISTS agent_stm;

COMMIT;
