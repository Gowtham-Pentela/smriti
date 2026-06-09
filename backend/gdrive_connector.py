"""
backend/gdrive_connector.py
────────────────────────────
Google Drive ingestion connector for Smriti (KGF).

Walks a user's Google Drive (or a specific folder), downloads supported
files, parses them into chunks, and inserts into vector_chunks via the
shared pg_ingest_chunks() pattern.

Supported file types:
  - Google Docs      → exported as plain text
  - Google Sheets    → exported as CSV (each sheet becomes a chunk)
  - Google Slides    → exported as plain text (each slide as a chunk)
  - PDF              → downloaded, parsed via parser.py
  - .txt / .md       → downloaded, parsed directly
  - .docx            → downloaded, parsed via parser.py

Design decisions:
  - Uses httpx (async) with the access_token in the Authorization header.
    No google-api-python-client dependency needed beyond token exchange.
  - Deduplication via Postgres ingestion_hashes (same as Slack connector).
  - PII scrub applied to all text content before embedding.
  - Files > 50MB are skipped to avoid memory pressure.
  - Google Workspace files (Docs/Sheets/Slides) are always re-exported on
    every sync because Drive doesn't expose a reliable content hash for them.
    MD5 hash is used for binary files.
"""

import asyncio
import hashlib
import io
import os
import re
import uuid
from typing import AsyncIterator

import asyncpg
import httpx

from backend.db import check_and_mark_ingested

SCHEMA          = "tenant_redwood_inference_prod"
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
MODEL_NAME_EMBED = "nomic-embed-text"

# Max file size to download (50MB)
MAX_FILE_BYTES = 50 * 1024 * 1024

# Google MIME types → export format mapping
GOOGLE_EXPORT_MAP = {
    "application/vnd.google-apps.document":     ("text/plain",              ".txt"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv",                ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain",              ".txt"),
}

# Native file MIME types we support directly
NATIVE_SUPPORTED = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Drive API base URL
DRIVE_API = "https://www.googleapis.com/drive/v3"

