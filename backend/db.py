"""
backend/db.py
─────────────
Shared database helpers used by the main API and all connectors.

Provides:
  - check_and_mark_ingested()  — Postgres-backed deduplication (replaces in-memory cache)
  - save_tenant_credentials()  — Encrypted OAuth token storage
  - load_tenant_credentials()  — Token retrieval + decryption
"""

import hashlib
import asyncpg
from backend.crypto import encrypt_token, decrypt_token

SCHEMA = "tenant_redwood_inference_prod"


# ── Deduplication ─────────────────────────────────────────────────────────────

def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def check_and_mark_ingested(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_id: str,
    raw_content: str,
) -> bool:
    """
    Check whether this (tenant_id, source, source_id) has already been ingested
    with the same content hash.

    Returns True  → already ingested with identical content (caller should SKIP).
    Returns False → new or changed content (caller should ingest, then call this
                    again with upsert=True, or rely on the ON CONFLICT DO UPDATE).

    Uses an UPSERT so the call is always safe to make without a prior SELECT.
    The unique constraint on (tenant_id, source, source_id) prevents duplicates.
    """
    content_hash = sha256(raw_content)

    result = await conn.fetchrow(
        f"""
        INSERT INTO {SCHEMA}.ingestion_hashes (tenant_id, source, source_id, content_hash)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT ON CONSTRAINT uq_ingestion_hashes
        DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            ingested_at  = NOW()
        WHERE {SCHEMA}.ingestion_hashes.content_hash != EXCLUDED.content_hash
        RETURNING (xmax = 0) AS is_new_row,
                  (content_hash = $4) AS hash_unchanged
        """,
        tenant_id, source, source_id, content_hash,
    )

    if result is None:
        # ON CONFLICT fired but WHERE clause prevented update → hash unchanged → skip
        return True   # already ingested, skip

    # Row was inserted (new) or updated (content changed) → process it
    return False


# ── Tenant credential store ───────────────────────────────────────────────────

async def save_tenant_credentials(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    token_dict: dict,
    scopes: list[str] | None = None,
) -> None:
    """
    Encrypt and upsert an OAuth token blob for a tenant+source pair.
    """
    encrypted = encrypt_token(token_dict)
    await conn.execute(
        f"""
        INSERT INTO {SCHEMA}.tenant_credentials (tenant_id, source, token_encrypted, scopes)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT ON CONSTRAINT uq_tenant_credentials
        DO UPDATE SET
            token_encrypted = EXCLUDED.token_encrypted,
            scopes          = EXCLUDED.scopes,
            updated_at      = NOW()
        """,
        tenant_id, source, encrypted, scopes or [],
    )


async def load_tenant_credentials(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
) -> dict | None:
    """
    Load and decrypt an OAuth token blob for a tenant+source pair.
    Returns None if no credentials are stored.
    """
    row = await conn.fetchrow(
        f"""
        SELECT token_encrypted FROM {SCHEMA}.tenant_credentials
        WHERE tenant_id = $1 AND source = $2
        """,
        tenant_id, source,
    )
    if row is None:
        return None
    return decrypt_token(bytes(row["token_encrypted"]))


async def delete_tenant_credentials(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
) -> bool:
    """
    Delete stored OAuth credentials for a tenant+source pair.
    Returns True if a row was deleted, False if nothing was found.
    Used when a user clicks 'Disconnect' in the UI.
    """
    result = await conn.execute(
        f"""
        DELETE FROM {SCHEMA}.tenant_credentials
        WHERE tenant_id = $1 AND source = $2
        """,
        tenant_id, source,
    )
    # asyncpg returns "DELETE N" where N is rows affected
    rows_deleted = int(result.split()[-1])
    return rows_deleted > 0

