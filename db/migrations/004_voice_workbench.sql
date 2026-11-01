-- 语音工作台迁移 · 语音会话 / 任务 / 产物的持久化与端到端读取
-- 原则：只增列/只增表；全部语句可重复执行；不改写既有语义。
-- 用法：psql "$DATABASE_URL" -f db/migrations/004_voice_workbench.sql
--
-- 背景：legacy 语音任务只有 tasks + task_events，没有 ma_session；观测台又忽略
-- task/artifact 帧，导致“任务执行完成但用户看不到后续”。本迁移为语音工作台补齐
-- 会话标题/摘要、轮次时延、工具事件、任务来源与语音会话回链，使历史可回看。

BEGIN;

-- ---------------------------------------------------------------------------
-- agent_session：语音会话历史（此前只有 active 窗口判断，没有可展示的标题/摘要）
-- ---------------------------------------------------------------------------
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT '';
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS summary text NOT NULL DEFAULT '';
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'voice';
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS last_message_at timestamptz;

CREATE INDEX IF NOT EXISTS agent_session_owner_list_idx
  ON agent_session (app_user_id, last_active_at DESC);
CREATE INDEX IF NOT EXISTS agent_session_last_message_idx
  ON agent_session (app_user_id, last_message_at DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- agent_turn：补齐语音轮次时延，工作台可直接回看端到端指标
-- ---------------------------------------------------------------------------
ALTER TABLE agent_turn ADD COLUMN IF NOT EXISTS asr_ms integer;
ALTER TABLE agent_turn ADD COLUMN IF NOT EXISTS first_audio_ms integer;
ALTER TABLE agent_turn ADD COLUMN IF NOT EXISTS llm_ttft_ms integer;
ALTER TABLE agent_turn ADD COLUMN IF NOT EXISTS turn_ms integer;

-- ---------------------------------------------------------------------------
-- agent_turn_event：语音轮次内的工具调用/结果（历史回放用；payload 仅摘要）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_turn_event (
  id               bigserial PRIMARY KEY,
  turn_id          text NOT NULL,
  conversation_id  text NOT NULL,
  app_user_id      text NOT NULL,
  type             text NOT NULL CHECK (type IN
                     ('tool_call', 'tool_result', 'task', 'artifact', 'notice')),
  name             text,
  payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_turn_event_turn_idx
  ON agent_turn_event (turn_id, id);
CREATE INDEX IF NOT EXISTS agent_turn_event_conv_idx
  ON agent_turn_event (conversation_id, id);

-- ---------------------------------------------------------------------------
-- tasks：来源与语音会话回链（工作台/自动化建的任务不落 tasks，新增列用于显式契约）
-- 旧数据：brief.session_id 形如 c_{user}_{device}_{hex}，回填到 voice_session_id。
-- ---------------------------------------------------------------------------
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'voice';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS voice_session_id text;

UPDATE tasks
   SET voice_session_id = brief->>'session_id'
 WHERE voice_session_id IS NULL
   AND COALESCE(brief->>'session_id', '') ~ '^c_[A-Za-z0-9_]+$';

CREATE INDEX IF NOT EXISTS tasks_owner_created_idx
  ON tasks (app_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_voice_session_idx
  ON tasks (voice_session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_backend_session_idx
  ON tasks (backend_session_id);

CREATE INDEX IF NOT EXISTS ma_session_origin_task_idx
  ON ma_session (origin_task_id) WHERE origin_task_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 任务链：记录发起会话，澄清补派后仍能回到原语音会话
-- ---------------------------------------------------------------------------
ALTER TABLE ma_session_task_group ADD COLUMN IF NOT EXISTS origin_session_id text;

CREATE INDEX IF NOT EXISTS ma_session_task_group_origin_idx
  ON ma_session_task_group (origin_session_id)
  WHERE origin_session_id IS NOT NULL;

COMMIT;
