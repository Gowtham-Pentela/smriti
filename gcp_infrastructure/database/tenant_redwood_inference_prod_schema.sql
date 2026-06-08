-- ============================================================================
-- Logical Schema: tenant_redwood_inference_prod
-- Simulation: Single-tenant environment from EnterpriseRAG-Bench corpus
-- Author: Principal Database Architect
-- ============================================================================

-- Enable pgvector and uuid-ossp extensions at the database level if not active
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create the dedicated schema for isolation
CREATE SCHEMA IF NOT EXISTS tenant_redwood_inference_prod;

-- ----------------------------------------------------------------------------
-- Table 1: graph_nodes
-- Holds entity structures (person or topic) representing knowledge nodes.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.graph_nodes (
    node_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type TEXT NOT NULL CHECK (node_type IN ('person', 'topic')),
    external_source_id TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Comment explaining indexing and schema design
COMMENT ON TABLE tenant_redwood_inference_prod.graph_nodes IS 
'Stores graph node representations of entities (people/topics) parsed from communications.';

-- ----------------------------------------------------------------------------
-- Table 2: graph_edges
-- Maps semantic relationship links and network structures between graph_nodes.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.graph_edges (
    source_id UUID NOT NULL REFERENCES tenant_redwood_inference_prod.graph_nodes (node_id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES tenant_redwood_inference_prod.graph_nodes (node_id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_graph_edges PRIMARY KEY (source_id, target_id)
);

COMMENT ON TABLE tenant_redwood_inference_prod.graph_edges IS 
'Stores directional, weighted relational links between graph_nodes.';

-- ----------------------------------------------------------------------------
-- Table 3: vector_chunks
-- Holds raw textual communication fragments, allowed groups/users for access 
-- control, and dense embeddings for similarity searches.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.vector_chunks (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source_id TEXT NOT NULL,
    thread_id TEXT NULL,
    source_type TEXT NOT NULL,
    author_id TEXT NOT NULL,
    channel_or_space TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL, -- Fixed 768-dim vector space for nomic-embed-text
    allowed_groups TEXT[] NOT NULL DEFAULT '{}',
    allowed_users TEXT[] NOT NULL DEFAULT '{}',
    is_public BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE tenant_redwood_inference_prod.vector_chunks IS 
'Stores communication chunks alongside their corresponding 768-dim nomic-embed-text vectors and access lists.';

-- ----------------------------------------------------------------------------
-- Optimization: Indexing & Vector Search Acceleration
-- ----------------------------------------------------------------------------

-- Vector Index: HNSW (Hierarchical Navigable Small World) for cosine distance
-- HNSW is preferred over IVFFlat for high accuracy recall and latency speed.
CREATE INDEX IF NOT EXISTS idx_vector_chunks_embedding_hnsw
ON tenant_redwood_inference_prod.vector_chunks
USING hnsw (embedding vector_cosine_ops);

-- B-Tree Index on foreign keys & search columns of graph_edges
CREATE INDEX IF NOT EXISTS idx_graph_edges_source_id 
ON tenant_redwood_inference_prod.graph_edges (source_id);

CREATE INDEX IF NOT EXISTS idx_graph_edges_target_id 
ON tenant_redwood_inference_prod.graph_edges (target_id);

-- B-Tree Index on vector_chunks search parameters
CREATE INDEX IF NOT EXISTS idx_vector_chunks_thread_id 
ON tenant_redwood_inference_prod.vector_chunks (thread_id);

-- Performance Index to accelerate tenant lookup matching under RLS
CREATE INDEX IF NOT EXISTS idx_vector_chunks_tenant_id 
ON tenant_redwood_inference_prod.vector_chunks (tenant_id);

-- ----------------------------------------------------------------------------
-- Security: Row-Level Security (RLS) Configuration
-- ----------------------------------------------------------------------------

-- Enable RLS on the vector_chunks table
ALTER TABLE tenant_redwood_inference_prod.vector_chunks ENABLE ROW LEVEL SECURITY;

-- Policy verifying tenant_id context constraints before reading data blocks.
-- The application tenant context is verified via session settings:
--   SET LOCAL app.current_tenant_id = 'c1866fd7-68a3-44a8-8f3c-240cf13b741b';
CREATE POLICY tenant_isolation_policy ON tenant_redwood_inference_prod.vector_chunks
    FOR SELECT
    USING (
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
    );
