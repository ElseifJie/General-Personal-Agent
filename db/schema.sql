-- 历史基线 schema（上一代语音 Agent Demo 遗留），不是当前最终表结构。
-- 全新空库先执行本文件，再依次应用 db/migrations/002..018；其中 007_drop_legacy.sql
-- 会删除本基线里不再使用的旧表，最终收敛为 GPA-native schema（见 README「当前状态」）。
-- 适用：PostgreSQL 14+。
-- 用法：psql "postgresql://demo:demo@127.0.0.1:5432/general_personal_agent" -f db/schema.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- Demo 登录账号（真实产品替换为 SSO / 用户中心）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user (
  app_user_id  text PRIMARY KEY,
  display_name text NOT NULL,
  role         text NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
  pin_hash     text,
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 身份映射：应用身份 -> OpenViking account/user/peer
-- key_ref 只存密钥引用名（对应 .env 变量或 KMS 路径），不存明文
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ov_identity (
  tenant_id   text NOT NULL,
  app_user_id text NOT NULL,
  ov_account  text NOT NULL,
  ov_user     text NOT NULL,
  ov_peer     text,
  auth_mode   text NOT NULL DEFAULT 'api_key' CHECK (auth_mode IN ('api_key','trusted','oidc')),
  key_ref     text,
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','deleted')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, app_user_id)
);
CREATE INDEX IF NOT EXISTS ov_identity_user_idx ON ov_identity (ov_account, ov_user);

-- ---------------------------------------------------------------------------
-- 精确 transcript（事实源；OpenViking 只存语义摘要）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcript (
  id          bigserial PRIMARY KEY,
  tenant_id   text NOT NULL,
  app_user_id text NOT NULL,
  session_id  text NOT NULL,
  role        text NOT NULL CHECK (role IN ('user','assistant','system')),
  text        text NOT NULL,
  source      text NOT NULL DEFAULT 'voice',
  ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transcript_owner_idx ON transcript (app_user_id, session_id, ts);

-- ---------------------------------------------------------------------------
-- 任务与事件
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
  task_id            text PRIMARY KEY,
  tenant_id          text NOT NULL,
  app_user_id        text NOT NULL,
  type               text NOT NULL,
  state              text NOT NULL CHECK (state IN ('queued','running','completed','failed','cancelled')),
  progress           real NOT NULL DEFAULT 0,
  brief              jsonb NOT NULL,
  backend            text,
  backend_session_id text,
  error              text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_owner_idx ON tasks (app_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_events (
  task_id    text NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  seq        integer NOT NULL,
  type       text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id text PRIMARY KEY,
  task_id     text NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  name        text NOT NULL,
  tos_key     text NOT NULL,
  bytes       bigint,
  version     integer NOT NULL DEFAULT 1,
  expires_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 知识库导入记录
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_docs (
  doc_id      text PRIMARY KEY,
  tenant_id   text NOT NULL,
  app_user_id text NOT NULL,
  scope       text NOT NULL CHECK (scope IN ('personal','admin')),
  target_uri  text NOT NULL,
  filename    text,
  bytes       bigint,
  ov_task_id  text,
  status      text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing','ready','failed')),
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 幂等
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idempotency (
  key        text PRIMARY KEY,
  response   jsonb NOT NULL,
  expires_at timestamptz NOT NULL
);

-- ---------------------------------------------------------------------------
-- 种子数据：与 OpenViking 控制台现有用户对齐（default / Alice / Bob）
-- ---------------------------------------------------------------------------
INSERT INTO app_user (app_user_id, display_name, role) VALUES
  ('alice', 'Alice', 'user'),
  ('bob',   'Bob',   'user'),
  ('admin', 'Admin', 'admin')
ON CONFLICT (app_user_id) DO NOTHING;

INSERT INTO ov_identity (tenant_id, app_user_id, ov_account, ov_user, ov_peer, key_ref) VALUES
  ('demo', 'alice', 'default', 'Alice',   'device_alice', 'OV_USER_KEY_ALICE'),
  ('demo', 'bob',   'default', 'Bob',     'device_bob',   'OV_USER_KEY_BOB'),
  ('demo', 'admin', 'default', 'default', NULL,           'OV_USER_KEY_ADMIN')
ON CONFLICT (tenant_id, app_user_id) DO NOTHING;

COMMIT;
