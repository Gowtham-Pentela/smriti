-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 002: vector_chunks with HNSW index and RLS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.vector_chunks (
    id           BIGSERIAL   PRIMARY KEY,
    tenant_id    UUID        NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    source       TEXT        NOT NULL,                 -- e.g. "s3://bucket/key" or "upload:file.pdf"
    source_type  TEXT        NOT NULL DEFAULT 'document',  -- document | image | transcript
    location     TEXT        NOT NULL DEFAULT '',      -- free-form pointer: page, slide, timestamp
    content      TEXT        NOT NULL,
    embedding    vector(768) NOT NULL,
    file_hash    TEXT        NOT NULL,                 -- for dedup
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for sub-200ms cosine search at 100M+ vectors
CREATE INDEX IF NOT EXISTS idx_vector_chunks_embedding_hnsw
    ON public.vector_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_vector_chunks_tenant_id
    ON public.vector_chunks(tenant_id);

CREATE INDEX IF NOT EXISTS idx_vector_chunks_file_hash
    ON public.vector_chunks(tenant_id, file_hash);

-- Full-text search index for keyword fallback
CREATE INDEX IF NOT EXISTS idx_vector_chunks_content_fts
    ON public.vector_chunks
    USING gin (to_tsvector('english', content));

-- Row-Level Security: every chunk is gated by app.current_tenant_id.
-- The app sets this on every connection via SET LOCAL.
ALTER TABLE public.vector_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_vector_chunks ON public.vector_chunks
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- Allow insert as long as the row's tenant_id matches the session tenant
CREATE POLICY tenant_insert_vector_chunks ON public.vector_chunks
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
