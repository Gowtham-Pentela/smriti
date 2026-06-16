#!/usr/bin/env python3
"""
Asynchronous Data Ingestion Script
Fulfills EnterpriseRAG-Bench ingest pipeline requirements.
Parses, normalizes, embeds, and bulk inserts Slack logs into tenant_redwood_inference_prod.vector_chunks.
"""

import os
import sys
import json
import uuid
import datetime
import argparse
import asyncio
import aiohttp
import asyncpg

# Portable defaults — resolve relative to this script's parent directory
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DATA_DIR = os.path.join(_REPO_ROOT, "data", "slack_export")
DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_FALLBACK_URL = "http://localhost:11434/api/embeddings"
MODEL_NAME = "nomic-embed-text"

# Deterministic namespace UUID for tenant-redwood-inference-prod
TENANT_NAMESPACE_UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "tenant-redwood-inference-prod")


def parse_author(record):
    """
    Extracts author information from raw record.
    Returns a dict containing 'id', 'display_name', and 'email'.
    """
    # Check if author dictionary exists in record or nested metadata
    author_data = record.get("author") or record.get("metadata", {}).get("author")
    if author_data and isinstance(author_data, dict):
        return {
            "id": str(author_data.get("id") or author_data.get("user_id") or "unknown"),
            "display_name": str(author_data.get("display_name") or author_data.get("name") or "unknown"),
            "email": str(author_data.get("email") or "unknown")
        }
    
    # Fallback to participants list if available
    participants = record.get("participants", [])
    author_name = participants[0] if participants else "unknown"
    return {
        "id": author_name.lower(),
        "display_name": author_name,
        "email": f"{author_name.lower()}@company.com"
    }


