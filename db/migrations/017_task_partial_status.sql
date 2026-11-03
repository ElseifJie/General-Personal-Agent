-- 任务可能在“已产出文件、但没走完总结”时被迫收尾（例如执行器预算耗尽）。
-- 这种结果不该整单丢弃，也不该冒充 completed：新增 partial 终态承载“部分交付”。
BEGIN;

ALTER TABLE task_run DROP CONSTRAINT IF EXISTS task_run_status_check;
ALTER TABLE task_run ADD CONSTRAINT task_run_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled'));

INSERT INTO schema_migrations (version, description)
VALUES ('017_task_partial_status', 'Partial task status for salvageable deliverables')
ON CONFLICT (version) DO NOTHING;

COMMIT;
