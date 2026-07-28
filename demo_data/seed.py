"""
demo_data/seed.py
─────────────────
One-shot script to wipe the company index and ingest the demo documents.

Usage:
    python -m demo_data.seed

Reads DATABASE_URL from .env (same as the backend). Uses the same ingestion
path the S3 worker uses, so categories get assigned from the folder name.
"""
import os
import sys
import asyncio
from pathlib import Path

# Make sure we run with the venv activated
import asyncpg
from dotenv import load_dotenv

# Repo root is the parent of demo_data/
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

sys.path.insert(0, str(ROOT))

from backend.s3_connector import ingest_local_folder  # noqa: E402
from backend.auth import COMPANY_TENANT_ID  # noqa: E402

DEMO_DIR = ROOT / "demo_data"
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


async def wipe(pool: asyncpg.Pool) -> None:
    """Wipe vector_chunks, ingestion_hashes, and s3_manifest for the tenant.
    s3_manifest is S3-scoped only; the local-folder seed clears it so a
    demo reset doesn't leak stale rows from a previous S3 session."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", COMPANY_TENANT_ID)
            await conn.execute("DELETE FROM public.vector_chunks")
            await conn.execute("DELETE FROM public.ingestion_hashes")
            await conn.execute("DELETE FROM public.s3_manifest")
    print("  wiped vector_chunks + ingestion_hashes + s3_manifest")


async def main() -> None:
    if not DEMO_DIR.is_dir():
        print(f"ERROR: demo_data/ not found at {DEMO_DIR}")
        sys.exit(1)

    print(f"Demo dir: {DEMO_DIR}")
    print(f"DB URL:   {DB_URL[:60]}...")

    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4, statement_cache_size=0)
    try:
        print("\n[1/2] Wiping existing index…")
        await wipe(pool)

        print("\n[2/2] Ingesting demo documents…")
        summary = await ingest_local_folder(str(DEMO_DIR), pool)

        print("\n=== Summary ===")
        print(f"  ok:      {summary.get('ok', 0)}")
        print(f"  skipped: {summary.get('skipped', 0)}")
        print(f"  failed:  {summary.get('failed', 0)}")
        for f in summary.get("files", []):
            cat = f["key"].split("/", 1)[0] if "/" in f["key"] else "general"
            status = f["status"]
            n = f.get("chunks", "—")
            print(f"  [{cat:11s}] {f['key']:50s} {status:8s} {n}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
