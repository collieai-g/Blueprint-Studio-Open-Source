-- A purge records candidates before resource/history deletion. Collection only
-- removes unreferenced immutable objects after the recovery grace period.
CREATE TABLE blob_gc (
 id UUID PRIMARY KEY, storage_mode TEXT NOT NULL, storage_location TEXT NOT NULL,
 object_key TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '', source_id UUID NOT NULL,
 owner_id UUID NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 not_before TIMESTAMPTZ NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(storage_mode,storage_location,object_key,version_id)
);
CREATE INDEX blob_gc_ready ON blob_gc(not_before) WHERE status <> 'removed';
-- Pin newly stored bytes until metadata publication completes. A stale pin has a
-- full day to recover, beyond the bounded upload/processing request deadlines.
CREATE TABLE blob_upload_pins (
 storage_mode TEXT NOT NULL,storage_location TEXT NOT NULL,object_key TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,PRIMARY KEY(storage_mode,storage_location,object_key)
);
