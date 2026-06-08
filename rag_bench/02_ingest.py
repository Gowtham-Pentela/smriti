#!/usr/bin/env python3
"""
Step 2: Async Slack ingestion pipeline for EnterpriseRAG-Bench.
Scans generated_data/sources/slack/, filters Slack records, embeds with
nomic-embed-text via Ollama, and bulk-writes to tenant_redwood_inference_prod.vector_chunks.
"""

import os
import sys
import json
import uuid
import asyncio
import asyncpg
import aiohttp
from pathlib import Path
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────────────
BENCH_ROOT    = Path("/Users/gowtham/EnterpriseRAG-Bench")
SLACK_ROOT    = BENCH_ROOT / "generated_data" / "sources" / "slack"
UUID_INDEX    = BENCH_ROOT / "generated_data" / "uuid_index.json"
QUESTIONS     = BENCH_ROOT / "questions.jsonl"
DB_URL        = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
EMBED_URL     = "http://localhost:11434/api/embed"
EMBED_FALLBACK= "http://localhost:11434/api/embeddings"
EMBED_MODEL   = "nomic-embed-text"
TENANT_ID     = "tenant-redwood-inference-prod"
TENANT_UUID   = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
SCHEMA        = "tenant_redwood_inference_prod"
BATCH_SIZE    = 20
EMBED_CONCUR  = 5

