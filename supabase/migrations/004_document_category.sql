-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 004: Document category + permissions columns
-- Enables category filtering and permission-aware retrieval.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Add document_category column ──────────────────────────────────────────
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS document_category TEXT NOT NULL DEFAULT 'general';

-- ── 2. Add document_title for better citation display ─────────────────────────
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS document_title TEXT;

-- ── 3. Add permission_visibility for coarse-grained access control ─────────────
-- Values: 'public' | 'team' | 'restricted'
-- 'public':     All authenticated users can see this chunk.
-- 'team':       Users whose email_domain matches the tenant's domain.
-- 'restricted': Only users listed in allowed_users or in allowed_groups.
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS permission_visibility TEXT NOT NULL DEFAULT 'public';

-- ── 4. Indexes for category-filtered queries ──────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_chunks_category
    ON tenant_redwood_inference_prod.vector_chunks (tenant_id, document_category);

CREATE INDEX IF NOT EXISTS idx_chunks_visibility
    ON tenant_redwood_inference_prod.vector_chunks (tenant_id, permission_visibility);

-- ── 5. Update existing chunks to 'general' (already the DEFAULT) ──────────────
-- No-op if DEFAULT was applied correctly. Explicit for safety.
UPDATE tenant_redwood_inference_prod.vector_chunks
SET document_category = 'general'
WHERE document_category IS NULL;
