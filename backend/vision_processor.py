"""
backend/vision_processor.py
───────────────────────────
Routes extracted PDF images to the right processing pipeline:
  1. Text-dense images (scanned pages, tables, code screenshots) → pytesseract OCR
  2. Diagram-heavy images (architecture, flowcharts, network maps) → Moondream 1.8B via Ollama

Both paths output text chunks that flow directly into the existing chunking and
embedding pipeline — nomic-embed-text does not know or care whether a chunk came
from PDF text, OCR, or a vision description.

Model stack (local only — no external API calls):
  - OCR primary:   pytesseract (Tesseract 5)
  - OCR fallback:  EasyOCR (better for stylized / non-Latin text)
  - Vision:        Moondream 1.8B via Ollama (already installed, fits in 8 GB RAM)
                   To upgrade later on a machine with 16+ GB: set SMRITI_VISION_MODEL=llava:7b

Routing heuristic:
  - pytesseract quick scan → "text density" = (text char area / image area)
  - density > 0.40 AND confidence > 60  → OCR path
  - density 0.20–0.40                    → both paths, combined output
  - density < 0.20                       → vision LLM path

PII guard:
  - OCR output → passes through parser.sanitize_secrets_and_pii()
  - Vision prompt → explicit "do not describe or name any people" instruction
"""

import base64
import io
import os
import re
import requests
from typing import Optional

try:
    import pytesseract
    from PIL import Image as PILImage
    _HAS_TESSERACT = True
except ImportError:
    _HAS_TESSERACT = False

try:
    import easyocr
    _HAS_EASYOCR = True
except ImportError:
    _HAS_EASYOCR = False

from backend.parser import sanitize_secrets_and_pii

OLLAMA_GEN_URL    = os.getenv("OLLAMA_GEN_URL", "http://localhost:11434/api/generate")
# Default to Moondream (1.8B, 1.7 GB) — fits in 8 GB RAM alongside nomic-embed-text.
# On machines with 16+ GB, override with: SMRITI_VISION_MODEL=llava:7b
VISION_MODEL      = os.getenv("SMRITI_VISION_MODEL", "moondream")
VISION_FALLBACK   = "moondream"  # same model; fallback path is a no-op safety net

# Minimum meaningful words in a vision description before we discard the chunk.
# Decorative patterns / watermarks produce <10 meaningful words.
MIN_VISION_WORDS  = 20

# Routing thresholds
HIGH_TEXT_DENSITY = 0.40   # → OCR path
LOW_TEXT_DENSITY  = 0.20   # → vision path; between = both

_easyocr_reader = None  # Lazy-loaded to avoid startup delay


def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None and _HAS_EASYOCR:
        import easyocr
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easyocr_reader


def process_image(
    image_bytes: bytes,
    page_num: int,
    img_idx: int,
    source_filename: str,
) -> list[dict]:
    """
    Route a single image to OCR, vision LLM, or both.

    Returns a list of chunk dicts (may be empty for non-informative images):
    {
        "source":            str,   # filename
        "type":              str,   # "document"
        "location":          str,   # "Page N, Image M"
        "content":           str,   # extracted / described text
        "content_type":      str,   # "image_ocr" | "image_vision" | "image_hybrid"
        "original_page":     int,
        "image_index":       int,
        "processing_model":  str,
    }
    """
    location = f"Page {page_num + 1}, Image {img_idx + 1}"
    chunks   = []

    # ── Step 1: Quick text-density estimation via tesseract ────────────────────
    density, confidence = _estimate_text_density(image_bytes)

    if density >= HIGH_TEXT_DENSITY and confidence >= 60:
        # OCR path
        text = _ocr_extract(image_bytes)
        if text:
            chunks.append(_make_chunk(
                source_filename, location, text,
                "image_ocr", page_num, img_idx, "tesseract",
            ))

    elif density >= LOW_TEXT_DENSITY:
        # Hybrid path — run both and combine
        ocr_text    = _ocr_extract(image_bytes)
        vision_text = _vision_describe(image_bytes)

        combined = _combine(ocr_text, vision_text)
        if combined:
            chunks.append(_make_chunk(
                source_filename, location, combined,
                "image_hybrid", page_num, img_idx,
                f"tesseract+{VISION_MODEL}",
            ))

    else:
        # Vision LLM path
        vision_text = _vision_describe(image_bytes)
        if vision_text:
            chunks.append(_make_chunk(
                source_filename, location, vision_text,
                "image_vision", page_num, img_idx, VISION_MODEL,
            ))

    # ponytail: if every pipeline produced nothing, still index a marker chunk
    # so the upload doesn't 422 with a vague "no text" error. The user sees
    # the image was received but the vision/OCR stack couldn't read it.
    if not chunks:
        chunks.append(_make_chunk(
            source_filename, location,
            f"[{source_filename}]: no text could be extracted — neither OCR nor "
            f"the vision model ({VISION_MODEL}) returned content for this image. "
            f"The image was indexed as a placeholder.",
            "image_empty", page_num, img_idx, "none",
        ))

    return chunks


