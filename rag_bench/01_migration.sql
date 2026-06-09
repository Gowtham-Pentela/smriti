-- =============================================================================
-- Migration: tenant_redwood_inference_prod schema
-- Engine: PostgreSQL with pgvector extension
-- Run against: postgresql://postgres:postgres@127.0.0.1:54322/postgres
-- =============================================================================

-- Ensure pgvector extension is active
CREATE EXTENSION IF NOT EXISTS vector;

-- Drop and recreate schema for clean run
DROP SCHEMA IF EXISTS tenant_redwood_inference_prod CASCADE;
CREATE SCHEMA tenant_redwood_inference_prod;

-- =============================================================================
-- Table 1: vector_chunks
-- Holds processed communication fragments with embeddings
-- =============================================================================
CREATE TABLE tenant_redwood_inference_prod.vector_chunks (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id         TEXT NOT NULL,
    thread_id         TEXT,
    source_type       TEXT NOT NULL,
    author_id         TEXT,
    channel_or_space  TEXT,
    content           TEXT NOT NULL,
    embedding         vector(768),
    allowed_groups    TEXT[]  DEFAULT '{}',
    allowed_users     TEXT[]  DEFAULT '{}',
    is_public         BOOLEAN DEFAULT TRUE,
    ingested_at       TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- Table 2: graph_nodes
-- Represents people and topics in the organizational knowledge graph
-- =============================================================================
CREATE TABLE tenant_redwood_inference_prod.graph_nodes (
    node_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type          TEXT NOT NULL CHECK (node_type IN ('person', 'topic')),
    external_source_id TEXT UNIQUE NOT NULL,
    display_name       TEXT,
    metadata           JSONB DEFAULT '{}'
);

-- =============================================================================
-- Table 3: graph_edges
-- Directed interaction links between nodes with weighted strengths
-- =============================================================================
CREATE TABLE tenant_redwood_inference_prod.graph_edges (
    source_id       UUID NOT NULL REFERENCES tenant_redwood_inference_prod.graph_nodes(node_id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES tenant_redwood_inference_prod.graph_nodes(node_id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL DEFAULT 'interaction',
    weight          DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    last_updated    TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_id, target_id)
);

-- =============================================================================
-- Indexes: vector search + relational lookups
-- =============================================================================

-- HNSW index on embedding column for cosine similarity (best for search)
CREATE INDEX idx_vector_chunks_embedding_hnsw
    ON tenant_redwood_inference_prod.vector_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree indexes for relational joins
CREATE INDEX idx_vector_chunks_thread_id
    ON tenant_redwood_inference_prod.vector_chunks (thread_id);

CREATE INDEX idx_vector_chunks_source_id
    ON tenant_redwood_inference_prod.vector_chunks (source_id);

CREATE INDEX idx_graph_edges_source_id
    ON tenant_redwood_inference_prod.graph_edges (source_id);

CREATE INDEX idx_graph_edges_target_id
    ON tenant_redwood_inference_prod.graph_edges (target_id);

-- Full-text search index on content for hybrid retrieval
CREATE INDEX idx_vector_chunks_content_fts
    ON tenant_redwood_inference_prod.vector_chunks
    USING gin(to_tsvector('english', content));

-- =============================================================================
-- Row-Level Security (RLS)
-- Verifies tenant_id context before allowing reads
-- =============================================================================
ALTER TABLE tenant_redwood_inference_prod.vector_chunks ENABLE ROW LEVEL SECURITY;

-- Policy: allow read only when session variable matches this tenant
CREATE POLICY tenant_isolation_policy
    ON tenant_redwood_inference_prod.vector_chunks
    FOR SELECT
    USING (
        current_setting('app.current_tenant_id', true)
            = '1b87e7de-de9c-5f96-87d6-b163402ddd4c'
    );

-- Superuser bypass so ingestion scripts can write without setting the var
CREATE POLICY superuser_write_policy
    ON tenant_redwood_inference_prod.vector_chunks
    FOR ALL
    TO postgres
    USING (true)
    WITH CHECK (true);

-- =============================================================================
-- Verification
-- =============================================================================
SELECT
    schemaname,
    tablename,
    rowsecurity
FROM pg_tables
WHERE schemaname = 'tenant_redwood_inference_prod'
ORDER BY tablename;
