"""
backend/sync_scheduler.py
──────────────────────────
Background sync scheduler — keeps all tenants' knowledge bases fresh.

Runs as a persistent asyncio task started in the FastAPI lifespan context.
Every SYNC_INTERVAL_SECONDS (default 1800 = 30 minutes), it:

  1. Loads all tenant credentials from tenant_credentials table.
  2. Decrypts each token.
  3. Calls ingest_from_slack() for each Slack-connected tenant.
  4. Records sync result in sync_log table.
  5. Sleeps until next tick.

Because ingest_from_slack() uses Postgres dedup (ingestion_hashes), re-running
is always safe: already-ingested chunks are skipped in O(1) per message.

Design decisions (locked):
  - No Celery, no Redis. FastAPI BackgroundTasks + asyncio.sleep is sufficient
    for ≤20 tenants. Add Celery Beat when you hit that wall.
  - Errors are per-tenant isolated: one bad token doesn't stop others.
  - Sync interval is tunable via SYNC_INTERVAL_SECONDS env var.
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import asyncpg

from backend.db import load_tenant_credentials
from backend import slack_connector
from backend.slack_oauth import get_all_public_channel_ids

SCHEMA         = "tenant_redwood_inference_prod"
SYNC_INTERVAL  = int(os.getenv("SYNC_INTERVAL_SECONDS", "1800"))  # 30 min default

# Shared status dict — polled by GET /sync-status
sync_status = {
    "is_running":     False,
    "last_run_at":    None,
    "next_run_at":    None,
    "tenants_synced": 0,
    "errors":         [],
}

# Per-tenant in-progress lock — prevents concurrent syncs for the same tenant.
# Key: "{tenant_id}:{source}". Value: True while running.
_active_syncs: dict = {}



async def start_sync_loop(db_pool: asyncpg.Pool) -> None:
    """
    Persistent background task. Call once from FastAPI lifespan.
    Never returns (runs until the process exits).
    """
    print(f"🔄 Sync scheduler started. Interval: {SYNC_INTERVAL}s ({SYNC_INTERVAL // 60} min)")

    while True:
        next_run = time.time() + SYNC_INTERVAL
        sync_status["next_run_at"] = datetime.fromtimestamp(next_run, timezone.utc).isoformat()

        # Wait for next tick
        await asyncio.sleep(SYNC_INTERVAL)

        await sync_all_tenants(db_pool)


async def sync_all_tenants(db_pool: asyncpg.Pool) -> dict:
    """
    Single sync cycle across all tenants with stored credentials.
    Returns a summary dict. Safe to call manually from tests or admin endpoints.
    """
    sync_status["is_running"] = True
    sync_status["errors"]     = []
    started_at = time.time()

    print(f"\n🔄 [{datetime.now().isoformat()}] Starting sync cycle...")

    # Load all (tenant_id, source) pairs from credentials table
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT DISTINCT tc.tenant_id, tc.source
            FROM {SCHEMA}.tenant_credentials tc
            JOIN tenant_registry tr ON tr.tenant_id::text = tc.tenant_id
            WHERE tr.is_active = true
            """
        )

    tenants_synced = 0
    errors = []

    for row in rows:
        tenant_id = row["tenant_id"]
        source    = row["source"]

        # ── Concurrent sync guard ──────────────────────────────────────────────
        # If the scheduler AND a manual /sync-now both fire simultaneously for the
        # same tenant, we'd hit the Slack API twice and burn Ollama compute twice.
        # The dedup table prevents duplicate chunks, but the wasted work is real.
        lock_key = f"{tenant_id}:{source}"
        if _active_syncs.get(lock_key):
            print(f"  ⏭ Skipping {lock_key} — sync already in progress.")
            continue

        _active_syncs[lock_key] = True
        try:
            await _sync_tenant(db_pool, tenant_id, source)
            tenants_synced += 1
        except Exception as e:
            err = f"tenant={tenant_id} source={source}: {e}"
            print(f"  ✗ Sync error — {err}")
            errors.append(err)
            await _log_sync(db_pool, tenant_id, source, 0, str(e))
        finally:
            _active_syncs.pop(lock_key, None)

    elapsed = round(time.time() - started_at, 1)
    now_iso = datetime.now(timezone.utc).isoformat()

    sync_status.update({
        "is_running":     False,
        "last_run_at":    now_iso,
        "tenants_synced": tenants_synced,
        "errors":         errors,
    })

    print(
        f"✅ Sync complete in {elapsed}s: "
        f"{tenants_synced} tenants synced, {len(errors)} errors."
    )
    return {"elapsed": elapsed, "tenants_synced": tenants_synced, "errors": errors}


async def _sync_tenant(db_pool: asyncpg.Pool, tenant_id: str, source: str) -> None:
    """Sync a single (tenant, source) pair."""
    async with db_pool.acquire() as conn:
        token_dict = await load_tenant_credentials(conn, tenant_id, source)

    if token_dict is None:
        print(f"  ⚠ No credentials for tenant={tenant_id} source={source} — skipping.")
        return

    if source == "slack":
        bot_token = token_dict.get("bot_token") or token_dict.get("access_token")
        if not bot_token:
            raise ValueError("Stored Slack credential has no bot_token field.")

        # Discover all public channels automatically
        channel_ids = await get_all_public_channel_ids(bot_token)
        if not channel_ids:
            print(f"  ℹ tenant={tenant_id}: No public channels found.")
            return

        result = await slack_connector.ingest_from_slack(
            bot_token=bot_token,
            channel_ids=channel_ids,
            db_pool=db_pool,
            tenant_id=tenant_id,
            tenant_namespace_uuid=tenant_id,
            days_back=1,  # Incremental sync: only last 24h on re-runs
        )
        await _log_sync(
            db_pool, tenant_id, source,
            result["ingested"],
            "; ".join(result["errors"]) if result["errors"] else None,
        )

    elif source == "gdrive":
        from backend import gdrive_oauth
        from backend import gdrive_connector
        access_token = await gdrive_oauth.get_valid_token(tenant_id, db_pool)
        result = await gdrive_connector.ingest_from_gdrive(
            access_token=access_token,
            db_pool=db_pool,
            tenant_id=tenant_id,
        )
        await _log_sync(
            db_pool, tenant_id, source,
            result["ingested"],
            "; ".join(result["errors"]) if result["errors"] else None,
        )

    elif source == "confluence":
        from backend import confluence_connector
        result = await confluence_connector.ingest_from_confluence(
            confluence_url=token_dict["confluence_url"],
            email=token_dict["email"],
            api_token=token_dict["api_token"],
            db_pool=db_pool,
            tenant_id=tenant_id,
        )
        await _log_sync(
            db_pool, tenant_id, source,
            result["ingested"],
            "; ".join(result["errors"]) if result["errors"] else None,
        )


async def _log_sync(
    db_pool: asyncpg.Pool,
    tenant_id: str,
    source: str,
    chunks_added: int,
    error_msg: str | None,
) -> None:
    """Write a sync_log row for observability."""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenant_redwood_inference_prod.sync_log
                    (tenant_id, source, chunks_added, error_msg)
                VALUES ($1, $2, $3, $4)
                """,
                tenant_id, source, chunks_added, error_msg,
            )
    except Exception as e:
        print(f"  ⚠ Failed to write sync_log: {e}")
