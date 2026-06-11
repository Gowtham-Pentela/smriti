-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 007: Sutra Tables for Meetings, Decisions, and Relations
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Meetings Table
CREATE TABLE IF NOT EXISTS public.meetings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    scheduled_start TIMESTAMP WITH TIME ZONE NOT NULL,
    attendees TEXT[] NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled, active, completed, failed
    meeting_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_tenant_id ON public.meetings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON public.meetings(status);

-- 2. Structured Decision Nodes (Entity & Fact Extraction)
CREATE TABLE IF NOT EXISTS public.decision_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES public.meetings(id) ON DELETE SET NULL,
    entity_name TEXT NOT NULL,          -- e.g. "/v1/auth", "billing_migration"
    action_type TEXT NOT NULL,          -- e.g. "deprecate", "integrate", "refactor"
    summary TEXT NOT NULL,              -- The actual decision / rule
    owner_email TEXT,                   -- Responsible party
    target_date DATE,                   -- Target deadline
    embedding vector(768),              -- Semantic vector of the summary
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_nodes_tenant_id ON public.decision_nodes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_decision_nodes_meeting_id ON public.decision_nodes(meeting_id);
CREATE INDEX IF NOT EXISTS idx_decision_nodes_embedding_hnsw
ON public.decision_nodes
USING hnsw (embedding vector_cosine_ops);

-- 3. Decision Relations (The Semantic Graph edges)
CREATE TABLE IF NOT EXISTS public.decision_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.tenant_registry(tenant_id) ON DELETE CASCADE,
    node_id_a UUID NOT NULL REFERENCES public.decision_nodes(id) ON DELETE CASCADE,
    node_id_b UUID NOT NULL REFERENCES public.decision_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,        -- 'contradicts', 'depends_on', 'supersedes'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT unique_relation UNIQUE (node_id_a, node_id_b, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_decision_relations_tenant_id ON public.decision_relations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_decision_relations_node_a ON public.decision_relations(node_id_a);
CREATE INDEX IF NOT EXISTS idx_decision_relations_node_b ON public.decision_relations(node_id_b);

-- 4. Enable Row-Level Security (RLS)
ALTER TABLE public.meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_relations ENABLE ROW LEVEL SECURITY;

-- 5. Define RLS Policies
CREATE POLICY tenant_isolation_meetings ON public.meetings
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

CREATE POLICY tenant_isolation_decision_nodes ON public.decision_nodes
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

CREATE POLICY tenant_isolation_decision_relations ON public.decision_relations
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