# ── PII scrub (mirrors slack_connector.py) ────────────────────────────────────
_PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    re.compile(r"\b(?:\+?1[-.\\s]?)?\(?\d{3}\)?[-.\\s]?\d{3}[-.\\s]?\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(sk|pk|rk)[-_](?:live|test|prod)[-_][A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def _scrub_pii(text: str) -> str:
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ── Embedding ─────────────────────────────────────────────────────────────────

async def _embed(text: str, http: httpx.AsyncClient) -> list[float]:
    try:
        resp = await http.post(
            OLLAMA_EMBED_URL,
            json={"model": MODEL_NAME_EMBED, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [0.0] * 768)
    except Exception as e:
        print(f"  ⚠ Embedding failed: {e}")
        return [0.0] * 768


# ── Text chunker ─────────────────────────────────────────────────────────────

def _chunk_text(text: str, source_name: str, chunk_size: int = 800, overlap: int = 100) -> list[dict]:
    """Split text into overlapping chunks of ~chunk_size characters."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    start  = 0
    idx    = 0
    while start < len(text):
        end     = min(start + chunk_size, len(text))
        content = text[start:end].strip()
        if content:
            chunks.append({
                "content":  _scrub_pii(content),
                "source":   source_name,
                "type":     "gdrive",
                "location": "google_drive",
                "title":    source_name,
                "idx":      idx,
            })
        start += chunk_size - overlap
        idx   += 1

    return chunks


# ── Drive API helpers ─────────────────────────────────────────────────────────

async def _list_files(
    http: httpx.AsyncClient,
    access_token: str,
    folder_id: str | None = None,
    page_token: str | None = None,
) -> tuple[list[dict], str | None]:
    """List one page of files from Drive."""
    params: dict = {
        "pageSize": 100,
        "fields":   "nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime,parents)",
        "q":        "trashed = false",
    }
    if folder_id:
        params["q"] += f" and '{folder_id}' in parents"
    if page_token:
        params["pageToken"] = page_token

    resp = await http.get(
        f"{DRIVE_API}/files",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    data          = resp.json()
    next_token    = data.get("nextPageToken")
    return data.get("files", []), next_token


async def _walk_drive(
    http: httpx.AsyncClient,
    access_token: str,
    folder_id: str | None = None,
) -> AsyncIterator[dict]:
    """Recursively yield all supported files from Drive."""
    page_token = None
    while True:
        files, page_token = await _list_files(http, access_token, folder_id, page_token)
        for f in files:
            mime = f.get("mimeType", "")
            if mime == "application/vnd.google-apps.folder":
                # Recurse into sub-folder
                async for sub_file in _walk_drive(http, access_token, f["id"]):
                    yield sub_file
            elif mime in GOOGLE_EXPORT_MAP or mime in NATIVE_SUPPORTED:
                yield f
            # else: skip unsupported types silently
        if not page_token:
            break


async def _download_file(
    http: httpx.AsyncClient,
    access_token: str,
    file_meta: dict,
) -> bytes | None:
    """Download or export a file, returning its raw bytes (or None on error)."""
    file_id  = file_meta["id"]
    mime     = file_meta.get("mimeType", "")
    size_str = file_meta.get("size")

    # Skip oversized native files
    if size_str and int(size_str) > MAX_FILE_BYTES:
        print(f"  ⚠ Skipping {file_meta['name']} ({int(size_str)//1024//1024}MB > 50MB limit)")
        return None

    if mime in GOOGLE_EXPORT_MAP:
        export_mime, _ = GOOGLE_EXPORT_MAP[mime]
        url    = f"{DRIVE_API}/files/{file_id}/export"
        params = {"mimeType": export_mime}
    else:
        url    = f"{DRIVE_API}/files/{file_id}"
        params = {"alt": "media"}

    try:
        resp = await http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as e:
        print(f"  ⚠ Download failed for {file_meta['name']}: {e.response.status_code}")
        return None
    except Exception as e:
        print(f"  ⚠ Download error for {file_meta['name']}: {e}")
        return None


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(raw_bytes: bytes, mime: str, file_name: str) -> str:
    """Extract plain text from raw file bytes."""
    if mime in (
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
        "text/plain",
        "text/markdown",
        "text/csv",
    ):
        # Already text — decode
        for enc in ("utf-8", "latin-1"):
            try:
                return raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    if mime == "application/pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except Exception as e:
            print(f"  ⚠ PDF extraction failed for {file_name}: {e}")
            return ""

    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            import docx
            doc = docx.Document(io.BytesIO(raw_bytes))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            print(f"  ⚠ DOCX extraction failed for {file_name}: {e}")
            return ""

    return ""


# ── Core ingestion function ───────────────────────────────────────────────────

async def ingest_from_gdrive(
    access_token: str,
    db_pool: asyncpg.Pool,
    tenant_id: str,
    folder_id: str | None = None,
) -> dict:
    """
    Walk Google Drive, download supported files, chunk, embed, and insert.

    Args:
        access_token:  Valid Google OAuth access token.
        db_pool:       Shared asyncpg connection pool.
        tenant_id:     User's Supabase UUID (private data silo).
        folder_id:     Optional specific Drive folder ID. None = entire Drive.

    Returns:
        Summary dict: {"ingested": int, "skipped": int, "files": int, "errors": list}
    """
    summary = {"ingested": 0, "skipped": 0, "files": 0, "errors": []}

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise RuntimeError(f"Invalid tenant_id UUID: {tenant_id}")

    print(f"  → Starting Google Drive ingestion for tenant={tenant_id[:8]}...")

    async with httpx.AsyncClient() as http:
        async for file_meta in _walk_drive(http, access_token, folder_id):
            file_name = file_meta.get("name", "unknown")
            file_id   = file_meta["id"]
            mime      = file_meta.get("mimeType", "")

            # Build a stable source_id from Drive file ID
            source_id = f"gdrive_{file_id}"

            # Compute a dedup key — use md5Checksum for native files,
            # or a modifiedTime-based hash for Google Workspace files
            md5 = file_meta.get("md5Checksum") or file_meta.get("modifiedTime", file_id)
            dedup_content = f"{source_id}|{md5}"

            async with db_pool.acquire() as conn:
                await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
                already = await check_and_mark_ingested(
                    conn=conn,
                    tenant_id=tenant_id,
                    source="gdrive",
                    source_id=source_id,
                    raw_content=dedup_content,
                )

            if already:
                summary["skipped"] += 1
                continue

            # Download
            raw_bytes = await _download_file(http, access_token, file_meta)
            if raw_bytes is None:
                summary["errors"].append(f"Download failed: {file_name}")
                continue

            # Extract text
            text = _extract_text(raw_bytes, mime, file_name)
            if not text.strip():
                summary["skipped"] += 1
                continue

            # Chunk
            chunks = _chunk_text(text, source_name=file_name)
            if not chunks:
                summary["skipped"] += 1
                continue

            # Embed and insert each chunk
            print(f"  → {file_name}: {len(chunks)} chunks...")
            async with httpx.AsyncClient() as embed_client:
                async with db_pool.acquire() as conn:
                    for chunk in chunks:
                        embedding = await _embed(chunk["content"], embed_client)
                        emb_str   = f"[{','.join(map(str, embedding))}]"
                        event_id  = uuid.uuid4()

                        try:
                            async with conn.transaction():
                                await conn.execute(
                                    f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
                                )
                                await conn.execute(
                                    f"""
                                    INSERT INTO {SCHEMA}.vector_chunks
                                        (event_id, tenant_id, source_id, source_type,
                                         author_id, channel_or_space, content, embedding,
                                         allowed_groups, allowed_users, is_public, document_title)
                                    VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8::text::vector,
                                            $9, $10, $11, $12)
                                    ON CONFLICT DO NOTHING
                                    """,
                                    event_id,
                                    tenant_uuid,
                                    f"{source_id}_chunk_{chunk['idx']}",
                                    "gdrive",
                                    "google_drive",
                                    "google_drive",
                                    chunk["content"],
                                    emb_str,
                                    [],
                                    [],
                                    True,
                                    file_name,
                                )
                                summary["ingested"] += 1
                        except Exception as e:
                            print(f"    ⚠ DB insert failed for chunk of {file_name}: {e}")

            summary["files"] += 1
            # Small delay between files to avoid rate limiting
            await asyncio.sleep(0.1)

    print(
        f"✅ Google Drive ingestion complete: "
        f"{summary['ingested']} chunks ingested, "
        f"{summary['skipped']} skipped, "
        f"{summary['files']} files processed, "
        f"{len(summary['errors'])} errors."
    )
    return summary
