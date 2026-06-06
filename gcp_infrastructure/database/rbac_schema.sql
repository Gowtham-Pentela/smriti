-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Table to store document chunks and embeddings
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    source_file VARCHAR(255) NOT NULL,
    chunk_type VARCHAR(50) NOT NULL, -- 'document', 'code', 'video', 'image'
    location VARCHAR(100) NOT NULL,   -- 'Page 3', 'Timestamp 02:15', 'Image Analysis'
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,   -- Maps to nomic-embed-text size
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for L2 similarity search (cosine distance)
CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);

-- Table mapping files/codebases to authorized user emails or AD security groups
CREATE TABLE IF NOT EXISTS document_permissions (
    id SERIAL PRIMARY KEY,
    source_file VARCHAR(255) NOT NULL,
    allowed_identity VARCHAR(255) NOT NULL -- User email or security group (e.g. 'engineering@company.com')
);

CREATE UNIQUE INDEX idx_file_identity ON document_permissions(source_file, allowed_identity);

-- Enable Row-Level Security (RLS)
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- Create policy to restrict queries to files the user has permission to access.
-- The app queries database by setting a config parameter, e.g.:
--   SET LOCAL app.current_user_email = 'gowtham@company.com';
--   SET LOCAL app.current_user_groups = 'engineering@company.com,admins@company.com';
CREATE POLICY rbac_document_access_policy ON documents
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 
            FROM document_permissions p
            WHERE p.source_file = documents.source_file
              AND (
                  p.allowed_identity = current_setting('app.current_user_email', true)
                  OR p.allowed_identity = ANY(string_to_array(current_setting('app.current_user_groups', true), ','))
              )
        )
    );
