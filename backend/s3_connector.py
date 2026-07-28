"""
backend/s3_connector.py
───────────────────────
S3 EventBridge → SQS → backend worker.

Lifecycle:
  1. Long-polls an SQS queue for ObjectCreated events from the docs bucket.
  2. Downloads the object to a temp file.
  3. Routes by extension:
       .pdf / .docx / .txt / .md / .csv / .json / .yaml / code  → parser.parse_document()
       .mp4 / .mov / .mkv / .webm                                  → ffmpeg + Whisper
       .wav / .mp3 / .m4a / .flac / .ogg                           → Whisper directly
       .png / .jpg / .jpeg / .webp / .gif                          → vision LLM (llava/moondream)
  4. pg_ingest_chunks() → embeddings via Ollama → pgvector.
  5. delete_tenant_credentials → n/a, we just call ingestion_hashes for dedup.
  6. SQS: DeleteMessage on success, leave in flight on failure (SQS retries).

Idempotency is enforced at two levels:
  - ingestion_hashes table (per-tenant, per-file-hash) — short-circuits the embed.
  - S3 itself won't re-deliver after DeleteMessage.

Two run modes:
  - Production: long-polls SQS forever (called from main.py lifespan).
  - Local dev:  --local-folder <path> walks a directory once and exits.
                Lets you test ingestion without standing up AWS.
"""

import os
import sys
import json
import time
import asyncio
import hashlib
import tempfile
import subprocess
import datetime
from pathlib import Path
from typing import Any

import boto3
import asyncpg
import httpx
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from backend.parser import parse_document
from backend.transcription import transcribe_video, get_whisper_model
from backend.db import check_and_mark_ingested
from backend.auth import COMPANY_TENANT_ID

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
EMBED_MODEL      = os.getenv("SMRITI_EMBED_MODEL", "nomic-embed-text")


async def _ollama_embed(client: httpx.AsyncClient, text: str) -> list[float]:
    """nomic-embed-text expects a search_document: prefix when indexing."""
    try:
        resp = await client.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": f"search_document: {text}"},
        )
        if resp.status_code == 200:
            return resp.json().get("embedding", [])
    except httpx.RequestError as e:
        print(f"[s3] embed failed: {e}")
    return []

# ── Config ────────────────────────────────────────────────────────────────────

S3_BUCKET     = os.getenv("S3_BUCKET", "").strip()
AWS_REGION    = os.getenv("AWS_REGION", "us-east-1").strip()
S3_QUEUE_URL  = os.getenv("S3_QUEUE_URL", "").strip()
SMRITI_WHISPER_MODEL = os.getenv("SMRITI_WHISPER_MODEL", "tiny")  # tiny | base | small | medium | large