# ── Embedding ───────────────────────────────────────────────────────────────
async def embed_text(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Call Ollama embed endpoint; fallback to legacy /api/embeddings."""
    try:
        async with session.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "input": text},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 200:
                data = await r.json()
                embs = data.get("embeddings", [])
                if embs:
                    return embs[0]
    except Exception:
        pass

    # Legacy fallback
    try:
        async with session.post(
            EMBED_FALLBACK,
            json={"model": EMBED_MODEL, "prompt": "search_document: " + text},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("embedding", [0.0] * 768)
    except Exception as e:
        print(f"  [embed error] {e}", file=sys.stderr)

    return [0.0] * 768


# ── Parsing ─────────────────────────────────────────────────────────────────
def parse_slack_file(path: Path, doc_uuid: str | None = None) -> dict | None:
    """Parse a single Slack JSON file into a normalized common event record."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"  [parse error] {path.name}: {e}", file=sys.stderr)
        return None

    # Determine content — can be a string or a list
    content_raw = raw.get("messages") or raw.get("text") or raw.get("content") or ""
    if isinstance(content_raw, list):
        content = "\n".join(str(x) for x in content_raw if x)
    else:
        content = str(content_raw)
    if not content.strip():
        return None

    channel   = raw.get("channel", path.parent.name)
    thread_ts = str(raw.get("thread_ts", raw.get("first_message_ts", "")))
    source_id = doc_uuid or raw.get("dataset_doc_uuid", str(uuid.uuid4()))

    participants = raw.get("participants", [])
    author_name  = participants[0] if participants else "unknown"

    try:
        ts_val = float(thread_ts) if thread_ts else 0.0
        ts_str = datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
    except Exception:
        ts_str = datetime.now(tz=timezone.utc).isoformat()

    return {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        TENANT_ID,
        "source":           "slack",
        "source_id":        source_id,
        "author": {
            "id":           author_name.lower().replace(" ", "_"),
            "display_name": author_name,
            "email":        f"{author_name.lower().replace(' ', '.')}@redwood.ai",
        },
        "timestamp":        ts_str,
        "thread_id":        thread_ts,
        "channel_or_space": channel,
        "content_type":     "message",
        "raw_content":      content,
        "cleaned_content":  content.strip(),
        "allowed_groups":   [],
        "allowed_users":    [],
        "is_public":        True,
    }


def collect_target_files() -> list[tuple[Path, str]]:
    """
    Use uuid_index.json to find exact file paths for all 167 Slack doc IDs
    referenced in questions.jsonl. Also includes all top-level Slack JSON files.
    Returns list of (path, doc_uuid) tuples.
    """
    # Step 1: collect all expected Slack doc IDs from questions
    needed_ids: set[str] = set()
    with open(QUESTIONS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            sources = [s.lower() for s in q.get("source_types", [])]
            q_type  = q.get("question_type", "").lower()
            if "slack" in sources or q_type in ("basic", "semantic"):
                needed_ids.update(q.get("expected_doc_ids", []))

    # Step 2: load uuid_index and find paths for needed IDs
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()

    if UUID_INDEX.exists():
        idx = json.loads(UUID_INDEX.read_text())
        for doc_id in needed_ids:
            rel = idx.get(doc_id)
            if rel:
                full = BENCH_ROOT / "generated_data" / "sources" / rel
                if full.exists() and "slack" in str(full) and doc_id not in seen:
                    result.append((full, doc_id))
                    seen.add(doc_id)

    # Step 3: also include all top-level Slack JSON files (pre-aggregated threads)
    for path in sorted(SLACK_ROOT.glob("*.json")):
        doc_id = None
        try:
            d = json.loads(path.read_text())
            doc_id = d.get("dataset_doc_uuid", str(uuid.uuid4()))
        except Exception:
            doc_id = str(uuid.uuid4())
        if doc_id not in seen:
            result.append((path, doc_id))
            seen.add(doc_id)

    return result


# ── DB write ────────────────────────────────────────────────────────────────
async def bulk_insert(conn: asyncpg.Connection, rows: list[dict]) -> int:
    """Batch insert rows into vector_chunks. Returns count inserted."""
    if not rows:
        return 0

    records = [
        (
            r["event_id"],
            TENANT_UUID,           # tenant_id (NOT NULL in existing schema)
            r["source_id"],
            r["thread_id"],
            "slack",
            r["author"]["id"],
            r["channel_or_space"],
            r["cleaned_content"],
            json.dumps(r["embedding"]),
            r["allowed_groups"],
            r["allowed_users"],
            r["is_public"],
        )
        for r in rows
    ]

    await conn.executemany(
        f"""
        INSERT INTO {SCHEMA}.vector_chunks
            (event_id, tenant_id, source_id, thread_id, source_type, author_id,
             channel_or_space, content, embedding,
             allowed_groups, allowed_users, is_public)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::text::vector, $10, $11, $12)
        ON CONFLICT DO NOTHING
        """,
        records,
    )
    return len(records)



# ── Main ────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  KGF Slack Ingestion Pipeline — EnterpriseRAG-Bench")
    print("=" * 60)

    # 1. Collect only the files we actually need for the benchmark
    targets = collect_target_files()
    print(f"\n[1] Targeted {len(targets)} Slack files (from uuid_index + top-level)")
    print(f"    (Skipping the other 285k files — not referenced by any benchmark question)")

    # 2. Parse all records
    records = []
    skipped = 0
    for path, doc_id in targets:
        rec = parse_slack_file(path, doc_id)
        if rec:
            records.append(rec)
        else:
            skipped += 1

    print(f"[2] Parsed {len(records)} valid records ({skipped} skipped/empty)")

    if not records:
        print("No records to process. Exiting.")
        return

    # 3. Connect to DB
    print(f"\n[3] Connecting to DB ...")
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        ssl=False
    )
    await conn.execute(f"SET app.current_tenant_id = '{TENANT_UUID}'")
    print("    Connected.")

    # 4. Embed + insert in batches
    print(f"\n[4] Embedding {len(records)} records (batch={BATCH_SIZE}, concur={EMBED_CONCUR}) ...")
    total_inserted = 0
    sem = asyncio.Semaphore(EMBED_CONCUR)

    async def embed_record(session, rec):
        async with sem:
            rec["embedding"] = await embed_text(session, rec["cleaned_content"])
        return rec

    connector = aiohttp.TCPConnector(limit=EMBED_CONCUR)
    async with aiohttp.ClientSession(connector=connector) as session:
        for batch_start in range(0, len(records), BATCH_SIZE):
            batch = records[batch_start : batch_start + BATCH_SIZE]
            embedded = await asyncio.gather(*[embed_record(session, rec) for rec in batch])
            n = await bulk_insert(conn, embedded)
            total_inserted += n
            done = min(batch_start + BATCH_SIZE, len(records))
            print(f"    [{done}/{len(records)}] inserted {n} records ...")

    await conn.close()

    print(f"\n{'=' * 60}")
    print(f"  Ingestion complete: {total_inserted} chunks written to")
    print(f"  {SCHEMA}.vector_chunks")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
