-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 005: Image metadata columns on vector_chunks
-- Enables rich citation display for OCR and vision-processed content.
-- ─────────────────────────────────────────────────────────────────────────────

-- content_type discriminates how a chunk was produced:
--   'text'          → extracted from PDF text layer or plain text file
--   'image_ocr'     → pytesseract OCR output from an embedded image
--   'image_vision'  → LLaVA / Moondream description of a diagram/flowchart
--   'image_hybrid'  → combined OCR (labels) + vision (structure) output
--   'warning'       → metadata chunk surfacing a processing failure to the user
--   'error'         → processing error (e.g. encrypted PDF)
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS content_type     TEXT NOT NULL DEFAULT 'text';

-- original_page: 1-indexed page number within the source PDF
-- NULL for non-PDF sources (Slack messages, plain text files, etc.)
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS original_page    INT;

-- image_index: which image on the page (0-indexed)
-- NULL for text chunks
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS image_index      INT;

-- processing_model: which model produced this chunk
-- Examples: 'text_extraction', 'tesseract', 'llava:7b', 'tesseract+llava:7b', 'moondream'
ALTER TABLE tenant_redwood_inference_prod.vector_chunks
    ADD COLUMN IF NOT EXISTS processing_model TEXT;

-- ── Index for citation UI queries ─────────────────────────────────────────────
-- The citation sidebar queries by source_id + content_type to show the right icon.
CREATE INDEX IF NOT EXISTS idx_chunks_content_type
    ON tenant_redwood_inference_prod.vector_chunks (tenant_id, content_type);

-- ── Backfill existing rows ────────────────────────────────────────────────────
UPDATE tenant_redwood_inference_prod.vector_chunks
SET
    content_type     = 'text',
    processing_model = 'text_extraction'
WHERE content_type IS NULL OR content_type = '';
