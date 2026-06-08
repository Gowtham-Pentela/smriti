-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 002: Sync log table
-- Records every sync cycle per tenant+source for observability.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_redwood_inference_prod.sync_log (
    id           BIGSERIAL   PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    source       TEXT        NOT NULL,   -- 'slack' | 'gmail' | 'drive' | ...
    synced_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    chunks_added INT         NOT NULL DEFAULT 0,
    error_msg    TEXT        -- NULL = success
);

CREATE INDEX IF NOT EXISTS idx_sync_log_tenant_source
    ON tenant_redwood_inference_prod.sync_log (tenant_id, source, synced_at DESC);

-- Helper view: last successful sync per tenant+source
CREATE OR REPLACE VIEW tenant_redwood_inference_prod.sync_health AS
SELECT
    tenant_id,
    source,
    MAX(synced_at) FILTER (WHERE error_msg IS NULL) AS last_success_at,
    SUM(chunks_added) FILTER (WHERE error_msg IS NULL) AS total_chunks_added,
    COUNT(*) FILTER (WHERE error_msg IS NOT NULL) AS error_count
FROM tenant_redwood_inference_prod.sync_log
GROUP BY tenant_id, source;