def parse_timestamp(record):
    """
    Parses timestamp into ISO8601 string.
    Supports thread_ts (unix ts), created_at, or general timestamp.
    """
    raw_ts = (
        record.get("timestamp")
        or record.get("created_at")
        or record.get("thread_ts")
        or record.get("first_message_ts")
    )
    if not raw_ts:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Check if raw_ts is Unix epoch timestamp
    try:
        ts_float = float(raw_ts)
        dt = datetime.datetime.fromtimestamp(ts_float, datetime.timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        # Already formatted string or fallback
        return str(raw_ts)


async def stream_records(data_dir):
    """
    Memory-efficient generator that streams JSON/JSONL records from the data directory.
    """
    if not os.path.exists(data_dir):
        print(f"Error: Data directory '{data_dir}' does not exist.", file=sys.stderr)
        return

    for root, _, files in os.walk(data_dir):
        for name in files:
            if name.endswith(".json") or name.endswith(".jsonl"):
                filepath = os.path.join(root, name)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        # Inspect the first character to detect single JSON or JSONL format
                        first_char = f.read(1)
                        f.seek(0)
                        if first_char == "{":
                            # Check if the first line is a complete JSON object
                            first_line = f.readline()
                            try:
                                record = json.loads(first_line)
                                # Successfully parsed first line: treat as JSONL
                                yield record, filepath
                                for line in f:
                                    if line.strip():
                                        yield json.loads(line), filepath
                            except json.JSONDecodeError:
                                # JSONDecodeError means the entire file is a single JSON object
                                f.seek(0)
                                record = json.loads(f.read())
                                yield record, filepath
                        elif first_char:
                            # Standard JSON Lines processing for non-curly bracket starters
                            for line in f:
                                if line.strip():
                                    yield json.loads(line), filepath
                except Exception as e:
                    print(f"Warning: Failed to parse file {filepath}: {e}", file=sys.stderr)


async def get_embeddings_batch(session, texts):
    """
    Fetches embeddings in batch from local Ollama service.
    Falls back to single embeddings request if batch endpoint fails.
    """
    payload = {
        "model": MODEL_NAME,
        "input": texts
    }
    
    try:
        async with session.post(OLLAMA_EMBED_URL, json=payload, timeout=60) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("embeddings", [])
            else:
                print(f"Ollama /api/embed returned status {resp.status}, trying fallback...", file=sys.stderr)
    except Exception as e:
        print(f"Ollama /api/embed failed: {e}, trying fallback...", file=sys.stderr)

    # Fallback to /api/embeddings for each text block
    embeddings = []
    for text in texts:
        fallback_payload = {
            "model": MODEL_NAME,
            "prompt": text
        }
        try:
            async with session.post(OLLAMA_FALLBACK_URL, json=fallback_payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embeddings.append(data.get("embedding", []))
                else:
                    print(f"Ollama fallback returned status {resp.status}. Mocking zeros.", file=sys.stderr)
                    embeddings.append([0.0] * 768)
        except Exception as e:
            print(f"Ollama fallback embedding call failed: {e}. Mocking zeros.", file=sys.stderr)
            embeddings.append([0.0] * 768)
            
    return embeddings


async def ingest_pipeline(data_dir, db_url, batch_size):
    """
    Ingestion pipeline manager.
    Coordinates file walking, filtering, embedding generation, and bulk DB inserts.
    """
    print(f"Initializing Ingestion Pipeline...")
    print(f"Scanning directory: {data_dir}")
    print(f"Target Database URL: {db_url}")
    print(f"Batch size: {batch_size}")

    # Establish asyncpg connection
    try:
        conn = await asyncpg.connect(db_url, statement_cache_size=0)
        print("Connected to PostgreSQL successfully.")
    except Exception as e:
        print(f"Critical Error: Failed to connect to database: {e}", file=sys.stderr)
        return

    # Ingestion stats
    processed_count = 0
    skipped_count = 0
    batch_records = []

    # Initialize aiohttp session for Ollama requests
    async with aiohttp.ClientSession() as session:
        async for raw_record, filepath in stream_records(data_dir):
            # Normalization Filter Checkpoint
            source = raw_record.get("source", "").lower()
            source_type = raw_record.get("source_type", "").lower()
            
            # Explicit Slack filtering logic
            is_slack = (source == "slack") or (source_type == "slack") or ("/slack/" in filepath.lower())
            if not is_slack:
                skipped_count += 1
                continue

            # Map raw fields into standardized Common Event Schema
            event_id = uuid.uuid4()
            source_id = str(raw_record.get("dataset_doc_uuid") or raw_record.get("id") or raw_record.get("uuid") or event_id)
            author = parse_author(raw_record)
            timestamp = parse_timestamp(raw_record)
            thread_id = raw_record.get("thread_ts") or raw_record.get("thread_id")
            channel_or_space = raw_record.get("channel") or raw_record.get("channel_or_space") or "general"
            
            raw_content = raw_record.get("messages") or raw_record.get("content") or raw_record.get("text") or ""
            cleaned_content = str(raw_content).strip()

            # Skip empty records
            if not cleaned_content:
                skipped_count += 1
                continue

            allowed_groups = raw_record.get("allowed_groups") or []
            allowed_users = raw_record.get("allowed_users") or []
            is_public = raw_record.get("is_public", True)

            # Store the normalized record ready for embedding
            batch_records.append({
                "event_id": event_id,
                "tenant_id": TENANT_NAMESPACE_UUID,
                "source_id": source_id,
                "thread_id": thread_id,
                "source_type": "slack",
                "author_id": author["id"],
                "channel_or_space": channel_or_space,
                "content": cleaned_content,
                "allowed_groups": allowed_groups,
                "allowed_users": allowed_users,
                "is_public": is_public
            })

            # Process batch if limit reached
            if len(batch_records) >= batch_size:
                await process_and_insert_batch(conn, session, batch_records)
                processed_count += len(batch_records)
                batch_records = []

        # Process any remaining records
        if batch_records:
            await process_and_insert_batch(conn, session, batch_records)
            processed_count += len(batch_records)

    await conn.close()
    print(f"\n==================================================")
    print(f"Ingestion completed.")
    print(f"Processed Slack messages: {processed_count}")
    print(f"Skipped records:           {skipped_count}")
    print(f"==================================================")


async def process_and_insert_batch(conn, session, records):
    """
    Helper function to process embeddings and bulk insert one batch.
    """
    texts = [r["content"] for r in records]
    
    # Generate embeddings batch
    embeddings = await get_embeddings_batch(session, texts)
    
    # Re-map records into database insertion tuples
    insert_data = []
    for idx, r in enumerate(records):
        emb = embeddings[idx] if idx < len(embeddings) else ([0.0] * 768)
        # Convert floats to string format for simple casting via text -> vector
        emb_str = f"[{','.join(map(str, emb))}]"
        
        insert_data.append((
            r["event_id"],
            r["tenant_id"],
            r["source_id"],
            r["thread_id"],
            r["source_type"],
            r["author_id"],
            r["channel_or_space"],
            r["content"],
            emb_str,
            r["allowed_groups"],
            r["allowed_users"],
            r["is_public"]
        ))

    # Bulk Insert utilizing asyncpg executemany
    try:
        await conn.executemany("""
            INSERT INTO tenant_redwood_inference_prod.vector_chunks (
                event_id, tenant_id, source_id, thread_id, source_type, author_id, 
                channel_or_space, content, embedding, allowed_groups, allowed_users, is_public
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::text::vector, $10, $11, $12)
        """, insert_data)
        print(f"Successfully ingested batch of {len(records)} records.")
    except Exception as e:
        print(f"Error during bulk batch DB insert: {e}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Async Ingestion for EnterpriseRAG-Bench Slack logs.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Path to generated_data directory.")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="Database connection URL.")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for embedding & insert.")
    
    args = parser.parse_args()
    
    asyncio.run(ingest_pipeline(args.data_dir, args.db_url, args.batch_size))
