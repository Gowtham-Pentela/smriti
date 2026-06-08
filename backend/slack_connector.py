"""
backend/slack_connector.py
──────────────────────────
Live Slack API connector — replaces the static JSON export ingestor.

Accepts a Bot Token + list of channel IDs, streams conversation history
via the Slack WebClient, normalises every message into the existing
Common Event Schema, deduplicates via Postgres, and feeds the output
into the shared pg_ingest_chunks() pipeline.

Design decisions (locked):
  - Uses slack_sdk.WebClient (synchronous) wrapped in asyncio.to_thread()
    so FastAPI's async event loop is never blocked.
  - Deduplication: Postgres ingestion_hashes table (crash-safe, multi-worker).
  - No Celery, no Redis queue. FastAPI BackgroundTasks is sufficient for ≤5
    concurrent connector runs. Add Celery when a customer complains.
  - OAuth tokens are stored encrypted in tenant_credentials (via db.py).
  - Default backfill: 90 days. Configurable per call.
"""

import asyncio
import datetime
import hashlib
import os
import uuid
from typing import Optional

import asyncpg
import httpx
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from backend.db import check_and_mark_ingested

SCHEMA = "tenant_redwood_inference_prod"
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
MODEL_NAME_EMBED = "nomic-embed-text"

# ── PII scrub patterns (mirrors parser.py) ────────────────────────────────────
import re

_PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # email
    re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # phone
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"(sk|pk|rk)[-_](?:live|test|prod)[-_][A-Za-z0-9]{20,}"),  # API keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS key
]


def _scrub_pii(text: str) -> str:
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ── Embedding helper ──────────────────────────────────────────────────────────

async def _embed(text: str, client: httpx.AsyncClient) -> list[float]:
    """Single-shot embedding with a silent zero-vector fallback."""
    try:
        resp = await client.post(
            OLLAMA_EMBED_URL,
            json={"model": MODEL_NAME_EMBED, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [0.0] * 768)
    except Exception as e:
        print(f"  ⚠ Embedding failed: {e}")
        return [0.0] * 768


# ── Core ingestion function ───────────────────────────────────────────────────

async def ingest_from_slack(
    bot_token: str,
    channel_ids: list[str],
    db_pool: asyncpg.Pool,
    tenant_id: str,
    tenant_namespace_uuid: str,
    days_back: int = 90,
) -> dict:
    """
    Pull channel history from Slack, normalise, deduplicate, embed, and insert.

    Args:
        bot_token:             Slack Bot OAuth token (xoxb-...).
        channel_ids:           List of Slack channel IDs to ingest.
        db_pool:               Shared asyncpg connection pool.
        tenant_id:             String tenant identifier (used for RLS and dedup).
        tenant_namespace_uuid: UUID string used for DB inserts (tenant_id column).
        days_back:             How many days of history to pull. Default 90.

    Returns:
        Summary dict: {"ingested": int, "skipped": int, "channels": int, "errors": list}
    """
    cutoff_ts = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_back)
    ).timestamp()

    # Slack SDK is synchronous — run in a thread so we don't block the event loop
    client = await asyncio.to_thread(WebClient, token=bot_token)

    summary = {"ingested": 0, "skipped": 0, "channels": 0, "errors": []}

    async with httpx.AsyncClient() as http_client:
        for channel_id in channel_ids:
            try:
                channel_summary = await _ingest_channel(
                    client=client,
                    http_client=http_client,
                    db_pool=db_pool,
                    channel_id=channel_id,
                    tenant_id=tenant_id,
                    tenant_namespace_uuid=tenant_namespace_uuid,
                    cutoff_ts=cutoff_ts,
                )
                summary["ingested"] += channel_summary["ingested"]
                summary["skipped"]  += channel_summary["skipped"]
                summary["channels"] += 1
            except Exception as e:
                err = f"Channel {channel_id}: {e}"
                print(f"  ✗ Slack ingest error — {err}")
                summary["errors"].append(err)

    print(
        f"✅ Slack ingestion complete: {summary['ingested']} new chunks, "
        f"{summary['skipped']} skipped (dedup), "
        f"{summary['channels']} channels, "
        f"{len(summary['errors'])} errors."
    )
    return summary


async def _ingest_channel(
    client: WebClient,
    http_client: httpx.AsyncClient,
    db_pool: asyncpg.Pool,
    channel_id: str,
    tenant_id: str,
    tenant_namespace_uuid: str,
    cutoff_ts: float,
) -> dict:
    """Ingest a single channel's history."""
    summary = {"ingested": 0, "skipped": 0}
    cursor = None
    channel_name = channel_id  # fallback; resolved below

    # Resolve channel name
    try:
        info = await asyncio.to_thread(client.conversations_info, channel=channel_id)
        channel_name = info["channel"].get("name", channel_id)
    except SlackApiError:
        pass

    print(f"  → Ingesting #{channel_name} ({channel_id})...")

    while True:
        kwargs: dict = {
            "channel": channel_id,
            "limit": 200,
            "oldest": str(cutoff_ts),
        }
        if cursor:
            kwargs["cursor"] = cursor

        try:
            result = await asyncio.to_thread(client.conversations_history, **kwargs)
        except SlackApiError as e:
            raise RuntimeError(f"conversations_history failed: {e.response['error']}")

        messages = result.get("messages", [])

        for msg in messages:
            # Skip bot messages and non-user messages
            if msg.get("bot_id") or msg.get("subtype"):
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            # Normalise to Common Event Schema
            ts = msg.get("ts", "0")
            source_id = f"slack_{channel_id}_{ts}"
            author_id = msg.get("user", "unknown")
            cleaned = _scrub_pii(text)

            async with db_pool.acquire() as conn:
                await conn.execute(
                    f"SET app.current_tenant_id = '{tenant_id}'"
                )

                # Dedup check (Postgres-backed, crash-safe)
                already_ingested = await check_and_mark_ingested(
                    conn=conn,
                    tenant_id=tenant_id,
                    source="slack",
                    source_id=source_id,
                    raw_content=cleaned,
                )
                if already_ingested:
                    summary["skipped"] += 1
                    continue

                # Generate embedding
                embedding = await _embed(cleaned, http_client)
                emb_str = f"[{','.join(map(str, embedding))}]"

                # Insert into vector_chunks
                event_id = uuid.uuid4()
                try:
                    await conn.execute(
                        f"""
                        INSERT INTO {SCHEMA}.vector_chunks
                            (event_id, tenant_id, source_id, source_type,
                             channel_or_space, content, embedding,
                             allowed_groups, allowed_users, is_public)
                        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::text::vector, $8, $9, $10)
                        ON CONFLICT DO NOTHING
                        """,
                        event_id,
                        tenant_namespace_uuid,
                        source_id,
                        "slack",
                        channel_name,
                        cleaned,
                        emb_str,
                        [],
                        [],
                        True,
                    )
                    summary["ingested"] += 1
                except Exception as e:
                    print(f"    ⚠ DB insert failed for {source_id}: {e}")

        # Pagination
        response_metadata = result.get("response_metadata", {})
        cursor = response_metadata.get("next_cursor")
        if not cursor:
            break

    return summary
