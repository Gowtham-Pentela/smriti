"""
backend/parser.py
─────────────────
Document parsing pipeline for Knowledge Guardian.

Supports:
  - PDF: text extraction + embedded image extraction (OCR + vision LLM)
  - Markdown / text: YAML front-matter permissions + chunking
  - Code files:  function-block-aware chunking
  - Images (standalone): vision description via Moondream / LLaVA

YAML front-matter permissions (Markdown documents):
  Authors can declare access controls inline in any .md file:

    ---
    kgf_permissions:
      visibility: team        # public | team | restricted
      groups: [engineering, devops]
      users: []
    ---

  If no front-matter is present, chunks default to is_public=True.

Scanned PDF detection:
  If a PDF page yields < 100 characters of text but the file is > 100KB,
  a warning is returned as a chunk so the user knows the document was
  partially ingested (not silently dropped).
"""

import os
import io
import base64
import requests
import re
import warnings
from typing import Optional

try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ─── PII / Secret Scrubber ────────────────────────────────────────────────────

def sanitize_secrets_and_pii(text: str) -> str:
    # AWS Access Key / Secret
    text = re.sub(
        r'(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}',
        '[REDACTED_AWS_KEY]', text,
    )
    # Generic API Keys / Secrets / Passwords
    text = re.sub(
        r'(?i)(?:key|secret|token|password|auth)\s*[:=]\s*[\'"][a-zA-Z0-9_\-]{16,}[\'"]',
        lambda m: m.group(0).split(':')[0].split('=')[0] + ': "[REDACTED_SECRET]"',
        text,
    )
    # Email addresses (outside of YAML front-matter — already captured at index time)
    text = re.sub(
        r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+',
        '[REDACTED_EMAIL]', text,
    )
    return text


# ─── YAML Front-matter Parser ─────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Extract YAML front-matter from Markdown text.

    Returns:
      (permissions_dict, text_without_frontmatter)

    permissions_dict keys:
      visibility: "public" | "team" | "restricted"
      groups:     list[str]
      users:      list[str]

    Example .md header:
      ---
      kgf_permissions:
        visibility: team
        groups: [engineering, devops]
        users: []
      ---

    If no front-matter is present → returns ({}, original_text).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_str    = match.group(1)
    body_text   = text[match.end():]
    permissions = {}

    if _HAS_YAML:
        try:
            meta = yaml.safe_load(yaml_str) or {}
            kgf  = meta.get("kgf_permissions", {})
            if kgf:
                permissions = {
                    "visibility": str(kgf.get("visibility", "public")).lower(),
                    "groups":     list(kgf.get("groups", [])),
                    "users":      list(kgf.get("users", [])),
                }
        except yaml.YAMLError as e:
            print(f"  ⚠ YAML front-matter parse error: {e}")
    else:
        print("  ⚠ PyYAML not installed — front-matter permissions ignored.")

    return permissions, body_text


# ─── Chunkers ─────────────────────────────────────────────────────────────────

def chunk_code(text: str, filename: str) -> list[dict]:
    lines = text.splitlines()
    chunks, current_chunk, chunk_idx, line_count = [], [], 1, 0
    block_start = re.compile(
        r'^\s*(class\s+|def\s+|async\s+def\s+|function\s+|func\s+|struct\s+|interface\s+)'
    )

    for line in lines:
        if block_start.match(line) and line_count >= 30:
            content = "\n".join(current_chunk)
            if content.strip():
                chunks.append({
                    "source": filename, "type": "code",
                    "location": f"Block {chunk_idx}",
                    "content": sanitize_secrets_and_pii(content),
                })
                chunk_idx += 1
            current_chunk, line_count = [], 0

        current_chunk.append(line)
        line_count += 1

        if line_count >= 80:
            content = "\n".join(current_chunk)
            if content.strip():
                chunks.append({
                    "source": filename, "type": "code",
                    "location": f"Block {chunk_idx}",
                    "content": sanitize_secrets_and_pii(content),
                })
                chunk_idx += 1
            current_chunk, line_count = [], 0

    if current_chunk:
        content = "\n".join(current_chunk)
        if content.strip():
            chunks.append({
                "source": filename, "type": "code",
                "location": f"Block {chunk_idx}",
                "content": sanitize_secrets_and_pii(content),
            })
    return chunks


