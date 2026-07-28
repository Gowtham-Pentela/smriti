-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 001: Single-company tenant registry
-- ─────────────────────────────────────────────────────────────────────────────
-- Smriti now runs as a single-tenant internal ChatGPT. There is exactly one
-- company tenant whose UUID is set in COMPANY_TENANT_ID at app startup.
-- We still keep the table so future expansion to multi-tenant is non-breaking.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector

CREATE TABLE IF NOT EXISTS public.tenant_registry (
    tenant_id   UUID        PRIMARY KEY,
    name        TEXT        NOT NULL DEFAULT 'Internal',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed: insert a placeholder tenant. The app will upsert with the real
-- COMPANY_TENANT_ID on first boot.
INSERT INTO public.tenant_registry (tenant_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Internal')
ON CONFLICT (tenant_id) DO NOTHING;
