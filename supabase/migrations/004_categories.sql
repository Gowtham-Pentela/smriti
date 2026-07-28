-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 004: categories on vector_chunks
-- ─────────────────────────────────────────────────────────────────────────────
-- Adds a free-form category column (compliance, products, internal, general).
-- The S3 worker and the local seed script assign category from the S3 key
-- prefix (e.g. s3://bucket/compliance/kyc.md → 'compliance'). The list_files
-- tool in the agent filters by this column.
--
-- Additive, no data backfill required: existing rows get the default 'general'.

ALTER TABLE public.vector_chunks
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'general';

CREATE INDEX IF NOT EXISTS idx_vector_chunks_category
    ON public.vector_chunks(tenant_id, category);
