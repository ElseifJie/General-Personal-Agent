-- 可重试任务的退避窗口。
--
-- requeue 会把任务放回 queued，但 created_at 不变：worker 下一次 claim 会立刻
-- 取回同一条任务，TASK_MAX_RETRY 在几秒内就被烧完。next_retry_at 让 claim 在
-- 到点前跳过它，重试才真正发生在“稍后”。
BEGIN;

ALTER TABLE task_run ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;

-- claim_next 的过滤条件包含 status / executor / next_retry_at。
CREATE INDEX IF NOT EXISTS task_run_ready_idx
  ON task_run (executor, created_at)
  WHERE status = 'queued';

INSERT INTO schema_migrations (version, description)
VALUES ('018_task_retry_backoff', 'Retry backoff column and queued-task index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
