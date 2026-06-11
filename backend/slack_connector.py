"""
backend/slack_connector.py
──────────────────────────
Live Slack API connector — replaces the static JSON export ingestor.

Accepts a Bot Token + list of channel IDs, streams conversation history
via the Slack WebClient, normalises every message into the existing
Common Event Schema, deduplicates via Postgres, and feeds the output
into the shared pg_ingest_chunks() pipeline.

Chunking strategy (Phase 3 alignment):
  - Messages are grouped by thread_ts into cohesive thread blocks.
  - Each thread block is formatted as:
      [Slack / #channel / @author / YYYY-MM-DD]
      @user: message text\n@user2: reply...
  - Threads ≤ 800 chars: ingested as a single atomic chunk.
  - Threads > 800 chars: split with 600-char window / 100-char overlap.
  This matches the benchmark pipeline chunking in rag_bench/run_comprehensive_eval.py.

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
import time
import uuid
from collections import defaultdict
from typing import Optional

import asyncpg
import httpx
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from backend.db import check_and_mark_ingested

SCHEMA = "tenant_redwood_inference_prod"
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
MODEL_NAME_EMBED = "nomic-embed-text"

# ── Chunking constants (must match benchmark pipeline) ────────────────────
SLACK_THREAD_MAX_CHARS = 800
SLACK_CHUNK_SIZE = 600
SLACK_CHUNK_OVERLAP = 100

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


# ── Thread-level chunking (matches benchmark pipeline) ───────────────────────

def _chunk_slack_thread(
    messages: list[dict],
    channel_name: str,
    source_id_prefix: str,
) -> list[dict]:
    """
    Converts a list of related Slack messages (a thread) into one or more
    chunk dicts suitable for DB insertion.

    Format:
        [Slack / #channel / @author / YYYY-MM-DD]
        @user1: message text
        @user2: reply text
    """
    if not messages:
        return []

    lines = []
    for msg in messages:
        author = msg.get("user") or msg.get("username") or "unknown"
        text = _scrub_pii(str(msg.get("text") or "").strip())
        if text:
            lines.append(f"@{author}: {text}")

    if not lines:
        return []

    # Date from first message timestamp
    first_ts = messages[0].get("ts", "0")
    try:
        date_str = time.strftime("%Y-%m-%d", time.gmtime(float(first_ts)))
    except Exception:
        date_str = "unknown-date"

    participants = list({
        msg.get("user") or msg.get("username") or "unknown"
        for msg in messages
        if msg.get("user") or msg.get("username")
    })
    primary_author = participants[0] if participants else "unknown"

    meta_prefix = f"[Slack / #{channel_name} / @{primary_author} / {date_str}]\n"
    full_text = meta_prefix + "\n".join(lines)

    def _make_chunk(content: str, idx: int) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "source_id": f"{source_id_prefix}_chunk{idx}",
            "source_type": "slack",
            "author_id": primary_author.lower().replace(" ", "_"),
            "channel_or_space": channel_name,
            "content": content,
        }

    # Short thread: single atomic chunk
    if len(full_text) <= SLACK_THREAD_MAX_CHARS:
        return [_make_chunk(full_text, 0)]

    # Long thread: overlapping splits
    chunks = []
    start = 0
    idx = 0
    while start < len(full_text):
        end = min(start + SLACK_CHUNK_SIZE, len(full_text))
        content = full_text[start:end].strip()
        if content:
            chunks.append(_make_chunk(content, idx))
        start += SLACK_CHUNK_SIZE - SLACK_CHUNK_OVERLAP
        idx += 1
    return chunks


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
    """Ingest a single channel's history using thread-level chunking."""
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

    # ── Step 1: Collect all messages (with pagination) ────────────────────────
    all_messages: list[dict] = []
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

        for msg in result.get("messages", []):
            # Skip bot messages and non-user messages
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            if not str(msg.get("text", "")).strip():
                continue
            all_messages.append(msg)

        response_metadata = result.get("response_metadata", {})
        cursor = response_metadata.get("next_cursor")
        if not cursor:
            break

    # ── Step 2: Group messages by thread_ts ───────────────────────────────
    threads: dict[str, list[dict]] = defaultdict(list)
    for msg in all_messages:
        # thread_ts is set for replies; for top-level messages, use ts as thread key
        thread_key = msg.get("thread_ts") or msg.get("ts", str(uuid.uuid4()))
        threads[thread_key].append(msg)

    # Sort messages within each thread by ts
    for thread_key in threads:
        threads[thread_key].sort(key=lambda m: float(m.get("ts", 0)))

    print(f"    Grouped {len(all_messages)} messages into {len(threads)} threads.")

    # ── Step 3: Chunk and ingest each thread ─────────────────────────────
    async with db_pool.acquire() as conn:
        await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")

        for thread_key, thread_msgs in threads.items():
            source_id_prefix = f"slack_{channel_id}_{thread_key}"
            thread_chunks = _chunk_slack_thread(thread_msgs, channel_name, source_id_prefix)

            for chunk in thread_chunks:
                # Dedup check using the chunk's source_id (thread + chunk index)
                already_ingested = await check_and_mark_ingested(
                    conn=conn,
                    tenant_id=tenant_id,
                    source="slack",
                    source_id=chunk["source_id"],
                    raw_content=chunk["content"],
                )
                if already_ingested:
                    summary["skipped"] += 1
                    continue

                embedding = await _embed(chunk["content"], http_client)
                emb_str = f"[{','.join(map(str, embedding))}]"

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
                        chunk["source_id"],
                        "slack",
                        channel_name,
                        chunk["content"],
                        emb_str,
                        [],
                        [],
                        True,
                    )
                    summary["ingested"] += 1
                except Exception as e:
                    print(f"    ⚠ DB insert failed for {chunk['source_id']}: {e}")

    return summary