# Extensions
DOC_EXTS   = {".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".sql",
              ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".cpp", ".c", ".h", ".rs", ".sh"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALL_EXTS   = DOC_EXTS | VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

# Per-source status (mirrors the old _connector_status pattern in main.py)
_status: dict[str, Any] = {
    "is_running": False,
    "last_message_at": None,
    "recent": [],          # list of {key, status, chunks, error, ts}
    "queue_depth": 0,
    "errors": [],
}
_STATUS_MAX = 50


def _record_status(key: str, status: str, chunks: int = 0, error: str | None = None) -> None:
    entry = {
        "key": key,
        "status": status,
        "chunks": chunks,
        "error": error,
        "ts": datetime.datetime.now().isoformat(),
    }
    _status["recent"].insert(0, entry)
    _status["recent"] = _status["recent"][:_STATUS_MAX]
    if error:
        _status["errors"].insert(0, {"key": key, "error": error, "ts": entry["ts"]})
        _status["errors"] = _status["errors"][:_STATUS_MAX]
    _status["last_message_at"] = entry["ts"]


def get_status() -> dict:
    return dict(_status)


# ── SQS message parsing ───────────────────────────────────────────────────────

def _parse_s3_event(message_body: str) -> dict | None:
    """
    EventBridge → SQS messages can arrive in two shapes:
      1. Raw S3 event JSON (when EventBridge → SQS is wired with raw delivery)
      2. EventBridge envelope (when SQS receives the structured event)

    Returns {"bucket": str, "key": str} or None if not parseable.
    """
    try:
        body = json.loads(message_body)
    except json.JSONDecodeError:
        return None

    # Raw S3 event shape
    if "Records" in body:
        rec = body["Records"][0] if body["Records"] else {}
        s3 = rec.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        key    = s3.get("object", {}).get("key")
        if bucket and key:
            return {"bucket": bucket, "key": key}
        return None

    # EventBridge envelope shape
    detail = body.get("detail") or {}
    bucket = detail.get("bucket", {}).get("name")
    key    = detail.get("object", {}).get("key")
    if bucket and key:
        return {"bucket": bucket, "key": key}
    return None


# ── Chunk ingestion (the path shared with /ingest) ────────────────────────────

async def _ingest_chunks(
    db_pool: asyncpg.Pool,
    tenant_id: str,
    chunks: list[dict],
    source: str,
    file_hash: str,
    category: str | None = None,
) -> int:
    """
    Set the tenant context, embed + insert chunks, record dedup.

    `category` is free-form and stored on each chunk. The agent's list_files tool
    filters by it. The S3 worker derives it from the S3 key prefix
    (e.g. `compliance/kyc.md` → 'compliance'). The local seed script derives
    it from the folder name. Pass None to use the column default ('general').
    """
    if not chunks:
        return 0

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)

            # Check dedup — if the (tenant, file_hash) is already known, skip.
            already = await check_and_mark_ingested(
                conn, tenant_id, file_hash, source, chunks=len(chunks),
            )
            if already:
                return 0

            # Embed + insert. We do NOT call pg_ingest_chunks from main.py because
            # that helper expects its own pool — here we already have a connection
            # inside the tenant context, so we embed inline.
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                for chunk in chunks:
                    content = chunk.get("content", "")
                    if not content.strip():
                        continue
                    emb = await _ollama_embed(client, content)
                    if not emb:
                        continue
                    emb_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
                    await conn.execute(
                        """
                        INSERT INTO public.vector_chunks
                            (tenant_id, source, source_type, location, content, embedding, file_hash, category)
                        VALUES ($1, $2, $3, $4, $5, $6::text::vector, $7, $8)
                        """,
                        tenant_id,
                        source,
                        chunk.get("type", "document"),
                        chunk.get("location", ""),
                        content,
                        emb_str,
                        file_hash,
                        category or "general",
                    )
            return len(chunks)


# ── Version-tracking helpers ──────────────────────────────────────────────────
# Used by both the SQS event-driven path and the new list-and-diff sync loop.
# The shared routine is _ingest_one_key at the bottom of this block.

_key_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _acquire_key_lock(source: str) -> asyncio.Lock:
    """One lock per source. The SQS worker and sync loop both go through
    this so they cannot race on the same key. Lazily creates the lock."""
    async with _locks_guard:
        lock = _key_locks.get(source)
        if lock is None:
            lock = asyncio.Lock()
            _key_locks[source] = lock
        return lock


async def _delete_chunks_for_source(conn, source: str) -> int:
    """Wipe everything we know about a source: chunks, dedup row, manifest
    row. Idempotent. Returns the number of chunk rows removed."""
    chunks = await conn.fetchval(
        "DELETE FROM public.vector_chunks WHERE source = $1 RETURNING 1",
        source,
    )
    await conn.execute(
        "DELETE FROM public.ingestion_hashes WHERE source = $1",
        source,
    )
    await conn.execute(
        "DELETE FROM public.s3_manifest WHERE source_url = $1",
        source,
    )
    return int(chunks or 0)


async def _upsert_manifest(
    conn, *, source: str, bucket: str, s3_key: str,
    file_hash: str, etag: str, size_bytes: int,
) -> None:
    """Atomic single-statement upsert. Tenant context must already be set."""
    await conn.execute(
        """
        INSERT INTO public.s3_manifest
            (tenant_id, s3_key, bucket, source_url, file_hash, etag,
             size_bytes, last_seen_at, last_etag_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
        ON CONFLICT (tenant_id, s3_key) DO UPDATE
          SET file_hash    = EXCLUDED.file_hash,
              etag         = EXCLUDED.etag,
              size_bytes   = EXCLUDED.size_bytes,
              source_url   = EXCLUDED.source_url,
              last_seen_at = NOW(),
              last_etag_at = CASE
                  WHEN public.s3_manifest.etag IS DISTINCT FROM EXCLUDED.etag
                       THEN NOW()
                  ELSE public.s3_manifest.last_etag_at
              END
        """,
        COMPANY_TENANT_ID, s3_key, bucket, source, file_hash, etag, size_bytes,
    )