def chunk_text(text: str, chunk_size: int = 2500, overlap: int = 400) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start : start + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


# ─── PDF Parser ───────────────────────────────────────────────────────────────

def parse_pdf(file_path: str, source_name: Optional[str] = None) -> list[dict]:
    """
    Full PDF parsing pipeline:
      1. Text extraction (pypdf) per page
      2. Scanned-page detection — warns user if page appears image-only
      3. Image extraction + OCR/vision processing (requires pymupdf + optional deps)

    Returns all chunks including text, OCR, and vision-described image chunks.
    """
    if not _HAS_PYPDF:
        print(f"  ⚠ pypdf not installed — PDF parsing skipped for {file_path}")
        return []

    chunks   = []
    filename = source_name or os.path.basename(file_path)
    file_size_kb = os.path.getsize(file_path) / 1024
    scanned_pages = []

    # ── Step 1: Text extraction ────────────────────────────────────────────────
    try:
        reader = PdfReader(file_path)
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text() or ""

            # Scanned-page detection: few chars but file is large → likely image-only
            if len(text.strip()) < 100 and file_size_kb > 100:
                scanned_pages.append(page_num + 1)
                continue

            page_chunks = chunk_text(text)
            for chunk_text_content in page_chunks:
                chunks.append({
                    "source":        filename,
                    "type":          "document",
                    "location":      f"Page {page_num + 1}",
                    "content":       sanitize_secrets_and_pii(chunk_text_content),
                    "content_type":  "text",
                    "original_page": page_num + 1,
                })
    except Exception as e:
        print(f"  ✗ Error parsing PDF text in {file_path}: {e}")

    # ── Step 2: Image extraction + processing ─────────────────────────────
    try:
        from backend.image_extractor import extract_images_from_pdf, render_scanned_pages
        from backend.vision_processor import process_image

        images = extract_images_from_pdf(file_path)
        if images:
            print(f"  → Extracted {len(images)} image(s) from {filename}. Processing...")

        # Track which scanned pages already got covered by an embedded image
        pages_with_embedded = {img["page"] for img in images}

        for img_data in images:
            img_chunks = process_image(
                image_bytes=img_data["image_bytes"],
                page_num=img_data["page"],
                img_idx=img_data["index"],
                source_filename=filename,
            )
            chunks.extend(img_chunks)

        # For scanned pages that had NO embedded images, render the page itself
        # (scanned PDFs store the whole page as a content stream, not an XObject)
        uncovered = [p - 1 for p in scanned_pages if (p - 1) not in pages_with_embedded]
        if uncovered:
            print(f"  → Rendering {len(uncovered)} scanned page(s) via PyMuPDF for OCR...")
            rendered = render_scanned_pages(file_path, uncovered, dpi=200)
            for img_data in rendered:
                img_chunks = process_image(
                    image_bytes=img_data["image_bytes"],
                    page_num=img_data["page"],
                    img_idx=0,
                    source_filename=filename,
                )
                if img_chunks:
                    chunks.extend(img_chunks)
                else:
                    # OCR found nothing useful — emit a brief notice (not a blocking warning)
                    print(f"  ℹ Page {img_data['page'] + 1} of '{filename}': OCR returned no text (blank or handwritten?).")

    except ImportError:
        if scanned_pages:
            # Surface a warning chunk so users know why these pages aren't queryable
            pages_str = ", ".join(str(p) for p in scanned_pages[:10])
            chunks.append({
                "source":       filename,
                "type":         "document",
                "location":     f"Pages {pages_str}",
                "content":      (
                    f"⚠ WARNING: Pages {pages_str} of '{filename}' appear to be "
                    f"scanned images with no extractable text. "
                    f"Install pymupdf + pytesseract for OCR: "
                    f"pip install pymupdf pytesseract"
                ),
                "content_type": "warning",
                "original_page": scanned_pages[0],
            })
    except RuntimeError as e:
        # Encrypted PDF — surface clear message
        chunks.append({
            "source":       filename,
            "type":         "document",
            "location":     "All pages",
            "content":      f"⚠ Cannot index '{filename}': {e}",
            "content_type": "error",
            "original_page": 0,
        })
    except Exception as e:
        print(f"  ⚠ Image extraction error for {filename}: {e}")

    # Inform about scanned pages going through vision/OCR pipeline
    if scanned_pages:
        pages_str = ", ".join(str(p) for p in scanned_pages[:10])
        print(
            f"  ℹ '{filename}': pages {pages_str} appear scanned "
            f"— processed through vision pipeline."
        )

    return chunks


