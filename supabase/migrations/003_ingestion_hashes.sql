-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 003: ingestion_hashes — crash-safe dedup
-- ─────────────────────────────────────────────────────────────────────────────
-- A row here means "we have already seen and embedded a file with this hash".
-- The S3 worker and the /ingest endpoint both check this before embedding,
-- so re-deliveries from SQS or duplicate file uploads become no-ops.

CREATE TABLE IF NOT EXISTS public.ingestion_hashes (
    tenant_id   UUID        NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    file_hash   TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    chunks      INTEGER     NOT NULL DEFAULT 0,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_hashes_ingested_at
    ON public.ingestion_hashes(ingested_at DESC);

ALTER TABLE public.ingestion_hashes ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_ingestion_hashes ON public.ingestion_hashes
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

CREATE POLICY tenant_insert_ingestion_hashes ON public.ingestion_hashes
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