async def _list_manifest(conn) -> dict[str, dict]:
    """Read every row. Returns {s3_key: {file_hash, etag, size_bytes, bucket,
    last_seen_at, source_url}}."""
    rows = await conn.fetch(
        """
        SELECT s3_key, file_hash, etag, size_bytes, bucket,
               last_seen_at, source_url
        FROM public.s3_manifest
        """
    )
    return {
        r["s3_key"]: {
            "file_hash":   r["file_hash"],
            "etag":        r["etag"],
            "size_bytes":  r["size_bytes"],
            "bucket":      r["bucket"],
            "last_seen_at": r["last_seen_at"],
            "source_url":  r["source_url"],
        }
        for r in rows
    }


def _sha256_of_s3_object(s3, bucket: str, key: str) -> tuple[str, str, int]:
    """
    Returns (file_hash, etag, size_bytes).

    Cheapest path: head_object → ETag + size.
      - If ETag has no '-' (single-part PUT), ETag == MD5 of the bytes. We
        use the ETag hex as file_hash so the cheap diff is correct.
      - If ETag has '-' (multi-part), ETag is the MD5 of concatenated
        part-MD5s and is NOT a content hash. We fall back to GET and
        stream-hash the body.

    `file_hash` is therefore "SHA-256 for multi-part, ETag-as-MD5 for
    single-part". Documented on the s3_manifest.file_hash column.
    """
    head = s3.head_object(Bucket=bucket, Key=key)
    etag = head["ETag"].strip('"')
    size = int(head["ContentLength"])
    if "-" not in etag:
        return etag, etag, size
    h = hashlib.sha256()
    obj = s3.get_object(Bucket=bucket, Key=key)
    for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
        h.update(chunk)
    return h.hexdigest(), etag, size


def _compute_diff(
    current: dict[str, dict],
    manifest: dict[str, dict],
) -> tuple[set[str], set[str], set[str]]:
    """
    Returns (new_keys, changed_keys, deleted_keys).

      new      = in current, not in manifest
      changed  = in both, etag or file_hash differs
      deleted  = in manifest, not in current
    """
    new_keys     = set(current)  - set(manifest)
    deleted_keys = set(manifest) - set(current)
    changed_keys = {
        k for k in (set(current) & set(manifest))
        if current[k]["etag"]      != manifest[k]["etag"]
        or current[k]["file_hash"] != manifest[k]["file_hash"]
    }
    return new_keys, changed_keys, deleted_keys


async def _ingest_one_key(
    s3, *, bucket: str, key: str, db_pool: asyncpg.Pool, tenant_id: str,
) -> None:
    """
    Shared "process this one S3 key" routine. Used by both the SQS
    event-driven path and the list-and-diff sync loop.

    Algorithm:
      1. Acquire per-source lock (so SQS and sync don't race on the same key).
      2. head_object → ETag + size (cheap, no download).
      3. Compare with manifest. If unchanged, touch last_seen_at, exit.
      4. Otherwise delete the old chunks + dedup row + manifest row.
      5. Download + parse + _ingest_chunks (outside any transaction).
      6. Upsert manifest.
    """
    source = f"s3://{bucket}/{key}"
    lock = await _acquire_key_lock(source)
    async with lock:
        # 1. Cheap hash via head_object (or streaming GET for multi-part).
        try:
            file_hash, etag, size = await asyncio.to_thread(
                _sha256_of_s3_object, s3, bucket, key,
            )
        except ClientError as e:
            _record_status(key, "failed", error=f"head_object: {e}")
            return

        # 2. Compare with the manifest.
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    tenant_id,
                )
                known = await conn.fetchrow(
                    "SELECT file_hash, etag FROM public.s3_manifest "
                    "WHERE s3_key = $1",
                    key,
                )
                if (
                    known
                    and known["etag"] == etag
                    and known["file_hash"] == file_hash
                ):
                    await conn.execute(
                        "UPDATE public.s3_manifest SET last_seen_at = NOW() "
                        "WHERE s3_key = $1",
                        key,
                    )
                    _record_status(key, "unchanged", chunks=0)
                    return

                # 3. New or changed: wipe the old state.
                await _delete_chunks_for_source(conn, source)

        # 4. Download + parse + ingest. (Outside the DB transaction because
        #    Ollama calls block on the network.)
        try:
            chunks = await asyncio.to_thread(_download_and_chunk, s3, bucket, key)
        except ClientError as e:
            _record_status(key, "failed", error=f"get_object: {e}")
            return
        except ValueError as e:
            _record_status(key, "skipped", error=str(e))
            return

        category = key.split("/", 1)[0] if "/" in key else None
        n = await _ingest_chunks(
            db_pool, tenant_id, chunks, source, file_hash, category=category,
        )

        # 5. Upsert the manifest row.
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    tenant_id,
                )
                await _upsert_manifest(
                    conn, source=source, bucket=bucket, s3_key=key,
                    file_hash=file_hash, etag=etag, size_bytes=size,
                )
        _record_status(key, "ok", chunks=n)


