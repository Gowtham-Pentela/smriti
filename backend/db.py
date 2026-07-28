"""
backend/db.py
─────────────
Database helpers — dedup only (the OAuth credential store is gone).

Provides:
  - sha256()                       — content hash for dedup
  - check_and_mark_ingested()      — UPSERT into ingestion_hashes; returns True
                                     if the (tenant, hash) is already known
                                     (caller should skip), False if it's new.
"""

import hashlib
import asyncpg


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def check_and_mark_ingested(
    conn: asyncpg.Connection,
    tenant_id: str,
    file_hash: str,
    source: str,
    chunks: int = 0,
) -> bool:
    """
    UPSERT into ingestion_hashes. Returns:
        True  → row already existed with the same hash (skip).
        False → new row inserted, or hash changed (caller should ingest).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO public.ingestion_hashes (tenant_id, file_hash, source, chunks)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, file_hash) DO UPDATE
          SET source      = EXCLUDED.source,
              chunks      = EXCLUDED.chunks,
              ingested_at = NOW()
        WHERE public.ingestion_hashes.source IS DISTINCT FROM EXCLUDED.source
        RETURNING (xmax = 0) AS is_new
        """,
        tenant_id, file_hash, source, chunks,
    )

    # If the WHERE clause in the UPDATE blocked the update, the row existed
    # with the same source → already ingested.
    if row is None:
        return True
    return False
