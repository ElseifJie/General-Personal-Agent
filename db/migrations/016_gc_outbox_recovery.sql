-- GC deleting leases, legacy outbox recovery and delete_failed visibility.
-- Additive; safe on an existing 015 database.
BEGIN;

ALTER TABLE file_object ADD COLUMN IF NOT EXISTS gc_lease_until timestamptz;
ALTER TABLE file_object ADD COLUMN IF NOT EXISTS gc_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE file_object ADD COLUMN IF NOT EXISTS gc_error text;

ALTER TABLE file_object DROP CONSTRAINT IF EXISTS file_object_gc_state_check;
ALTER TABLE file_object ADD CONSTRAINT file_object_gc_state_check
  CHECK (gc_state IN ('active','candidate','deleting','deleted','delete_failed'));

CREATE INDEX IF NOT EXISTS file_object_gc_lease_idx
  ON file_object (gc_state, gc_lease_until);

-- Rows claimed by pre-lease code have deleted_at set and no lease.  Keep the
-- logical delete and let the next claim retry the TOS deletion.
UPDATE file_object
   SET deleted_at = COALESCE(deleted_at, now())
 WHERE gc_state IN ('deleting', 'deleted');

-- Pre-015 outbox rows have an inflight status but no lease.  Make them
-- immediately reclaimable instead of blocking their conversation forever.
UPDATE context_outbox
   SET lease_until = now(), claim_token = NULL, updated_at = now()
 WHERE status = 'inflight' AND lease_until IS NULL;

INSERT INTO schema_migrations (version, description)
VALUES ('016_gc_outbox_recovery', 'GC deleting leases and legacy inflight/outbox recovery')
ON CONFLICT (version) DO NOTHING;

COMMIT;