# ── File-type routing ─────────────────────────────────────────────────────────

def _route_and_chunk(local_path: str, key: str) -> list[dict]:
    ext = Path(key).suffix.lower()

    if ext in DOC_EXTS:
        return parse_document(local_path, source_name=key)

    if ext in IMAGE_EXTS:
        # parse_document already handles images; delegate to it
        return parse_document(local_path, source_name=key)

    if ext in VIDEO_EXTS:
        return transcribe_video(local_path, source_name=key)

    if ext in AUDIO_EXTS:
        # Whisper on a direct audio file — synthesise a "video-style" wrapper
        return _transcribe_audio(local_path, key)

    return []


def _transcribe_audio(audio_path: str, source_name: str) -> list[dict]:
    """Transcribe a standalone audio file (mp3, wav, m4a, …) with Whisper."""
    model = get_whisper_model()
    result = model.transcribe(audio_path, beam_size=1)
    chunks: list[dict] = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        s, e = seg.get("start", 0), seg.get("end", 0)
        mm, ss = int(s // 60), int(s % 60)
        em, es = int(e // 60), int(e % 60)
        chunks.append({
            "source":   source_name,
            "type":     "audio",
            "location": f"Timestamp {mm:02d}:{ss:02d} - {em:02d}:{es:02d}",
            "content":  text,
        })
    return chunks


# ── Single-object handler ─────────────────────────────────────────────────────

def _download_and_chunk(s3_client, bucket: str, key: str) -> list[dict]:
    """
    Synchronous: download the S3 object to a temp file, route to the
    right parser, return the chunks. Caller is responsible for any
    transaction orchestration.

    Raises ClientError on s3:GetObject failure.
    Returns [] if no extractable text (caller should record "empty").
    """
    ext = Path(key).suffix.lower()
    if ext and ext not in ALL_EXTS:
        raise ValueError(f"unsupported extension {ext}")
    with tempfile.NamedTemporaryFile(suffix=ext or "", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        with open(tmp_path, "wb") as f:
            for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
                f.write(chunk)
        return _route_and_chunk(tmp_path, key)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def _process_s3_object(
    s3_client,
    bucket: str,
    key: str,
    db_pool: asyncpg.Pool,
    tenant_id: str,
) -> None:
    """
    SQS event-driven entry point. Delegates to the shared _ingest_one_key
    so the SQS path and the sync loop cannot diverge.
    """
    try:
        await _ingest_one_key(
            s3_client, bucket=bucket, key=key, db_pool=db_pool, tenant_id=tenant_id,
        )
    except Exception as e:
        # SQS expects the exception to bubble so the message stays in
        # flight and gets redelivered. _ingest_one_key already records
        # "failed" / "skipped" in the status dict.
        raise


# ── SQS long-poll loop (production) ───────────────────────────────────────────

def _sqs_client():
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        config=BotoConfig(
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=5,
            read_timeout=20,
        ),
    )


def _s3_client():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        ),
    )


