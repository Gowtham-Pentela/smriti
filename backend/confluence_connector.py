"""
backend/confluence_connector.py
───────────────────────────────
Confluence Wiki ingestion connector for Smriti (KGF).

Walks a user's Confluence space/pages, downloads content, cleans the storage
format HTML/XML, parses into chunks, and inserts into vector_chunks via the
shared pg_ingest_chunks() pattern.

Supported auth:
  - Atlassian API Token Basic Auth (Domain URL, Email/Username, API Token)

Design decisions:
  - Uses httpx (async) with Basic Authentication.
  - Deduplication via Postgres ingestion_hashes (same as other connectors).
  - PII scrub applied to all text content before embedding.
"""

import asyncio
import base64
import hashlib
import os
import re
import uuid
from typing import Dict, List, Any

import asyncpg
import httpx

from backend.db import check_and_mark_ingested

SCHEMA          = "tenant_redwood_inference_prod"
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
MODEL_NAME_EMBED = "nomic-embed-text"

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

async def _embed(text: str, http: httpx.AsyncClient) -> List[float]:
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

def _chunk_text(
    text: str,
    page_title: str,
    space_key: str,
    page_id: str,
    chunk_size: int = 800,
    overlap: int = 100
) -> List[Dict[str, Any]]:
    """Split Confluence page content into overlapping chunks."""
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
                "source":   f"confluence_{page_id}",
                "type":     "confluence",
                "location": space_key,
                "title":    page_title,
                "idx":      idx,
            })
        start += chunk_size - overlap
        idx   += 1

    return chunks


# ── HTML/XML Storage Format cleaner ──────────────────────────────────────────

def _clean_confluence_html(html: str) -> str:
    """Strip XML/HTML tags and decode common entities to get clean text."""
    if not html:
        return ""
    # Strip HTML/XML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode basic HTML entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Collapse multiple spaces/newlines
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ── Confluence API helpers ────────────────────────────────────────────────────

def get_auth_headers(email: str, api_token: str) -> Dict[str, str]:
    """Generate basic auth headers for Confluence API."""
    usr_pass = f"{email}:{api_token}"
    auth_b64 = base64.b64encode(usr_pass.encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {auth_b64}",
        "Accept": "application/json",
    }


def normalize_confluence_url(url: str) -> str:
    """Normalize domain/URL and ensure correct wiki prefix for cloud api."""
    url = url.strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    if ".atlassian.net" in url and not url.endswith("/wiki"):
        url = f"{url}/wiki"
    return url


async def verify_confluence_credentials(url: str, email: str, api_token: str) -> bool:
    """Test connection to Confluence by making a simple metadata API request."""
    norm_url = normalize_confluence_url(url)
    headers = get_auth_headers(email, api_token)
    
    async with httpx.AsyncClient() as client:
        try:
            # Simple metadata check
            resp = await client.get(
                f"{norm_url}/rest/api/settings/systemInfo",
                headers=headers,
                timeout=10.0,
            )
            # If systemInfo isn't accessible, try fetching spaces as backup
            if resp.status_code != 200:
                resp = await client.get(
                    f"{norm_url}/rest/api/space?limit=1",
                    headers=headers,
                    timeout=10.0,
                )
            return resp.status_code == 200
        except Exception as e:
            print(f"  ⚠ Confluence verification failed: {e}")
            return False


# ── Ingestion Walker ─────────────────────────────────────────────────────────

async def ingest_from_confluence(
    confluence_url: str,
    email: str,
    api_token: str,
    db_pool: asyncpg.Pool,
    tenant_id: str,
) -> Dict[str, Any]:
    """
    Paginate through Confluence pages, clean XML storage, chunk, embed and index.
    """
    summary = {"ingested": 0, "skipped": 0, "files": 0, "errors": []}

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise RuntimeError(f"Invalid tenant_id UUID: {tenant_id}")

    norm_url = normalize_confluence_url(confluence_url)
    headers = get_auth_headers(email, api_token)

    print(f"  → Starting Confluence ingestion for tenant={tenant_id[:8]} at {norm_url}...")

    start = 0
    limit = 50
    async with httpx.AsyncClient() as http:
        while True:
            params = {
                "type": "page",
                "expand": "body.storage,space",
                "limit": limit,
                "start": start,
            }
            try:
                resp = await http.get(
                    f"{norm_url}/rest/api/content",
                    headers=headers,
                    params=params,
                    timeout=25.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                err_msg = f"Confluence fetch pages failed: {e}"
                print(f"  ✗ {err_msg}")
                summary["errors"].append(err_msg)
                break

            results = data.get("results", [])
            if not results:
                break

            for page in results:
                page_id    = page.get("id")
                page_title = page.get("title", "Untitled Page")
                space_info = page.get("space") or {}
                space_key  = space_info.get("key", "Confluence")

                # Build source identifier
                source_id = f"confluence_{page_id}"

                body_obj  = page.get("body", {}).get("storage", {})
                raw_html  = body_obj.get("value", "")

                # Modified time or version hash for deduplication
                version = page.get("version", {}).get("number", 1)
                dedup_content = f"{source_id}|v{version}"

                # Deduplication check
                async with db_pool.acquire() as conn:
                    await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
                    already = await check_and_mark_ingested(
                        conn=conn,
                        tenant_id=tenant_id,
                        source="confluence",
                        source_id=source_id,
                        raw_content=dedup_content,
                    )

                if already:
                    summary["skipped"] += 1
                    continue

                # Extract and clean plain text
                clean_text = _clean_confluence_html(raw_html)
                if not clean_text.strip():
                    summary["skipped"] += 1
                    continue

                # Chunk page content
                chunks = _chunk_text(clean_text, page_title, space_key, page_id)
                if not chunks:
                    summary["skipped"] += 1
                    continue

                # Embed & Index
                print(f"  → Confluence Page: {page_title} ({len(chunks)} chunks)...")
                async with db_pool.acquire() as conn:
                    for chunk in chunks:
                        embedding = await _embed(chunk["content"], http)
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
                                    "confluence",
                                    "confluence",
                                    space_key,
                                    chunk["content"],
                                    emb_str,
                                    [],
                                    [],
                                    True,
                                    page_title,
                                )
                                summary["ingested"] += 1
                        except Exception as e:
                            print(f"    ⚠ DB insert failed for Confluence chunk: {e}")

                summary["files"] += 1
                await asyncio.sleep(0.05)

            # Check next page pagination
            if len(results) < limit:
                break
            start += limit

    print(
        f"✅ Confluence ingestion complete: "
        f"{summary['ingested']} chunks, "
        f"{summary['skipped']} skipped, "
        f"{summary['files']} pages, "
        f"{len(summary['errors'])} errors."
    )
    return summary
