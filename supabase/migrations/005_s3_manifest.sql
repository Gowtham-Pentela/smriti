-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 005: s3_manifest — single source of truth for "what keys exist"
-- ─────────────────────────────────────────────────────────────────────────────
-- One row per (tenant, s3_key) the system has ever indexed. Updated whenever
-- the S3 sync loop or the SQS event-driven path processes a key, and deleted
-- when the sync loop notices the key is gone from the bucket.
--
-- The sync loop diffs ListBucketV2 against this table to detect deletes and
-- content changes. The event-driven SQS path upserts on every ObjectCreated.
--
-- Note: s3_manifest is S3-scoped only. local:// sources (from demo_data/seed)
-- are not represented here.

CREATE TABLE IF NOT EXISTS public.s3_manifest (
    tenant_id     UUID        NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    s3_key        TEXT        NOT NULL,
    bucket        TEXT        NOT NULL,
    source_url    TEXT        NOT NULL,           -- 's3://bucket/key' (derived, indexed)
    -- file_hash is SHA-256 of the bytes for multi-part objects, or the S3
    -- single-part ETag (= MD5 of bytes) when we did not download. The asymmetry
    -- is documented in backend.s3_connector._sha256_of_s3_object. The
    -- expensive-diff path (file_hash) is only consulted when the cheap path
    -- (etag) is inconclusive.
    file_hash     TEXT        NOT NULL,
    etag          TEXT        NOT NULL,           -- raw ETag from S3 (cheap-diff key)
    size_bytes    BIGINT      NOT NULL DEFAULT 0,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_etag_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, s3_key)
);

CREATE INDEX IF NOT EXISTS idx_s3_manifest_source_url
    ON public.s3_manifest(tenant_id, source_url);

CREATE INDEX IF NOT EXISTS idx_s3_manifest_last_seen
    ON public.s3_manifest(tenant_id, last_seen_at DESC);

-- Same RLS shape as ingestion_hashes: 4 policies, all scoped to
-- app.current_tenant_id.
ALTER TABLE public.s3_manifest ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_s3_manifest ON public.s3_manifest;
CREATE POLICY tenant_isolation_s3_manifest ON public.s3_manifest
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

DROP POLICY IF EXISTS tenant_insert_s3_manifest ON public.s3_manifest;
CREATE POLICY tenant_insert_s3_manifest ON public.s3_manifest
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

DROP POLICY IF EXISTS tenant_update_s3_manifest ON public.s3_manifest;
CREATE POLICY tenant_update_s3_manifest ON public.s3_manifest
    FOR UPDATE
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

DROP POLICY IF EXISTS tenant_delete_s3_manifest ON public.s3_manifest;
CREATE POLICY tenant_delete_s3_manifest ON public.s3_manifest
    FOR DELETE
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