async def start_worker(db_pool: asyncpg.Pool) -> None:
    """
    Long-lived task. Reads SQS messages, processes them, deletes on success.
    """
    if not S3_BUCKET or not S3_QUEUE_URL:
        print("[s3] S3_BUCKET or S3_QUEUE_URL not set — S3 worker disabled.")
        return

    print(f"[s3] worker starting. bucket={S3_BUCKET} queue={S3_QUEUE_URL}")
    sqs = _sqs_client()
    s3 = _s3_client()
    _status["is_running"] = True
    tenant_id = COMPANY_TENANT_ID

    try:
        while True:
            try:
                resp = sqs.receive_message(
                    QueueUrl=S3_QUEUE_URL,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,            # long poll
                    VisibilityTimeout=600,         # 10 min per message
                    MessageAttributeNames=["All"],
                )
            except ClientError as e:
                print(f"[s3] receive_message failed: {e}")
                await asyncio.sleep(5)
                continue

            msgs = resp.get("Messages", [])
            for m in msgs:
                info = _parse_s3_event(m.get("Body", ""))
                if not info:
                    # Unparseable — delete so we don't loop forever.
                    sqs.delete_message(QueueUrl=S3_QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
                    _record_status("<unknown>", "skipped", error="unparseable SQS body")
                    continue

                try:
                    await _process_s3_object(s3, info["bucket"], info["key"], db_pool, tenant_id)
                    sqs.delete_message(QueueUrl=S3_QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
                except Exception as e:
                    # Don't delete — SQS will re-deliver after VisibilityTimeout.
                    print(f"[s3] processing failed for {info['key']}: {e}")
                    # Optionally: change_visibility to short re-delivery

            # Update queue depth opportunistically
            try:
                attrs = sqs.get_queue_attributes(
                    QueueUrl=S3_QUEUE_URL,
                    AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
                )
                _status["queue_depth"] = int(
                    attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0)
                )
            except ClientError:
                pass

    except asyncio.CancelledError:
        print("[s3] worker stopped (cancel).")
        _status["is_running"] = False
        raise


# ── S3 sync loop (list + diff) ────────────────────────────────────────────────
S3_SYNC_INTERVAL_MIN = int(os.getenv("S3_SYNC_INTERVAL_MIN", "10"))


async def start_sync_loop(db_pool: asyncpg.Pool) -> None:
    """
    Long-lived reconciliation task. Every S3_SYNC_INTERVAL_MIN minutes:
      1. list_objects_v2 the bucket
      2. head_object each key for the ETag (cheap)
      3. diff against s3_manifest
      4. apply deletes (cheap, no I/O)
      5. route new/changed through _ingest_one_key
    Runs alongside start_worker. Gated on S3_BUCKET only — having a bucket
    without a queue is fine (dev mode).
    """
    if not S3_BUCKET:
        print("[s3.sync] S3_BUCKET not set — sync loop disabled.")
        _status["sync_enabled"] = False
        return

    s3 = _s3_client()
    tenant_id = COMPANY_TENANT_ID
    _status["sync_enabled"] = True
    print(f"[s3.sync] starting. bucket={S3_BUCKET} every={S3_SYNC_INTERVAL_MIN}m")

    try:
        while True:
            try:
                await _sync_once(s3, db_pool, tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[s3.sync] cycle failed: {e}")
                _record_status("<sync>", "failed", error=str(e))
            await asyncio.sleep(S3_SYNC_INTERVAL_MIN * 60)
    except asyncio.CancelledError:
        print("[s3.sync] stopped (cancel).")
        _status["sync_enabled"] = False
        raise


async def _sync_once(s3, db_pool: asyncpg.Pool, tenant_id: str) -> None:
    """One reconciliation pass. Idempotent — safe to run as often as you like."""
    started = time.time()

    # 1. List bucket, collect {s3_key: {file_hash, etag, size_bytes}} for every
    #    supported extension. Skips the rest.
    current: dict[str, dict] = {}
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get("Contents", []) or []:
                k = obj["Key"]
                if Path(k).suffix.lower() not in ALL_EXTS:
                    continue
                try:
                    fh, et, sz = await asyncio.to_thread(
                        _sha256_of_s3_object, s3, S3_BUCKET, k,
                    )
                except ClientError as e:
                    _record_status(k, "failed", error=f"sync head/get: {e}")
                    continue
                current[k] = {"file_hash": fh, "etag": et, "size_bytes": sz}
    except ClientError as e:
        # Bucket not found, auth failure, etc.
        _record_status("<sync>", "failed", error=f"list_objects_v2: {e}")
        return

    # 2. Read the manifest.
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", tenant_id,
            )
            manifest = await _list_manifest(conn)

    # 3. Diff.
    new_keys, changed_keys, deleted_keys = _compute_diff(current, manifest)

    # 4. Apply deletes first (cheap, no I/O).
    for k in sorted(deleted_keys):
        source = f"s3://{S3_BUCKET}/{k}"
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    tenant_id,
                )
                n = await _delete_chunks_for_source(conn, source)
        _record_status(k, "deleted", chunks=n)

    # 5. Apply new + changed via the shared routine.
    for k in sorted(new_keys | changed_keys):
        try:
            await _ingest_one_key(
                s3, bucket=S3_BUCKET, key=k, db_pool=db_pool, tenant_id=tenant_id,
            )
        except Exception as e:
            print(f"[s3.sync] ingest failed for {k}: {e}")
            _record_status(k, "failed", error=str(e))

    summary = {
        "ts":          datetime.datetime.now().isoformat(),
        "duration_s":  round(time.time() - started, 2),
        "scanned":     len(current),
        "new":         sorted(new_keys),
        "changed":     sorted(changed_keys),
        "deleted":     sorted(deleted_keys),
    }
    _status["last_sync"] = summary
    print(f"[s3.sync] cycle: scanned={summary['scanned']} new={len(new_keys)} "
          f"changed={len(changed_keys)} deleted={len(deleted_keys)} "
          f"in {summary['duration_s']}s")


