-- 助手工作台（workbench）迁移 · 依据《助手工作台方案设计_v1.3》§3
-- 原则：只增 + 少量改列；每个语句可重复执行（幂等）；`pending` 占位语义用
-- “可空列 + 部分唯一索引”实现，避免 UNIQUE 与重试竞态。
-- 用法：psql "$DATABASE_URL" -f db/migrations/003_wb.sql
-- 注意：tasks 表冻结不改（停写新行、历史只读；origin_task_id / artifacts.task_id
--       的 FK 依赖该表，因此不降级为视图）。

BEGIN;

-- ---------------------------------------------------------------------------
-- ma_session：统一任务执行权威投影（工作台/语音/自动化/任务链同一入口）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ma_session (
  id              text PRIMARY KEY,                -- s_{hex10}，本地生成
  tenant_id       text NOT NULL,
  app_user_id     text NOT NULL REFERENCES app_user,
  ark_session_id  text,                            -- 远端 ID；创建期间为 NULL
  agent_kind      text NOT NULL CHECK (agent_kind IN ('platform','personal')),
  ark_agent_id    text NOT NULL,
  agent_version   text NOT NULL DEFAULT 'v1',
  environment_id  text NOT NULL,
  title           text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'initializing'
                  CHECK (status IN ('initializing','running','rescheduled','upgrading',
                                    'idle','terminated','failed','cancelled')),
  raw_status      text,                            -- Ark 表外状态兜底（按 running 处理）
  source          text NOT NULL CHECK (source IN ('voice','workbench','automation','chain')),
  origin_task_id  text REFERENCES tasks(task_id),  -- 语音回链（tasks 为冻结归档表）
  group_id        uuid,                            -- 多意图任务链（§3.3）
  seq_in_group    integer,
  chain_state     text CHECK (chain_state IN ('pending','ready','dispatched','skipped')),
  deps            jsonb NOT NULL DEFAULT '[]'::jsonb,  -- 依赖的组内 seq 列表
  temporary       boolean NOT NULL DEFAULT false,
  create_attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz,
  max_runtime_at  timestamptz,                     -- running 总超时护栏
  last_error      text,
  archived_at     timestamptz,
  pinned_at       timestamptz,
  last_event_id   text,
  last_event_at   timestamptz,
  last_checked_at timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ma_session_ark_unique
  ON ma_session (ark_session_id) WHERE ark_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ma_session_owner_idx ON ma_session (app_user_id, archived_at);
CREATE INDEX IF NOT EXISTS ma_session_status_idx ON ma_session (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ma_session_group_idx ON ma_session (group_id);

-- ---------------------------------------------------------------------------
-- ma_session_turn：turn 结算（§3.2）—— 一次用户提交 = 一个 turn；
-- 通知 / OV 摘要 / 产物发现 / 语音播报全部按 turn 幂等。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ma_session_turn (
  id                uuid PRIMARY KEY,
  ma_session_id     text NOT NULL REFERENCES ma_session(id) ON DELETE CASCADE,
  app_user_id       text NOT NULL,
  seq               integer NOT NULL,
  input_event_id    text NOT NULL,                 -- 触发的 user.message
  settled_event_id  text,                          -- 结算依据的 status 事件
  outcome           text CHECK (outcome IN ('running','completed','failed','cancelled')),
  stop_reason       text,                          -- end_turn / max_tokens / ...
  summary           text NOT NULL DEFAULT '',      -- 播报摘要缓存
  announced_at      timestamptz,                   -- 语音已播标记（幂等）
  ov_written_at     timestamptz,                   -- OV 摘要已写标记
  artifact_ids      jsonb NOT NULL DEFAULT '[]'::jsonb,
  cancel_requested  boolean NOT NULL DEFAULT false,
  started_at        timestamptz NOT NULL DEFAULT now(),
  ended_at          timestamptz,
  UNIQUE (ma_session_id, seq)
);
CREATE INDEX IF NOT EXISTS ma_session_turn_open_idx
  ON ma_session_turn (ma_session_id) WHERE outcome = 'running';

-- ---------------------------------------------------------------------------
-- ma_session_event：Ark 事件投影（唯一事实源；event_id 幂等去重）
-- 保留策略：随会话保留、删除级联；demo 期不设 TTL（P2 增加 180 天归档）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ma_session_event (
  event_id     text NOT NULL,
  session_id   text NOT NULL REFERENCES ma_session(id) ON DELETE CASCADE,
  app_user_id  text NOT NULL,
  source_type  text NOT NULL,
  type         text NOT NULL,                      -- UiEvent type（message/thinking/...）
  payload      jsonb NOT NULL DEFAULT '{}'::jsonb, -- 脱敏后的 UiEvent payload
  occurred_at  timestamptz NOT NULL,
  ingested_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, event_id)
);
CREATE INDEX IF NOT EXISTS ma_session_event_time_idx ON ma_session_event (session_id, occurred_at);

-- ---------------------------------------------------------------------------
-- ma_session_input：任务材料（uploading→ready→bound；过期置 expired）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ma_session_input (
  id            uuid PRIMARY KEY,
  app_user_id   text NOT NULL REFERENCES app_user,
  session_id    text REFERENCES ma_session(id),
  ark_file_id   text,
  original_name text NOT NULL,
  mime_type     text,
  size_bytes    bigint,
  mount_path    text NOT NULL,
  status        text NOT NULL DEFAULT 'uploading'
                CHECK (status IN ('uploading','ready','bound','failed','expired')),
  error         text,
  expires_at    timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ma_session_input_user_idx
  ON ma_session_input (app_user_id, status, created_at DESC);

-- ---------------------------------------------------------------------------
-- ma_session_task_group：多意图任务链（D6/§3.3）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ma_session_task_group (
  id                     uuid PRIMARY KEY,
  app_user_id            text NOT NULL,
  source                 text,
  origin_text            text,                     -- 用户原话
  state                  text NOT NULL DEFAULT 'running'
                         CHECK (state IN ('running','done','failed','cancelled')),
  pending_clarifications jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{seq,question,candidates}]
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- wb_submission：submissionId 幂等回执（canonical request_hash 照搬 MAWorker）
-- turn_id：受理即建 turn（input_event_id 提交后回填）；重放返回同一 turnId。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wb_submission (
  id           text PRIMARY KEY,                   -- submissionId（客户端生成）
  app_user_id  text NOT NULL,
  ma_session_id text NOT NULL REFERENCES ma_session(id) ON DELETE CASCADE,
  turn_id      uuid,
  request_hash text NOT NULL,                      -- canonical JSON 哈希
  payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
  status       text NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted','delivered','conflict')),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_submission_user_idx ON wb_submission (app_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- wb_change：per-user 变更流（TTL 30 天，维护车道清理）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wb_change (
  seq         bigserial PRIMARY KEY,
  app_user_id text NOT NULL,
  kind        text NOT NULL,                       -- session/artifact/notification/...
  entity_id   text,
  hints       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_change_user_idx ON wb_change (app_user_id, seq);
CREATE INDEX IF NOT EXISTS wb_change_ttl_idx ON wb_change (created_at);

-- ---------------------------------------------------------------------------
-- wb_notification：人读通知（TTL 90 天；按 (session,turn,kind) 幂等）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wb_notification (
  id          uuid PRIMARY KEY,
  app_user_id text NOT NULL,
  session_id  text,
  turn_id     uuid,
  kind        text NOT NULL,                       -- turn.completed / turn.failed / ...
  title       text NOT NULL,
  body        text NOT NULL DEFAULT '',
  read_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, turn_id, kind)
);
CREATE INDEX IF NOT EXISTS wb_notification_user_idx ON wb_notification (app_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- wb_automation / occurrence：自动化（P2 完整调度；本期建表）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wb_automation (
  id          uuid PRIMARY KEY,
  app_user_id text NOT NULL,
  title       text NOT NULL,
  content     text NOT NULL,
  schedule    jsonb NOT NULL,                      -- {kind: daily|weekly|once, ...}
  timezone    text NOT NULL DEFAULT 'Asia/Shanghai',
  enabled     boolean NOT NULL DEFAULT true,
  kind        text NOT NULL DEFAULT 'run' CHECK (kind IN ('run','remind')),
  last_run_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
-- 旧库补列（kind 在 v1.3 首版迁移中缺失）
ALTER TABLE wb_automation ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'run'
  CHECK (kind IN ('run','remind'));

CREATE TABLE IF NOT EXISTS wb_automation_occurrence (
  id           uuid PRIMARY KEY,
  automation_id uuid NOT NULL REFERENCES wb_automation(id) ON DELETE CASCADE,
  run_at       timestamptz NOT NULL,
  ma_session_id text,
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','done','failed','skipped')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (automation_id, run_at)
);

-- ---------------------------------------------------------------------------
-- wb_skill / wb_mcp_server / wb_personal_agent（P3；本期建表）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wb_skill (
  id          uuid PRIMARY KEY,
  app_user_id text NOT NULL,
  name        text NOT NULL,
  ark_skill_id text,
  size_bytes  bigint,
  status      text NOT NULL DEFAULT 'ready' CHECK (status IN ('uploading','ready','failed')),
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (app_user_id, name)
);

CREATE TABLE IF NOT EXISTS wb_mcp_server (
  id          uuid PRIMARY KEY,
  app_user_id text NOT NULL,
  name        text NOT NULL,
  url         text NOT NULL,
  status      text NOT NULL DEFAULT 'active',
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (app_user_id, name)
);

CREATE TABLE IF NOT EXISTS wb_personal_agent (
  id           uuid PRIMARY KEY,
  app_user_id  text NOT NULL UNIQUE,               -- auto-default：每用户一行
  ark_agent_id text NOT NULL,
  model_id     text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- artifacts 扩展列：task_id 可空（工作台任务无语音 task）、ma_session 归属、
-- turn 归属、远端唯一键、自管副本（own_tos_key）。
-- ---------------------------------------------------------------------------
ALTER TABLE artifacts ALTER COLUMN task_id DROP NOT NULL;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS ma_session_id text REFERENCES ma_session(id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS turn_id uuid;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS ark_file_id text;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS mime_type text;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS own_tos_key text;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS copy_status text NOT NULL DEFAULT 'pending'
  CHECK (copy_status IN ('pending','done','failed','disabled'));
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS copy_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
-- 幂等登记依据：同会话同远端文件只登记一次（历史行 ark_file_id 为 NULL 不受约束）
CREATE UNIQUE INDEX IF NOT EXISTS artifacts_session_file_unique
  ON artifacts (ma_session_id, ark_file_id) WHERE ark_file_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS artifacts_session_idx ON artifacts (ma_session_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- v_task_context：TaskSource 任务视图（设计 §5.2）—— 修复“工作台 title 为空
-- 导致【任务】段无内容”：title 回退取首条 user.message 前 24 字。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_task_context AS
SELECT s.id, s.app_user_id, s.source, s.status,
       COALESCE(NULLIF(s.title, ''),
                LEFT((SELECT e.payload->>'content' FROM ma_session_event e
                       WHERE e.session_id = s.id AND e.source_type = 'user.message'
                      ORDER BY e.occurred_at LIMIT 1), 24)) AS title,
       t.summary AS latest_summary,
       t.outcome AS turn_outcome,
       t.id AS turn_id,
       (SELECT json_agg(json_build_object('id', a.artifact_id, 'name', a.name,
                     'mime', a.mime_type) ORDER BY a.created_at DESC)
          FROM (SELECT a2.* FROM artifacts a2
                 WHERE a2.ma_session_id = s.id AND a2.deleted_at IS NULL
                 ORDER BY a2.created_at DESC LIMIT 2) a) AS top_artifacts
  FROM ma_session s
  LEFT JOIN LATERAL (SELECT * FROM ma_session_turn t2
                      WHERE t2.ma_session_id = s.id ORDER BY t2.seq DESC LIMIT 1) t ON true;

COMMIT;