# ─── Text / Markdown Parser ───────────────────────────────────────────────────

def parse_text_file(
    file_path: str,
    source_name: Optional[str] = None,
) -> list[dict]:
    """
    Parse text / Markdown files. Extracts YAML front-matter for permission
    metadata, then chunks remaining body content.
    """
    chunks   = []
    filename = source_name or os.path.basename(file_path)
    ext      = os.path.splitext(file_path)[1].lower()

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception as e:
        print(f"  ✗ Error reading {file_path}: {e}")
        return []

    # Parse YAML front-matter for Markdown documents
    permissions = {}
    if ext in (".md", ".markdown"):
        permissions, text = _parse_frontmatter(text)

    text_chunks = chunk_text(text)
    for idx, chunk_text_content in enumerate(text_chunks):
        chunk: dict = {
            "source":        filename,
            "type":          "document",
            "location":      f"Section {idx + 1}",
            "content":       sanitize_secrets_and_pii(chunk_text_content),
            "content_type":  "text",
        }
        # Attach permissions if declared in front-matter
        if permissions:
            chunk["permissions"] = permissions
        chunks.append(chunk)

    return chunks


# ─── Image Parser (standalone image files) ───────────────────────────────────

def parse_image_via_vision(
    file_path: str,
    source_name: Optional[str] = None,
) -> list[dict]:
    """
    Parse a standalone image file (PNG, JPG) via the vision pipeline.
    Falls back to Moondream (legacy) if vision_processor is unavailable.
    """
    filename = source_name or os.path.basename(file_path)

    try:
        with open(file_path, "rb") as f:
            image_bytes = f.read()

        try:
            from backend.vision_processor import process_image
            return process_image(
                image_bytes=image_bytes,
                page_num=0,
                img_idx=0,
                source_filename=filename,
            )
        except ImportError:
            pass

        # Legacy fallback — Moondream via raw Ollama call
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        ollama_url = os.getenv("OLLAMA_GEN_URL", "http://localhost:11434/api/generate")
        response = requests.post(
            ollama_url,
            json={
                "model":  "moondream",
                "prompt": (
                    "Describe this image in detail, listing any visible text, "
                    "code blocks, UI elements, flowcharts, or system components."
                ),
                "images": [img_b64],
                "stream": False,
            },
            timeout=60,
        )
        if response.status_code == 200:
            description = response.json().get("response", "").strip()
            return [{
                "source":   filename,
                "type":     "image",
                "location": "Image Analysis",
                "content":  f"Visual Content Description of {filename}: {description}",
            }]
        return []

    except Exception as e:
        print(f"  ✗ Error describing image {file_path}: {e}")
        return []


# ─── Main Dispatch ────────────────────────────────────────────────────────────

def parse_document(
    file_path: str,
    source_name: Optional[str] = None,
) -> list[dict]:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return parse_pdf(file_path, source_name)

    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        return parse_image_via_vision(file_path, source_name)

    elif ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".java",
                 ".go", ".cpp", ".c", ".h", ".rs", ".sh"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            filename = source_name or os.path.basename(file_path)
            return chunk_code(text, filename)
        except Exception as e:
            print(f"  ✗ Error parsing code file {file_path}: {e}")
            return []

    elif ext in (".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".sql"):
        return parse_text_file(file_path, source_name)

    return []