# ── Local-folder mode (for testing without AWS) ──────────────────────────────

async def ingest_local_folder(folder: str, db_pool: asyncpg.Pool) -> dict:
    """
    Walk `folder`, ingest every supported file. Returns a summary.
    """
    tenant_id = COMPANY_TENANT_ID
    s3 = None  # we don't go through S3 in this path
    summary = {"ok": 0, "skipped": 0, "failed": 0, "files": []}
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return {"error": f"folder not found: {folder}", **summary}

    for p in sorted(folder_path.rglob("*")):
        if not p.is_file():
            continue
        # Skip files that aren't user-authored knowledge content. `__init__.py`
        # and `seed.py` exist for the Python package — they aren't documents
        # an employee would ask about.
        if p.name.startswith("__") or p.name in {"seed.py"}:
            continue
        ext = p.suffix.lower()
        if ext not in ALL_EXTS:
            continue
        key = str(p.relative_to(folder_path))
        # Category from the folder name: `compliance/kyc.md` → 'compliance'.
        category = key.split("/", 1)[0] if "/" in key else None
        try:
            chunks = await asyncio.to_thread(_route_and_chunk, str(p), key)
            if not chunks:
                summary["skipped"] += 1
                summary["files"].append({"key": key, "status": "empty"})
                continue
            with open(p, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
            source = f"local://{key}"
            n = await _ingest_chunks(db_pool, tenant_id, chunks, source, h, category=category)
            summary["ok"] += 1
            summary["files"].append({"key": key, "status": "ok", "chunks": n})
        except Exception as e:
            summary["failed"] += 1
            summary["files"].append({"key": key, "status": "failed", "error": str(e)})
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Smriti S3 ingestion worker")
    p.add_argument("--local-folder", help="Run once over a local folder (no AWS)")
    args = p.parse_args()

    if args.local_folder:
        # Boot a pool and run once
        from backend.db import SCHEMA  # noqa: F401  (import-time sanity)
        async def _run():
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4, statement_cache_size=0)
            try:
                result = await ingest_local_folder(args.local_folder, pool)
                print(json.dumps(result, indent=2))
            finally:
                await pool.close()
        asyncio.run(_run())
    else:
        print("Production worker is started from main.py's lifespan — use --local-folder for testing.")


if __name__ == "__main__":
    main()
