-- File upload + knowledge base
-- Additive only: no existing table is renamed or dropped.
BEGIN;

CREATE TABLE IF NOT EXISTS file_object (
  file_id          text PRIMARY KEY,
  tenant_id        text NOT NULL,
  app_user_id      text NOT NULL,
  sha256           text NOT NULL,
  name             text NOT NULL,
  mime             text,
  bytes            bigint,
  storage_ref      text,
  status           text NOT NULL DEFAULT 'uploading'
                   CHECK (status IN ('uploading','stored','extracting','ready','failed','deleted')),
  text_status      text NOT NULL DEFAULT 'pending'
                   CHECK (text_status IN ('pending','extracting','ready','failed','skipped')),
  text_chars       integer NOT NULL DEFAULT 0,
  text_truncated   boolean NOT NULL DEFAULT false,
  page_count       integer,
  error            text,
  gc_state         text NOT NULL DEFAULT 'active'
                   CHECK (gc_state IN ('active','candidate','deleting','deleted')),
  gc_candidate_at  timestamptz,
  meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS file_object_dedupe_idx
  ON file_object (tenant_id, app_user_id, sha256)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS file_object_gc_idx
  ON file_object (gc_state, gc_candidate_at);

CREATE TABLE IF NOT EXISTS conversation_file (
  conversation_file_id text PRIMARY KEY,
  conversation_id      text NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
  file_id              text NOT NULL REFERENCES file_object(file_id) ON DELETE CASCADE,
  tenant_id            text NOT NULL,
  app_user_id          text NOT NULL,
  status               text NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','removed')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  removed_at           timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS conversation_file_active_idx
  ON conversation_file (conversation_id, file_id)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS conversation_file_file_idx
  ON conversation_file (file_id, status);

CREATE TABLE IF NOT EXISTS turn_attachment (
  turn_attachment_id text PRIMARY KEY,
  turn_id            text NOT NULL REFERENCES conversation_turn(turn_id) ON DELETE CASCADE,
  file_id            text NOT NULL REFERENCES file_object(file_id) ON DELETE CASCADE,
  role               text NOT NULL DEFAULT 'input'
                     CHECK (role IN ('input','reference')),
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS turn_attachment_unique_idx
  ON turn_attachment (turn_id, file_id, role);
CREATE INDEX IF NOT EXISTS turn_attachment_file_idx
  ON turn_attachment (file_id);

CREATE TABLE IF NOT EXISTS task_input_file (
  task_input_id  text PRIMARY KEY,
  task_id        text NOT NULL REFERENCES task_run(task_id) ON DELETE CASCADE,
  attempt_id     uuid REFERENCES task_attempt(attempt_id) ON DELETE SET NULL,
  file_id        text NOT NULL REFERENCES file_object(file_id) ON DELETE CASCADE,
  mount_path     text NOT NULL,
  ma_file_id     text,
  ma_file_status text NOT NULL DEFAULT 'pending'
                 CHECK (ma_file_status IN ('pending','uploaded','ready','failed','deleted')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS task_input_file_unique_idx
  ON task_input_file (task_id, file_id);
CREATE INDEX IF NOT EXISTS task_input_file_file_idx
  ON task_input_file (file_id, ma_file_status);

CREATE TABLE IF NOT EXISTS kb_document (
  doc_id             text PRIMARY KEY,
  file_id            text NOT NULL REFERENCES file_object(file_id) ON DELETE CASCADE,
  tenant_id          text NOT NULL,
  app_user_id        text NOT NULL,
  scope              text NOT NULL CHECK (scope IN ('private','shared')),
  collection         text NOT NULL DEFAULT 'default',
  target_uri         text NOT NULL,
  logical_path       text NOT NULL,
  uri_version        integer NOT NULL DEFAULT 0,
  content_sha256     text NOT NULL,
  title              text,
  index_status       text NOT NULL DEFAULT 'pending'
                     CHECK (index_status IN
                       ('pending','indexing','reconciling','ready',
                        'deleting','deleted','failed','delete_failed')),
  reconcile_state    text NOT NULL DEFAULT 'none'
                     CHECK (reconcile_state IN ('none','pending','unknown','confirmed','failed')),
  reconcile_attempts integer NOT NULL DEFAULT 0,
  last_reconciled_at timestamptz,
  index_generation   integer NOT NULL DEFAULT 0,
  ov_task_id         text,
  error              text,
  indexed_at         timestamptz,
  deleted_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS kb_document_uri_idx
  ON kb_document (tenant_id, target_uri)
  WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS kb_document_private_dedupe_idx
  ON kb_document (tenant_id, app_user_id, content_sha256)
  WHERE scope = 'private' AND deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS kb_document_shared_dedupe_idx
  ON kb_document (tenant_id, content_sha256)
  WHERE scope = 'shared' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS kb_document_scope_idx
  ON kb_document (tenant_id, scope, logical_path);

CREATE TABLE IF NOT EXISTS kb_tombstone (
  tombstone_id text PRIMARY KEY,
  tenant_id    text NOT NULL,
  app_user_id  text NOT NULL,
  scope        text NOT NULL CHECK (scope IN ('private','shared')),
  uri          text NOT NULL,
  recursive    boolean NOT NULL DEFAULT false,
  reason       text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_tombstone_scope_idx
  ON kb_tombstone (tenant_id, scope, uri);

CREATE TABLE IF NOT EXISTS kb_uri_lock (
  tenant_id    text NOT NULL,
  scope        text NOT NULL CHECK (scope IN ('private','shared')),
  uri_prefix   text NOT NULL,
  mode         text NOT NULL CHECK (mode IN ('delete')),
  owner_job_id text NOT NULL,
  expires_at   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, scope, uri_prefix)
);
CREATE INDEX IF NOT EXISTS kb_uri_lock_expiry_idx ON kb_uri_lock (expires_at);

CREATE TABLE IF NOT EXISTS kb_dir_invalidation (
  tenant_id        text NOT NULL,
  scope            text NOT NULL CHECK (scope IN ('private','shared')),
  dir_uri          text NOT NULL,
  reason           text,
  rebuild_attempts integer NOT NULL DEFAULT 0,
  last_attempt_at  timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  resolved_at      timestamptz,
  PRIMARY KEY (tenant_id, scope, dir_uri)
);
CREATE INDEX IF NOT EXISTS kb_dir_invalidation_open_idx
  ON kb_dir_invalidation (tenant_id, scope)
  WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS file_chunk (
  chunk_id   text PRIMARY KEY,
  file_id    text NOT NULL REFERENCES file_object(file_id) ON DELETE CASCADE,
  seq        integer NOT NULL,
  text       text NOT NULL,
  tokens     integer,
  page_from  integer,
  page_to    integer,
  char_start integer NOT NULL,
  char_end   integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (file_id, seq)
);
CREATE INDEX IF NOT EXISTS file_chunk_file_idx ON file_chunk (file_id, seq);

CREATE TABLE IF NOT EXISTS file_job (
  job_id          text PRIMARY KEY,
  tenant_id       text NOT NULL,
  app_user_id     text NOT NULL,
  type            text NOT NULL
                  CHECK (type IN ('extract','kb_index','kb_reconcile','kb_delete',
                                  'kb_dir_refresh','file_gc')),
  status          text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','done','failed','dead','cancelled')),
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 3,
  run_after       timestamptz NOT NULL DEFAULT now(),
  idempotency_key text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  error           text,
  lease_until     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS file_job_idempotency_idx ON file_job (idempotency_key);
CREATE INDEX IF NOT EXISTS file_job_claim_idx ON file_job (status, run_after);

ALTER TABLE conversation_turn
  ADD COLUMN IF NOT EXISTS attachment_fingerprint text;

INSERT INTO schema_migrations (version, description)
VALUES ('014_files', 'File upload and knowledge base core')
ON CONFLICT (version) DO NOTHING;

COMMIT;