# ─── OCR Path ─────────────────────────────────────────────────────────────────

def _estimate_text_density(image_bytes: bytes) -> tuple[float, float]:
    """
    Quick Tesseract pass to estimate text coverage and confidence.
    Returns (density_ratio 0-1, avg_confidence 0-100).
    """
    if not _HAS_TESSERACT:
        return 0.0, 0.0
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        total_chars = sum(len(w) for w in data["text"] if w.strip())
        confidences = [c for c, w in zip(data["conf"], data["text"])
                       if w.strip() and c != -1]
        if not confidences:
            return 0.0, 0.0

        img_area   = img.width * img.height
        # Rough approximation: each character ≈ 10×15 pixels
        text_area  = total_chars * 150
        density    = min(1.0, text_area / max(img_area, 1))
        avg_conf   = sum(confidences) / len(confidences)
        return density, avg_conf
    except Exception:
        return 0.0, 0.0


def _ocr_extract(image_bytes: bytes) -> str:
    """
    Full OCR extraction using pytesseract (primary) and EasyOCR (fallback).
    Cleans output: removes single chars, strips artifact lines < 10 chars.
    Also runs PII scrubber.
    """
    text = ""

    # Primary: pytesseract
    if _HAS_TESSERACT:
        try:
            img  = PILImage.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(img, config="--psm 6")
        except Exception as e:
            print(f"  ⚠ Tesseract OCR failed: {e}")

    # Fallback: EasyOCR (better for stylized / mixed-language)
    if not text.strip() and _HAS_EASYOCR:
        try:
            reader = _get_easyocr_reader()
            if reader:
                results = reader.readtext(image_bytes, detail=0)
                text    = " ".join(results)
        except Exception as e:
            print(f"  ⚠ EasyOCR fallback failed: {e}")

    # Clean output
    lines = text.splitlines()
    clean = []
    for line in lines:
        line = line.strip()
        # Remove lines that are just noise (single chars, very short artifacts)
        if len(line) < 10 or len(line.split()) < 2:
            continue
        clean.append(line)

    cleaned = "\n".join(clean).strip()
    if not cleaned:
        return ""

    return sanitize_secrets_and_pii(cleaned)


# ─── Vision LLM Path ──────────────────────────────────────────────────────────

_VISION_PROMPT = (
    "This image is from a technical document. "
    "Describe every component, label, relationship, data flow, and process shown. "
    "Include all text visible in the image. "
    "Be specific and complete. "
    "Do not describe or name any people visible in this image. "
    "Your description will be used to answer technical questions about this document."
)


def _vision_describe(image_bytes: bytes, use_fallback: bool = False) -> str:
    """
    Send image to LLaVA 7B (or Moondream fallback) via local Ollama.
    Returns descriptive text, or empty string on failure.
    """
    model = VISION_FALLBACK if use_fallback else VISION_MODEL
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        response = requests.post(
            OLLAMA_GEN_URL,
            json={
                "model":  model,
                "prompt": _VISION_PROMPT,
                "images": [img_b64],
                "stream": False,
            },
            timeout=90,
        )
        if response.status_code != 200:
            if not use_fallback:
                print(f"  ⚠ {model} returned {response.status_code}, trying {VISION_FALLBACK}...")
                return _vision_describe(image_bytes, use_fallback=True)
            return ""

        description = response.json().get("response", "").strip()

        # ── Relevance gate: discard decorative / low-value descriptions ────────
        # Watermarks, decorative patterns, background textures produce very few
        # meaningful words. Don't pollute the vector store with them.
        meaningful_words = [w for w in description.split() if len(w) > 3]
        if len(meaningful_words) < MIN_VISION_WORDS:
            return ""

        return sanitize_secrets_and_pii(description)

    except requests.exceptions.ConnectionError:
        print(f"  ⚠ Ollama not running — vision processing skipped. Run: ollama serve")
        return ""
    except Exception as e:
        print(f"  ⚠ Vision LLM ({model}) failed: {e}")
        if not use_fallback:
            return _vision_describe(image_bytes, use_fallback=True)
        return ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _combine(ocr: str, vision: str) -> str:
    """Merge OCR and vision outputs into a single chunk for hybrid images."""
    parts = []
    if ocr:
        parts.append(f"[Text content]\n{ocr}")
    if vision:
        parts.append(f"[Visual content]\n{vision}")
    return "\n\n".join(parts)


def _make_chunk(
    source: str,
    location: str,
    content: str,
    content_type: str,
    page_num: int,
    img_idx: int,
    model: str,
) -> dict:
    return {
        "source":           source,
        "type":             "document",
        "location":         location,
        "content":          content,
        "content_type":     content_type,   # for citation display
        "original_page":    page_num + 1,   # 1-indexed for UI
        "image_index":      img_idx,
        "processing_model": model,
    }
