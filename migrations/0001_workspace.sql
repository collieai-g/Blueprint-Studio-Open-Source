CREATE TABLE users (
 id UUID PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','admin','member','viewer','guest')),
 groups JSONB NOT NULL DEFAULT '[]', active BOOLEAN NOT NULL DEFAULT true,
 totp_secret TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id UUID NOT NULL REFERENCES users(id),expires_at TIMESTAMPTZ NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE api_tokens(id UUID PRIMARY KEY,user_id UUID NOT NULL REFERENCES users(id),name TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,scopes JSONB NOT NULL,resource_ids JSONB,expires_at TIMESTAMPTZ,revoked_at TIMESTAMPTZ,last_used_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE company_settings(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),company JSONB NOT NULL DEFAULT '{}',modules JSONB NOT NULL DEFAULT '{}',updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO company_settings(singleton) VALUES(true);
CREATE TABLE resources(
 id UUID PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,parent_id UUID REFERENCES resources(id),owner_id UUID NOT NULL REFERENCES users(id),
 visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','company')),acl JSONB NOT NULL DEFAULT '{}',data JSONB NOT NULL DEFAULT '{}',
 version BIGINT NOT NULL DEFAULT 1,search_document TEXT NOT NULL DEFAULT '',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),deleted_at TIMESTAMPTZ
);
CREATE INDEX resources_kind_active ON resources(kind,updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX resources_parent ON resources(parent_id);
CREATE INDEX resources_data ON resources USING GIN(data);
CREATE INDEX resources_acl ON resources USING GIN(acl);
CREATE TABLE resource_history(id BIGSERIAL PRIMARY KEY,resource_id UUID NOT NULL REFERENCES resources(id),version BIGINT NOT NULL,actor_id UUID NOT NULL REFERENCES users(id),snapshot JSONB NOT NULL,action TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),UNIQUE(resource_id,version));
CREATE TABLE audit_log(id BIGSERIAL PRIMARY KEY,actor_id UUID REFERENCES users(id),action TEXT NOT NULL,resource_id UUID,details JSONB NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX audit_time ON audit_log(created_at DESC);
CREATE TABLE jobs(id UUID PRIMARY KEY,owner_id UUID NOT NULL REFERENCES users(id),kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',payload JSONB NOT NULL DEFAULT '{}',result JSONB NOT NULL DEFAULT '{}',error TEXT,attempts INTEGER NOT NULL DEFAULT 0,idempotency_key TEXT,available_at TIMESTAMPTZ NOT NULL DEFAULT now(),lease_until TIMESTAMPTZ,lease_owner UUID,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX jobs_idempotency ON jobs(owner_id,kind,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX jobs_claim ON jobs(available_at,created_at) WHERE status IN ('queued','running');
CREATE TABLE workflow_effects(run_id UUID NOT NULL,node_key TEXT NOT NULL,result JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(run_id,node_key));
CREATE TABLE auth_challenges(token_hash TEXT PRIMARY KEY,user_id UUID REFERENCES users(id),purpose TEXT NOT NULL,data JSONB NOT NULL DEFAULT '{}',expires_at TIMESTAMPTZ NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE login_attempts(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0,window_start TIMESTAMPTZ NOT NULL DEFAULT now());
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS pgroonga;
  EXECUTE 'CREATE INDEX resources_search_pgroonga ON resources USING pgroonga(search_document)';
EXCEPTION WHEN undefined_file OR feature_not_supported OR insufficient_privilege THEN
  RAISE NOTICE 'PGroonga is not available; text search uses the PostgreSQL fallback';
END $$;
