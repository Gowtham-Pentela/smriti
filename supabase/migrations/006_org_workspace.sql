-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 006: Org-level workspace isolation and team invitation tables
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. User organization membership table ────────────────────────────────────
-- Associates users (by Supabase user UUID) with a specific tenant organization.
CREATE TABLE IF NOT EXISTS public.user_org_membership (
    user_id          UUID        PRIMARY KEY, -- Matches Supabase user UUID / dev token UUID
    tenant_id        UUID        NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    role             TEXT        NOT NULL CHECK (role IN ('admin', 'member')),
    email            TEXT        NOT NULL,
    joined_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_org_membership_tenant_id
    ON public.user_org_membership(tenant_id);

CREATE INDEX IF NOT EXISTS idx_user_org_membership_email
    ON public.user_org_membership(email);

-- ── 2. Team invitations table ────────────────────────────────────────────────
-- Tracks pending and accepted organization invites sent via email.
CREATE TABLE IF NOT EXISTS public.org_invites (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    invited_email    TEXT        NOT NULL,
    role             TEXT        NOT NULL CHECK (role IN ('admin', 'member')),
    invited_by       UUID        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at      TIMESTAMPTZ NULL,

    -- Avoid duplicate active invites to the same person for a given organization
    CONSTRAINT uq_org_invites_tenant_email UNIQUE (tenant_id, invited_email)
);

CREATE INDEX IF NOT EXISTS idx_org_invites_invited_email
    ON public.org_invites(invited_email);
