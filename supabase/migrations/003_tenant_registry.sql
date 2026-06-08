-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 003: Tenant registry
-- Auto-provisioned on first Google SSO login from a new email domain.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_registry (
    tenant_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email_domain     TEXT        NOT NULL UNIQUE,   -- "acme.com"
    company_name     TEXT,                           -- "Acme Corp" (editable)
    provisioned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    -- Metadata for the future billing layer
    plan             TEXT        NOT NULL DEFAULT 'trial',   -- trial | starter | growth | enterprise
    trial_ends_at    TIMESTAMPTZ          DEFAULT (NOW() + INTERVAL '14 days')
);

CREATE INDEX IF NOT EXISTS idx_tenant_registry_domain
    ON tenant_registry (email_domain);

-- Note: tenant_registry is in the public schema (not the tenant schema)
-- because it must be readable before the tenant context is established.
-- No RLS here — the application layer gates access by authenticated user domain.
