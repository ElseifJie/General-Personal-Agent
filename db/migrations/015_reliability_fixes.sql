-- Reliability fixes: task ownership/leases, outbox leases, KB lifecycle timestamps.
-- Additive only.
BEGIN;

-- Task execution ownership.  A worker records itself when it claims a task and
-- refreshes heartbeat_at while it runs; startup recovery only fails tasks whose
-- owner has stopped heartbeating.
ALTER TABLE task_run ADD COLUMN IF NOT EXISTS payload_fingerprint text;
ALTER TABLE task_run ADD COLUMN IF NOT EXISTS owner_id text;
ALTER TABLE task_run ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

CREATE INDEX IF NOT EXISTS task_run_running_heartbeat_idx
  ON task_run (status, heartbeat_at)
  WHERE status = 'running';

-- Outbox: leases make an inflight row recoverable after a crash, and
-- claim_token prevents an old worker from completing/failing a reclaimed row.
ALTER TABLE context_outbox ADD COLUMN IF NOT EXISTS lease_until timestamptz;
ALTER TABLE context_outbox ADD COLUMN IF NOT EXISTS claim_token text;

CREATE INDEX IF NOT EXISTS context_outbox_lease_idx
  ON context_outbox (status, lease_until);

-- File jobs get the same claim token so a stale worker cannot mark a
-- reclaimed job done.
ALTER TABLE file_job ADD COLUMN IF NOT EXISTS claim_token text;
ALTER TABLE file_job ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

CREATE INDEX IF NOT EXISTS file_job_lease_idx
  ON file_job (status, lease_until);

-- KB lifecycle: updated_at lets the sweeper find pending/indexing rows that
-- were abandoned by a crashed worker; delete_attempts bounds delete retries.
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS delete_attempts integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS kb_document_stalled_idx
  ON kb_document (index_status, updated_at);

INSERT INTO schema_migrations (version, description)
VALUES ('015_reliability_fixes', 'Task/outbox/job leases, fingerprints, KB lifecycle timestamps')
ON CONFLICT (version) DO NOTHING;

COMMIT;
