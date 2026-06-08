-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 001: Crash-safe deduplication + tenant credentials store
-- Run once against the Supabase local DB before starting the backend.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Ingestion hash deduplication table ────────────────────────────────────
-- Replaces the in-memory _file_hash_cache dict. Survives restarts and is
-- safe for multi-worker Celery use in future phases.
CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.ingestion_hashes (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    source      TEXT        NOT NULL,   -- 'slack' | 'gmail' | 'drive' | ...
    source_id   TEXT        NOT NULL,   -- original message / file ID from source system
    content_hash TEXT       NOT NULL,   -- SHA-256 of raw_content
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_ingestion_hashes UNIQUE (tenant_id, source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_hashes_tenant_source
    ON tenant_redwood_inference_prod.ingestion_hashes (tenant_id, source);

-- ── 2. Tenant credentials table (Fernet-encrypted OAuth tokens) ──────────────
-- One row per (tenant_id, source). The token_encrypted column stores the
-- Fernet-encrypted JSON blob {"access_token": "...", "refresh_token": "..."}.
-- The master encryption key lives in .env → KGF_ENCRYPTION_KEY.
CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.tenant_credentials (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        TEXT        NOT NULL,
    source           TEXT        NOT NULL,   -- 'slack' | 'gmail' | 'drive' | ...
    token_encrypted  BYTEA       NOT NULL,   -- Fernet(json_blob)
    scopes           TEXT[]      NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tenant_credentials UNIQUE (tenant_id, source)
);

-- ── 3. RLS for new tables (mirrors existing vector_chunks policy) ─────────────
ALTER TABLE tenant_redwood_inference_prod.ingestion_hashes  ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_redwood_inference_prod.tenant_credentials ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_ingestion_hashes ON tenant_redwood_inference_prod.ingestion_hashes
    USING (tenant_id = current_setting('app.current_tenant_id', true));

CREATE POLICY tenant_isolation_tenant_credentials ON tenant_redwood_inference_prod.tenant_credentials
    USING (tenant_id = current_setting('app.current_tenant_id', true));
