import os

# Load .env before anything else — handles "KEY = VALUE" spacing that shell
# `source` can't parse. This ensures GOOGLE_CLIENT_ID, SLACK_CLIENT_ID, etc.
# are always available regardless of how uvicorn was launched.
from dotenv import load_dotenv
load_dotenv(override=False)  # override=False: env vars already set in shell take priority

import glob
import asyncio
import hashlib
import json
import re
import time
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import asyncpg
import uuid
import tempfile
import shutil

# ─── File Hash Utility ───────────────────────────────────────────────────────

def get_file_hash(file_path: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error hashing file {file_path}: {e}")
        return ""

# ─── Audit Trail Logging ─────────────────────────────────────────────────────

def write_audit_log(user_email: str, query: str, accessed_files: list):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audit_file = os.path.join(base_dir, "data", "audit_log.json")

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "user_email": user_email,
        "query": query,
        "accessed_files": list(set(accessed_files)),
    }

    logs = []
    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []

    logs.append(log_entry)

    try:
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        print(f"Failed to write audit log: {e}")

# ─── Local imports ────────────────────────────────────────────────────────────

from backend.parser import parse_document
from backend.transcription import transcribe_video
from backend.grounding import validate_response, extract_citations, MODEL_NAME
from backend import slack_connector
from backend import gdrive_connector as _gdrive_connector
from backend import confluence_connector as _confluence_connector
from backend.db import check_and_mark_ingested, save_tenant_credentials, load_tenant_credentials
from backend.auth import get_current_user, UserIdentity
from backend import sync_scheduler
from backend import slack_oauth as _slack_oauth
from backend import gdrive_oauth as _gdrive_oauth
from backend.doc_classifier import classify_document


DB_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
TENANT_NAMESPACE_UUID = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
TENANT_UUID = uuid.UUID(TENANT_NAMESPACE_UUID)

MODEL_NAME_EMBED = "nomic-embed-text"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"   # chat API, proper system/user roles

COMMON_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "cannot", "could", "did", "do", "does",
    "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has",
    "have", "having", "here", "how", "i", "if", "in", "into", "is", "it", "its",
    "just", "me", "more", "most", "no", "nor", "not", "of", "off", "on", "once",
    "only", "or", "other", "our", "out", "over", "own", "same", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "with", "would", "you", "your", "yours",
}

# ─── File hash cache (legacy — /index-folder only) ──────────────────────────
# Connectors use the Postgres ingestion_hashes table instead (crash-safe).
# This dict remains for the local /index-folder path only.
_file_hash_cache: Dict[str, str] = {}

# ─── Connector ingestion status (shared across all connector types) ───────────
_connector_status: Dict[str, Any] = {
    "is_running": False,
    "connector": None,
    "ingested": 0,
    "skipped": 0,
    "errors": [],
    "message": "idle",
}

# ─── Indexing State ───────────────────────────────────────────────────────────

indexing_status = {
    "is_indexing": False,
    "progress": 0,
    "current_file": "",
    "total_files": 0,
    "indexed_files": [],
    "elapsed_time": 0,
    "total_time": 0,
}
cancel_indexing_flag = False

# Per-tenant sync lock — prevents concurrent sync_all_tenants() for the same tenant.
# Keys: tenant_id string. Value: True while a sync is in progress.
_active_syncs: Dict[str, bool] = {}


# ─── App Setup (lifespan replaces deprecated on_event) ──────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect DB pool, wire auth, start sync scheduler, warm Ollama."""

    # ── SAFETY: refuse to start with dev mode ON in production ────────────────
    _is_dev_mode = os.getenv("KGF_DEV_MODE", "").lower() == "true"
    _env_name    = os.getenv("KGF_ENV", "local").lower()
    if _is_dev_mode and _env_name not in ("local", "dev", "development"):
        raise RuntimeError(
            "SECURITY: KGF_DEV_MODE=true is not allowed outside local/dev environments. "
            "Set KGF_ENV=local only for local development. "
            "Remove KGF_DEV_MODE from production environment variables."
        )

    # ── DB pool ─────────────────────────────────────────────────────────────────
    print("Initializing connection pool → Postgres...")
    try:
        app.state.db_pool = await asyncpg.create_pool(DB_URL, min_size=5, max_size=20)
        print("🚀 DB connection pool established.")
    except Exception as e:
        print(f"❌ Critical: DB pool failed: {e}")
        import sys; sys.exit(1)

    # Wire the DB pool into the auth dependency (avoids circular import)
    get_current_user._db_pool = app.state.db_pool

    # ── Ollama warmup ───────────────────────────────────────────────────────────
    # Send a warm-up embedding request so the model is loaded before the first
    # real query. Prevents the 30-60s cold-start timeout during a live demo.
    # Runs as a background task so it doesn't block server startup.
    async def _warmup_ollama():
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    OLLAMA_EMBED_URL,
                    json={"model": MODEL_NAME_EMBED, "prompt": "warmup"},
                    timeout=60.0,
                )
            print("🔥 Ollama model warm-up complete.")
        except Exception as e:
            print(f"⚠️  Ollama warm-up failed (non-fatal): {e}")
            print("    Run: ollama pull nomic-embed-text && ollama serve")

    _warmup_task = asyncio.create_task(_warmup_ollama())

    # ── Background sync loop ───────────────────────────────────────────────────────
    _sync_task = asyncio.create_task(
        sync_scheduler.start_sync_loop(app.state.db_pool)
    )

    yield  # ← app is running here

    # Shutdown
    _sync_task.cancel()
    _warmup_task.cancel()
    try:
        await _sync_task
    except asyncio.CancelledError:
        pass
    await app.state.db_pool.close()
    print("🛑 DB connection pool closed.")



app = FastAPI(
    title="Knowledge Guardian Foundry — KGF",
    description="Privacy-first organizational AI assistant.",
    lifespan=lifespan,
)

# ─── Static frontend (served at /app/) ───────────────────────────────────────
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(_frontend_dir):
    # ── No-cache middleware for frontend files ──────────────────────────────
    # Prevents browsers from serving stale JS/HTML after deploys.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            response = await call_next(request)
            path = request.url.path
            if path.startswith('/app/') or path == '/app':
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma']        = 'no-cache'
                response.headers['Expires']       = '0'
            return response

    app.add_middleware(NoCacheMiddleware)
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

@app.get("/", include_in_schema=False)
async def _root_redirect():
    """Redirect bare domain to landing page."""
    return RedirectResponse(url="/app/landing.html")


@app.get("/auth-config", include_in_schema=False)
async def get_auth_config():
    """
    Public endpoint — no auth required.
    Tells the frontend whether the backend is running in dev mode.
    When dev_mode=true the frontend skips the Supabase sign-in gate
    and the backend accepts requests without a Bearer token.
    """
    from backend.auth import KGF_DEV_MODE
    dev_email = os.getenv("KGF_DEV_USER_EMAIL", "dev@localhost.local")
    return {
        "dev_mode":  KGF_DEV_MODE,
        "dev_email": dev_email if KGF_DEV_MODE else None,
    }



# ─── CORS (restrict wildcard in prod via CORS_ORIGINS env var) ──────────────────
_ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Dev-User-Email"],
)

# ─── Request Models ───────────────────────────────────────────────────────────

class IndexRequest(BaseModel):
    folder_path: str

class ChatMessage(BaseModel):
    role:            str                 # "user" or "assistant"
    content:         str

class QueryRequest(BaseModel):
    query:           str
    top_k:           int = 6             # number of chunks to retrieve (capped at 10 in route)
    category_filter: Optional[str] = None  # e.g. "deployment", "requirements"
    history:         Optional[List[ChatMessage]] = None


def _sanitize(text: str) -> str:
    """Strip control characters that break JSON serialization.

    Removes ASCII control characters (0x00-0x1f) EXCEPT the three that
    json.dumps handles natively: tab (0x09), newline (0x0a), and CR (0x0d).
    Slack message content can contain null bytes and other control chars
    from copy-pasted terminal output or malformed encodings.
    """
    if not text:
        return text
    import re as _re
    # Strip everything in 0x00-0x1f except \t (0x09), \n (0x0a), \r (0x0d)
    return _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

class IngestSlackRequest(BaseModel):
    bot_token:   str
    channel_ids: List[str]
    days_back:   int  = 90
    save_token:  bool = False

class ConfluenceConnectRequest(BaseModel):
    confluence_url: str
    email:   str
    api_token: str

class OrgInviteRequest(BaseModel):
    email: str
    role:  str

class OrgInfoPatchRequest(BaseModel):
    company_name: str

# ─── Async Ollama Embedding ───────────────────────────────────────────────────

async def get_async_ollama_embedding(query_text: str) -> list:
    """Non-blocking embedding call with 3-attempt retry and exponential backoff.

    Raises HTTPException(503) on total failure instead of silently returning
    a zero vector — a zero vector fed to pgvector's cosine operator causes a
    division-by-zero crash in Postgres and a misleading 500 to the client.
    """
    import asyncio
    last_exc: Exception = RuntimeError("unknown")

    for attempt in range(1, 4):  # attempts 1, 2, 3
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    OLLAMA_EMBED_URL,
                    json={"model": MODEL_NAME_EMBED, "prompt": query_text},
                    timeout=30.0,  # raised from 15s; model cold-start can take ~20s
                )
                response.raise_for_status()
                data = response.json()
                if "embedding" in data:
                    return data["embedding"]
                raise KeyError("'embedding' key missing from Ollama response.")
        except Exception as e:
            last_exc = e
            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
            print(f"⚠️  Ollama embedding attempt {attempt}/3 failed: {e}. Retrying in {wait}s...")
            if attempt < 3:
                await asyncio.sleep(wait)

    # All retries exhausted — surface a clean 503 to the caller
    raise HTTPException(
        status_code=503,
        detail=(
            f"Ollama embedding service unavailable after 3 attempts: {last_exc}. "
            "Ensure 'ollama serve' is running and the nomic-embed-text model is pulled."
        ),
    )

# ─── Postgres Ingestion Helper ────────────────────────────────────────────────

async def pg_ingest_chunks(chunks: list, db_pool: asyncpg.Pool, tenant_id: str | None = None):
    """
    Embeds and bulk-inserts parsed document chunks into vector_chunks.
    Replaces the old LocalVectorStore.add_chunks() path for /index-folder.

    tenant_id: the per-user silo key (Supabase user_id UUID). If None,
               falls back to the global constant (legacy/dev path only).
    """
    if not chunks:
        return

    # Resolve effective tenant
    effective_tenant_id  = tenant_id or TENANT_NAMESPACE_UUID
    try:
        effective_tenant_uuid = uuid.UUID(effective_tenant_id)
    except ValueError:
        effective_tenant_uuid = TENANT_UUID

    print(f"  → Embedding & inserting {len(chunks)} chunks into Postgres...")
    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            try:
                resp = await client.post(
                    OLLAMA_EMBED_URL,
                    json={"model": MODEL_NAME_EMBED, "prompt": chunk["content"]},
                    timeout=20.0,
                )
                resp.raise_for_status()
                emb = resp.json().get("embedding", [0.0] * 768)
            except Exception as e:
                print(f"  ⚠ Embedding failed for chunk from {chunk.get('source')}: {e}")
                emb = [0.0] * 768

            emb_str = f"[{','.join(map(str, emb))}]"
            event_id = uuid.uuid4()
            source_id = chunk.get("source", str(event_id))
            source_type = chunk.get("type", "document")
            channel_or_space = chunk.get("location", "local")
            content = chunk.get("content", "")
            author_id = chunk.get("author_id") or chunk.get("author") or "system"
            document_title = chunk.get("title") or chunk.get("section") or source_id

            try:
                async with db_pool.acquire() as conn:
                    # CRITICAL FIX: SET LOCAL inside a transaction prevents RLS
                    # tenant context from leaking to the next connection pool user.
                    async with conn.transaction():
                        await conn.execute(
                            f"SET LOCAL app.current_tenant_id = '{effective_tenant_id}'"
                        )
                        await conn.execute(
                            """
                            INSERT INTO tenant_redwood_inference_prod.vector_chunks
                                (event_id, tenant_id, source_id, source_type,
                                 author_id, channel_or_space, content, embedding,
                                 allowed_groups, allowed_users, is_public,
                                 document_title)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text::vector,
                                    $9, $10, $11, $12)
                            ON CONFLICT DO NOTHING
                            """,
                            event_id,
                            effective_tenant_uuid,
                            source_id,
                            source_type,
                            author_id,
                            channel_or_space,
                            content,
                            emb_str,
                            [],
                            [],
                            True,
                            document_title,
                        )
            except Exception as e:
                print(f"  ⚠ DB insert failed for chunk {source_id}: {e}")

# ─── Background Indexing Task ─────────────────────────────────────────────────

# run_indexing sync wrapper removed — indexing now uses asyncio.create_task()
# to run on the SAME event loop as the asyncpg connection pool.


async def _async_run_indexing(folder_path: str, db_pool: asyncpg.Pool, tenant_id: str | None = None):
    global indexing_status, cancel_indexing_flag, _file_hash_cache

    indexing_status.update({
        "is_indexing": True,
        "progress": 0,
        "current_file": "",
        "indexed_files": [],
        "elapsed_time": 0,
        "total_time": 0,
    })
    cancel_indexing_flag = False

    import time
    start_time = time.time()

    try:
        doc_exts = {
            ".pdf", ".txt", ".md", ".markdown", ".json",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
            ".java", ".go", ".cpp", ".c", ".h", ".rs", ".sh",
            ".yaml", ".yml", ".sql",
        }
        video_exts = {".mp4", ".mkv", ".avi", ".mov"}

        # Exact filenames that are never useful for RAG — lock files, minified assets
        skip_filenames = {
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "Cargo.lock", "poetry.lock", "Gemfile.lock", "composer.lock",
            "bun.lockb",
        }
        skip_dirs = {
            ".git", "node_modules", "dist", "build", ".next", ".nuxt",
            ".venv", "venv", "env", ".gemini", "__pycache__",
            ".cache", "coverage", ".vercel", ".turbo",
        }
        # Size limits:
        # - PDFs are parsed page-by-page (streaming) → allow up to 200 MB
        # - Code/text files load fully into memory → keep 150 KB cap
        MAX_FILE_BYTES_DEFAULT = 150_000        # 150 KB for text/code
        MAX_FILE_BYTES_PDF     = 200 * 1024 * 1024  # 200 MB for PDFs

        all_files = []
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                if file in skip_filenames:
                    continue
                # Skip *.min.js, *.min.css, *.bundle.js etc.
                if re.search(r'\.(min|bundle|chunk)\.(js|css)$', file, re.IGNORECASE):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in doc_exts or ext in video_exts:
                    full_path = os.path.join(root, file)
                    try:
                        fsize = os.path.getsize(full_path)
                        # PDFs get a generous 200 MB limit (page-by-page parsing)
                        limit = MAX_FILE_BYTES_PDF if ext == '.pdf' else MAX_FILE_BYTES_DEFAULT
                        if fsize > limit:
                            print(f"  ⏭ Skipping oversized file: {file} ({fsize//1024}KB, limit {limit//1024}KB)")
                            continue
                    except OSError:
                        continue
                    all_files.append(full_path)


        indexing_status["total_files"] = len(all_files)
        if not all_files:
            print("No indexable files found.")
            return

        print(f"Found {len(all_files)} files to index in {folder_path}.")

        for idx, file_path in enumerate(all_files):
            if cancel_indexing_flag:
                print("Indexing cancelled by user.")
                break

            source_name = os.path.relpath(file_path, folder_path)
            file_hash = get_file_hash(file_path)
            file_size = os.path.getsize(file_path)

            ext = os.path.splitext(file_path)[1].lower()
            # Skip large non-PDF, non-video files > 1 MB
            # PDFs are handled by the per-file limit above and parsed page-by-page
            if ext not in video_exts and ext != '.pdf' and file_size > 1024 * 1024:
                indexing_status["progress"] = int((idx / len(all_files)) * 100)
                continue

            # Skip unchanged files
            if _file_hash_cache.get(source_name) == file_hash:
                indexing_status["progress"] = int((idx / len(all_files)) * 100)
                continue

            indexing_status["current_file"] = source_name
            indexing_status["progress"] = int((idx / len(all_files)) * 100)
            indexing_status["elapsed_time"] = int(time.time() - start_time)

            chunks = []
            if ext in video_exts:
                chunks = transcribe_video(file_path, source_name=source_name)
            else:
                chunks = parse_document(file_path, source_name=source_name)

            if chunks:
                await pg_ingest_chunks(chunks, db_pool, tenant_id=tenant_id)
                _file_hash_cache[source_name] = file_hash
                indexing_status["indexed_files"].append(source_name)

        indexing_status["progress"] = 100
        indexing_status["total_time"] = int(time.time() - start_time)
        print(f"Indexing completed in {indexing_status['total_time']}s.")

    except Exception as e:
        print(f"Error during indexing: {e}")
    finally:
        indexing_status["is_indexing"] = False

# ─── Graph Expert Helper ──────────────────────────────────────────────────────

async def fetch_top_experts(conn: asyncpg.Connection, top_n: int = 3) -> list:
    """
    Query graph_nodes + graph_edges to surface the most active experts
    by total accumulated interaction weight.
    """
    try:
        rows = await conn.fetch(
            """
            SELECT gn.display_name, SUM(ge.weight) AS total_weight
            FROM tenant_redwood_inference_prod.graph_edges ge
            JOIN tenant_redwood_inference_prod.graph_nodes gn
                ON gn.node_id = ge.source_id
            WHERE gn.node_type = 'person'
            GROUP BY gn.display_name
            ORDER BY total_weight DESC
            LIMIT $1
            """,
            top_n,
        )
        return [
            {"name": r["display_name"], "score": round(float(r["total_weight"]), 2)}
            for r in rows
        ]
    except Exception as e:
        print(f"Graph expert query failed (non-critical): {e}")
        return []

# ─── API Routes ───────────────────────────────────────────────────────────────

@app.post("/index-folder")
async def index_folder(
    req: IndexRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Kick off folder indexing on the SAME event loop as the asyncpg pool.
    Requires authentication so documents are stored in the calling user's
    private data silo (per-user tenant_id).
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Could not resolve user tenant.")
    if not os.path.exists(req.folder_path):
        raise HTTPException(status_code=404, detail="Folder path does not exist.")
    if indexing_status["is_indexing"]:
        raise HTTPException(status_code=400, detail="Indexing already in progress.")
    asyncio.create_task(_async_run_indexing(req.folder_path, app.state.db_pool, tenant_id=tenant_id))
    return {"status": "success", "message": "Indexing started in background."}


@app.get("/indexing-progress")
def get_indexing_progress():
    return indexing_status


@app.post("/cancel-indexing")
def cancel_indexing():
    global cancel_indexing_flag
    if indexing_status["is_indexing"]:
        cancel_indexing_flag = True
        return {"status": "success", "message": "Cancellation request received."}
    return {"status": "ignored", "message": "No active indexing task."}


# ─── Slack Live Connector ─────────────────────────────────────────────────────

class IngestDriveRequest(BaseModel):
    """Request body for /ingest-gdrive."""
    folder_id: str | None = None  # None = ingest entire Drive


async def _run_slack_ingest(req: IngestSlackRequest):
    """Background coroutine: pull Slack history, dedup, embed, insert."""
    global _connector_status
    _connector_status.update({
        "is_running": True,
        "connector": "slack",
        "ingested": 0,
        "skipped": 0,
        "errors": [],
        "message": "running",
    })
    try:
        # Optionally persist the token encrypted in tenant_credentials
        if req.save_token:
            async with app.state.db_pool.acquire() as conn:
                await conn.execute(
                    f"SET app.current_tenant_id = '{TENANT_NAMESPACE_UUID}'"
                )
                await save_tenant_credentials(
                    conn=conn,
                    tenant_id=TENANT_NAMESPACE_UUID,
                    source="slack",
                    token_dict={"bot_token": req.bot_token},
                    scopes=[],
                )

        result = await slack_connector.ingest_from_slack(
            bot_token=req.bot_token,
            channel_ids=req.channel_ids,
            db_pool=app.state.db_pool,
            tenant_id=TENANT_NAMESPACE_UUID,
            tenant_namespace_uuid=TENANT_NAMESPACE_UUID,
            days_back=req.days_back,
        )
        _connector_status.update({
            "ingested": result["ingested"],
            "skipped":  result["skipped"],
            "errors":   result["errors"],
            "message":  "completed",
        })
    except Exception as e:
        _connector_status["errors"].append(str(e))
        _connector_status["message"] = f"failed: {e}"
    finally:
        _connector_status["is_running"] = False


@app.post("/ingest-slack")
async def ingest_slack(req: IngestSlackRequest, background_tasks: BackgroundTasks):
    """Kick off a background Slack ingestion job."""
    if _connector_status["is_running"]:
        raise HTTPException(
            status_code=400,
            detail=f"A connector is already running: {_connector_status['connector']}",
        )
    background_tasks.add_task(_run_slack_ingest, req)
    return {
        "status": "started",
        "message": f"Ingesting {len(req.channel_ids)} channel(s) with {req.days_back}-day lookback.",
    }


ALLOWED_INGEST_EXTS = {".pdf", ".txt", ".md", ".docx", ".csv"}

@app.post("/ingest")
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    user: UserIdentity = Depends(get_current_user),
):
    """
    Accept a file upload from the browser and index it into the user's knowledge base.
    Supported: PDF, TXT, MD, DOCX, CSV (up to 50 MB).
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_INGEST_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_INGEST_EXTS))}",
        )

    # Write upload to a temp file (parse_document needs a real path)
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        source_name = os.path.basename(file.filename or tmp_path)
        chunks = parse_document(tmp_path, source_name=source_name)
        if not chunks:
            raise HTTPException(status_code=422, detail="No text could be extracted from this file.")

        db_pool = request.app.state.db_pool
        tenant_id = getattr(request.state, "tenant_id", None)
        await pg_ingest_chunks(chunks, db_pool, tenant_id=tenant_id)
        return {
            "status": "ok",
            "filename": source_name,
            "chunks_indexed": len(chunks),
        }
    finally:
        os.unlink(tmp_path)


@app.get("/ingest-status")
def get_ingest_status():
    """Poll connector ingestion progress."""
    return _connector_status


@app.get("/health", include_in_schema=False)
async def health_check():
    """Lightweight ping — returns minimal JSON. Used by frontend connection check."""
    return {"status": "ok"}


@app.get("/status")
async def get_status(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Return indexed chunk count and source count — scoped to the calling user."""
    from backend.auth import KGF_DEV_MODE
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        return {"status": "ok", "dev_mode": KGF_DEV_MODE, "indexed_chunks_count": 0, "indexed_sources_count": 0}
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
            count = await conn.fetchval(
                "SELECT count(*) FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid",
                tenant_id,
            )
            sources_count = await conn.fetchval(
                "SELECT count(DISTINCT source_id) FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid",
                tenant_id,
            )
        return {
            "status": "ok",
            "dev_mode": KGF_DEV_MODE,
            "indexed_chunks_count": count,
            "indexed_sources_count": sources_count,
        }
    except Exception as e:
        return {"status": "ok", "dev_mode": KGF_DEV_MODE, "indexed_chunks_count": 0, "indexed_sources_count": 0, "error": str(e)}


@app.get("/files")
async def get_files(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Return the full list of indexed source IDs — scoped to the calling user."""
    from backend.auth import KGF_DEV_MODE
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        return {"status": "ok", "indexed_files": []}
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
            rows = await conn.fetch(
                "SELECT DISTINCT source_id FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid ORDER BY source_id",
                tenant_id,
            )
        return {
            "status": "ok",
            "indexed_files": [r["source_id"] for r in rows],
        }
    except Exception as e:
        return {"status": "ok", "indexed_files": [], "error": str(e)}


@app.post("/clear")
async def clear_index(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Clear ONLY the calling user's indexed data (chunks + graph). Safe: no cross-user deletion."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Could not resolve user tenant.")
    schema = "tenant_redwood_inference_prod"
    try:
        async with app.state.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                tid_uuid = uuid.UUID(tenant_id)
                # graph_edges cascades from graph_nodes via FK; neither has tenant_id column.
                await conn.execute(
                    f"DELETE FROM {schema}.graph_nodes WHERE external_source_id IN "
                    f"(SELECT DISTINCT author_id FROM {schema}.vector_chunks WHERE tenant_id = $1)",
                    tid_uuid,
                )
                await conn.execute(f"DELETE FROM {schema}.vector_chunks    WHERE tenant_id = $1", tid_uuid)
                await conn.execute(f"DELETE FROM {schema}.ingestion_hashes WHERE tenant_id = $1", tenant_id)  # TEXT
        _file_hash_cache.clear()
        return {"status": "success", "message": f"Index cleared for user {user.email}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear index: {e}")


@app.post("/admin/clear-my-data")
async def admin_clear_my_data(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Flush ALL data for the calling user and reset their file-hash cache.
    Useful during development; production users should use DELETE /account.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Could not resolve user tenant.")
    schema = "tenant_redwood_inference_prod"
    tid = uuid.UUID(tenant_id)
    try:
        async with app.state.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                await conn.execute(
                    f"DELETE FROM {schema}.graph_nodes WHERE external_source_id IN "
                    f"(SELECT DISTINCT author_id FROM {schema}.vector_chunks WHERE tenant_id = $1)",
                    tid,
                )
                await conn.execute(f"DELETE FROM {schema}.vector_chunks    WHERE tenant_id = $1", tid)
                await conn.execute(f"DELETE FROM {schema}.ingestion_hashes WHERE tenant_id = $1", tid)
        _file_hash_cache.clear()
        return {"status": "ok", "message": f"Data cleared for {user.email}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear: {e}")

@app.get("/me")
async def get_me(user: UserIdentity = Depends(get_current_user)):
    """Return the currently authenticated user identity."""
    return {"email": user.email, "domain": user.domain, "is_admin": user.is_admin}


@app.get("/org/info")
async def get_org_info(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Return organization details, members, and pending invites."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No active workspace found.")

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace ID.")

    async with app.state.db_pool.acquire() as conn:
        # 1. Fetch organization details
        org_row = await conn.fetchrow(
            "SELECT email_domain, company_name, provisioned_at FROM tenant_registry WHERE tenant_id = $1",
            tenant_uuid,
        )
        if not org_row:
            company_name = user.domain.split(".")[0].capitalize()
            email_domain = user.domain
        else:
            company_name = org_row["company_name"] or user.domain.split(".")[0].capitalize()
            email_domain = org_row["email_domain"]

        # 2. Fetch active members
        member_rows = await conn.fetch(
            """
            SELECT user_id, email, role, joined_at 
            FROM public.user_org_membership 
            WHERE tenant_id = $1::uuid 
            ORDER BY joined_at ASC
            """,
            tenant_uuid,
        )
        members = [
            {
                "user_id": str(r["user_id"]),
                "email": r["email"],
                "role": r["role"],
                "joined_at": r["joined_at"].isoformat() if r["joined_at"] else None,
            }
            for r in member_rows
        ]

        # 3. Fetch pending invites
        invite_rows = await conn.fetch(
            """
            SELECT id, invited_email, role, created_at 
            FROM public.org_invites 
            WHERE tenant_id = $1::uuid AND accepted_at IS NULL 
            ORDER BY created_at ASC
            """,
            tenant_uuid,
        )
        invites = [
            {
                "id": str(r["id"]),
                "email": r["invited_email"],
                "role": r["role"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "invite_link": f"https://smriti.one/app/auth.html?invite={r['id']}",
            }
            for r in invite_rows
        ]

    return {
        "status": "ok",
        "company_name": company_name,
        "email_domain": email_domain,
        "role": "admin" if user.is_admin else "member",
        "members": members,
        "invites": invites,
    }


@app.post("/org/invite")
async def invite_org_member(
    req: OrgInviteRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Invite a new team member to this organization. restricted to admins."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only workspace admins can invite members.")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No active workspace found.")

    invited_email = req.email.strip().lower()
    role = req.role.strip().lower()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Invalid role specified. Use 'admin' or 'member'.")

    # Generate deterministic user UUID for the current user for invited_by (needed if in dev mode)
    if user.user_id.startswith("dev:"):
        import uuid as _uuid
        _NS = _uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        user_uuid = _uuid.uuid5(_NS, user.user_id)
    else:
        user_uuid = uuid.UUID(user.user_id)

    try:
        tenant_uuid = uuid.UUID(tenant_id)
        async with app.state.db_pool.acquire() as conn:
            # Check if email is already a member
            existing_member = await conn.fetchval(
                "SELECT count(*) FROM public.user_org_membership WHERE tenant_id = $1::uuid AND email = $2",
                tenant_uuid,
                invited_email,
            )
            if existing_member > 0:
                raise HTTPException(status_code=400, detail="User is already a member of this workspace.")

            # Create or update invite
            await conn.execute(
                """
                INSERT INTO public.org_invites (tenant_id, invited_email, role, invited_by)
                VALUES ($1::uuid, $2, $3, $4)
                ON CONFLICT ON CONSTRAINT uq_org_invites_tenant_email
                DO UPDATE SET
                    role = EXCLUDED.role,
                    created_at = NOW(),
                    accepted_at = NULL
                """,
                tenant_uuid,
                invited_email,
                role,
                user_uuid,
            )
        return {"status": "ok", "message": f"Invitation successfully created for {invited_email}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create invite: {e}")


@app.delete("/org/invites/{invite_id}")
async def cancel_org_invite(
    invite_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Revoke a pending team invitation. restricted to admins."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only workspace admins can cancel invites.")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No active workspace found.")

    try:
        invite_uuid = uuid.UUID(invite_id)
        tenant_uuid = uuid.UUID(tenant_id)
        async with app.state.db_pool.acquire() as conn:
            deleted = await conn.execute(
                "DELETE FROM public.org_invites WHERE id = $1 AND tenant_id = $2",
                invite_uuid,
                tenant_uuid,
            )
        if "DELETE 0" in deleted:
            raise HTTPException(status_code=404, detail="Invitation not found or already accepted.")
        return {"status": "ok", "message": "Invitation successfully canceled."}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid invitation ID format.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel invite: {e}")


@app.delete("/org/members/{member_user_id}")
async def remove_org_member(
    member_user_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Remove a user from this organization. restricted to admins."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only workspace admins can remove members.")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No active workspace found.")

    # Determine caller user_uuid
    if user.user_id.startswith("dev:"):
        import uuid as _uuid
        _NS = _uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        user_uuid = _uuid.uuid5(_NS, user.user_id)
    else:
        user_uuid = uuid.UUID(user.user_id)

    try:
        member_uuid = uuid.UUID(member_user_id)
        tenant_uuid = uuid.UUID(tenant_id)

        if member_uuid == user_uuid:
            raise HTTPException(status_code=400, detail="You cannot remove yourself from the workspace.")

        async with app.state.db_pool.acquire() as conn:
            deleted = await conn.execute(
                "DELETE FROM public.user_org_membership WHERE user_id = $1 AND tenant_id = $2",
                member_uuid,
                tenant_uuid,
            )
        if "DELETE 0" in deleted:
            raise HTTPException(status_code=404, detail="Member not found in this workspace.")
        return {"status": "ok", "message": "Member successfully removed from workspace."}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove member: {e}")


@app.patch("/org/info")
async def update_org_info(
    req: OrgInfoPatchRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Update organization profile details (e.g. company display name). restricted to admins."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only workspace admins can update organization settings.")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No active workspace found.")

    company_name = req.company_name.strip()
    if not company_name:
        raise HTTPException(status_code=400, detail="Company name cannot be empty.")

    try:
        tenant_uuid = uuid.UUID(tenant_id)
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenant_registry SET company_name = $1 WHERE tenant_id = $2",
                company_name,
                tenant_uuid,
            )
        return {"status": "ok", "message": "Workspace settings updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {e}")



@app.delete("/account")
async def delete_account(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Delete ALL data for the authenticated user's tenant.

    Permanently removes:
      - All vector chunks (indexed documents)
      - Knowledge graph nodes and edges
      - Ingestion hashes (deduplication records)
      - OAuth credentials (Slack etc.)
      - Tenant registry entry

    The user is NOT deleted from Supabase Auth — they can create a fresh
    workspace by signing in again.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant found for this account.")

    schema = "tenant_redwood_inference_prod"
    print(f"⚠ ACCOUNT DELETE requested: tenant={tenant_id}, user={user.email}")

    async with app.state.db_pool.acquire() as conn:
        # Set RLS context
        await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")

        tid_uuid = uuid.UUID(tenant_id)  # for UUID columns: vector_chunks, tenant_registry
        # tenant_id is a plain str for TEXT columns: ingestion_hashes, tenant_credentials

        # graph_edges cascades from graph_nodes (FK ON DELETE CASCADE).
        # graph_nodes has no tenant_id; delete nodes whose author_id appears in this tenant's chunks.
        await conn.execute(
            f"DELETE FROM {schema}.graph_nodes WHERE external_source_id IN "
            f"(SELECT DISTINCT author_id FROM {schema}.vector_chunks WHERE tenant_id = $1)",
            tid_uuid,
        )
        await conn.execute(f"DELETE FROM {schema}.vector_chunks     WHERE tenant_id = $1", tid_uuid)
        await conn.execute(f"DELETE FROM {schema}.ingestion_hashes  WHERE tenant_id = $1", tenant_id)   # TEXT
        await conn.execute(f"DELETE FROM {schema}.tenant_credentials WHERE tenant_id = $1", tenant_id)  # TEXT
        await conn.execute("DELETE FROM tenant_registry WHERE tenant_id = $1", tid_uuid)

    print(f"✅ Account deleted: tenant={tenant_id}, user={user.email}")
    return {"status": "deleted", "tenant_id": tenant_id, "email": user.email}



@app.get("/connections")
async def get_connections(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Return which data sources are connected for this tenant."""
    tenant_id = getattr(request.state, "tenant_id", TENANT_NAMESPACE_UUID)
    try:
        async with app.state.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source, scopes, updated_at
                FROM tenant_redwood_inference_prod.tenant_credentials
                WHERE tenant_id = $1
                """,
                tenant_id,
            )
        return [
            {"source": r["source"], "scopes": r["scopes"],
             "connected_at": r["updated_at"].isoformat()}
            for r in rows
        ]
    except Exception as e:
        return []


# ─── Slack OAuth Endpoints ─────────────────────────────────────────────────────

@app.get("/slack/oauth/start")
async def slack_oauth_start(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Returns JSON {url: ...} with the Slack consent URL.
    The frontend calls this via authFetch() (Bearer token), then redirects
    the browser to the returned URL.  Plain browser navigation won't work
    because there's no way to attach an Authorization header to an <a href>.
    """
    tenant_id    = getattr(request.state, "tenant_id", TENANT_NAMESPACE_UUID)
    redirect_uri = str(request.base_url).rstrip("/") + "/slack/oauth/callback"
    try:
        auth_url = _slack_oauth.build_authorization_url(tenant_id, redirect_uri)
        return {"url": auth_url}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/slack/oauth/callback")
async def slack_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """
    Step 2: Slack calls this after user authorizes.
    Exchanges code for token, stores encrypted, redirects to UI.
    """
    if error:
        return RedirectResponse(url="/app/index.html?error=slack_denied#connectors", status_code=302)

    redirect_uri = str(request.base_url).rstrip("/") + "/slack/oauth/callback"
    try:
        await _slack_oauth.exchange_code_for_token(
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            db_pool=app.state.db_pool,
        )
        return RedirectResponse(url="/app/index.html?connected=slack#connectors", status_code=302)
    except ValueError as e:
        # State expired or malformed — give the user a clean retry path.
        # This happens when the user starts OAuth, gets interrupted (e.g. a meeting),
        # and returns after the 10-minute state window has expired.
        # Return a redirect to the app with an error code so the UI can show
        # a retry button rather than a cryptic error page.
        print(f"  ⚠ OAuth state error (likely expired): {e}")
        return RedirectResponse(
            url="/app/index.html?error=oauth_expired#connectors",
            status_code=302,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Slack token exchange failed: {e}")


@app.delete("/slack/disconnect")
async def slack_disconnect(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Disconnect Slack: delete the stored OAuth token for this tenant.
    The user will need to go through OAuth again to reconnect.
    No Slack data is deleted from the vector store — only the credential is removed.
    """
    tenant_id = getattr(request.state, "tenant_id", TENANT_NAMESPACE_UUID)
    from backend.db import delete_tenant_credentials
    async with app.state.db_pool.acquire() as conn:
        deleted = await delete_tenant_credentials(conn, tenant_id, "slack")
    if deleted:
        print(f"✅ Slack disconnected for tenant={tenant_id} by {user.email}")
        return {"status": "disconnected", "source": "slack"}
    else:
        raise HTTPException(
            status_code=404,
            detail="No Slack connection found for this tenant.",
        )


# ─── Google Drive OAuth + Connector Endpoints ──────────────────────────────────

@app.get("/gdrive/oauth/start")
async def gdrive_oauth_start(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Returns JSON {url: ...} with the Google consent URL.
    Called via authFetch() from the frontend — NOT via direct browser navigation.
    The frontend JS receives the URL and does window.location.href = url.
    """
    tenant_id    = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    redirect_uri = str(request.base_url).rstrip("/") + "/gdrive/oauth/callback"
    try:
        auth_url = _gdrive_oauth.build_authorization_url(tenant_id, redirect_uri)
        return {"url": auth_url}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/gdrive/oauth/callback")
async def gdrive_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """
    Step 2: Google calls this after user authorizes Drive access.
    Exchanges code for token (including refresh_token), stores encrypted.
    """
    if error:
        return RedirectResponse(url="/app/index.html?error=gdrive_denied#connectors", status_code=302)

    redirect_uri = str(request.base_url).rstrip("/") + "/gdrive/oauth/callback"
    try:
        await _gdrive_oauth.exchange_code_for_token(
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            db_pool=app.state.db_pool,
        )
        return RedirectResponse(url="/app/index.html?connected=gdrive#connectors", status_code=302)
    except ValueError as e:
        print(f"  ⚠ Google OAuth state error (likely expired): {e}")
        return RedirectResponse(
            url="/app/index.html?error=oauth_expired#connectors",
            status_code=302,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Google Drive token exchange failed: {e}")


@app.post("/ingest-gdrive")
async def ingest_gdrive(
    req: IngestDriveRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: UserIdentity = Depends(get_current_user),
):
    """
    Kick off a background Google Drive ingestion job.
    Requires an active OAuth connection (/gdrive/oauth/start).
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if _connector_status["is_running"]:
        raise HTTPException(
            status_code=400,
            detail=f"A connector is already running: {_connector_status['connector']}",
        )

    async def _run():
        global _connector_status
        _connector_status.update({
            "is_running": True,
            "connector":  "gdrive",
            "ingested":   0,
            "skipped":    0,
            "errors":     [],
            "message":    "running",
        })
        try:
            access_token = await _gdrive_oauth.get_valid_token(tenant_id, app.state.db_pool)
            result = await _gdrive_connector.ingest_from_gdrive(
                access_token=access_token,
                db_pool=app.state.db_pool,
                tenant_id=tenant_id,
                folder_id=req.folder_id,
            )
            _connector_status.update({
                "ingested": result["ingested"],
                "skipped":  result["skipped"],
                "errors":   result["errors"],
                "message":  f"completed — {result['files']} files processed",
            })
        except Exception as e:
            _connector_status["errors"].append(str(e))
            _connector_status["message"] = f"failed: {e}"
            print(f"  ✗ Google Drive ingest error: {e}")
        finally:
            _connector_status["is_running"] = False

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": "Google Drive ingestion started in background.",
        "folder_id": req.folder_id or "(entire Drive)",
    }


@app.get("/gdrive/status")
async def gdrive_status(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Check whether Google Drive is connected for this user."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        return {"connected": False}
    async with app.state.db_pool.acquire() as conn:
        creds = await load_tenant_credentials(conn, tenant_id, "gdrive")
    return {
        "connected":    creds is not None,
        "connected_at": creds.get("connected_at") if creds else None,
    }


@app.delete("/gdrive/disconnect")
async def gdrive_disconnect(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Disconnect Google Drive: removes stored OAuth token."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    from backend.db import delete_tenant_credentials
    async with app.state.db_pool.acquire() as conn:
        deleted = await delete_tenant_credentials(conn, tenant_id, "gdrive")
    if deleted:
        print(f"✅ Google Drive disconnected for tenant={tenant_id} by {user.email}")
        return {"status": "disconnected", "source": "gdrive"}
    else:
        raise HTTPException(
            status_code=404,
            detail="No Google Drive connection found for this tenant.",
        )


@app.get("/confluence/status")
async def confluence_status(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Check whether Confluence is connected for this user."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        return {"connected": False}
    async with app.state.db_pool.acquire() as conn:
        creds = await load_tenant_credentials(conn, tenant_id, "confluence")
    return {
        "connected":    creds is not None,
        "connected_at": creds.get("connected_at") if creds else None,
    }


@app.post("/confluence/connect")
async def confluence_connect(
    req: ConfluenceConnectRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: UserIdentity = Depends(get_current_user),
):
    """Connect Confluence with URL + credentials, verify them, and index in background."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
        
    url = _confluence_connector.normalize_confluence_url(req.confluence_url)
    
    # Verify connection
    is_valid = await _confluence_connector.verify_confluence_credentials(
        url, req.email, req.api_token
    )
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail="Could not connect to Confluence. Please check URL, email, and API token.",
        )
        
    token_dict = {
        "confluence_url": url,
        "email":          req.email,
        "api_token":      req.api_token,
        "connected_at":   int(time.time()),
    }
    
    async with app.state.db_pool.acquire() as conn:
        await save_tenant_credentials(
            conn=conn,
            tenant_id=tenant_id,
            source="confluence",
            token_dict=token_dict,
        )
        
    print(f"✅ Confluence connected for tenant={tenant_id} by {user.email}")
    
    # Auto-trigger indexing in background
    background_tasks.add_task(
        _run_confluence_ingest_task, tenant_id, url, req.email, req.api_token
    )
    
    return {"status": "connected", "message": "Confluence connected and indexing started."}


async def _run_confluence_ingest_task(tenant_id: str, url: str, email: str, api_token: str):
    global _connector_status
    _connector_status.update({
        "is_running": True,
        "connector":  "confluence",
        "ingested":   0,
        "skipped":    0,
        "errors":     [],
        "message":    "running",
    })
    try:
        result = await _confluence_connector.ingest_from_confluence(
            confluence_url=url,
            email=email,
            api_token=api_token,
            db_pool=app.state.db_pool,
            tenant_id=tenant_id,
        )
        _connector_status.update({
            "ingested": result["ingested"],
            "skipped":  result["skipped"],
            "errors":   result["errors"],
            "message":  f"completed — {result['files']} pages processed",
        })
    except Exception as e:
        _connector_status["errors"].append(str(e))
        _connector_status["message"] = f"failed: {e}"
        print(f"  ✗ Confluence ingest error: {e}")
    finally:
        _connector_status["is_running"] = False


@app.post("/ingest-confluence")
async def ingest_confluence(
    request: Request,
    background_tasks: BackgroundTasks,
    user: UserIdentity = Depends(get_current_user),
):
    """Manually trigger Confluence background sync."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if _connector_status["is_running"]:
        raise HTTPException(
            status_code=400,
            detail=f"A connector is already running: {_connector_status['connector']}",
        )
        
    async with app.state.db_pool.acquire() as conn:
        creds = await load_tenant_credentials(conn, tenant_id, "confluence")
        
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Confluence is not connected. Connect Confluence first.",
        )
        
    background_tasks.add_task(
        _run_confluence_ingest_task,
        tenant_id,
        creds["confluence_url"],
        creds["email"],
        creds["api_token"],
    )
    return {"status": "started", "message": "Confluence sync started in the background."}


@app.delete("/confluence/disconnect")
async def confluence_disconnect(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """Disconnect Confluence: removes stored credentials."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    from backend.db import delete_tenant_credentials
    async with app.state.db_pool.acquire() as conn:
        deleted = await delete_tenant_credentials(conn, tenant_id, "confluence")
    if deleted:
        print(f"✅ Confluence disconnected for tenant={tenant_id} by {user.email}")
        return {"status": "disconnected", "source": "confluence"}
    else:
        raise HTTPException(
            status_code=404,
            detail="No Confluence connection found for this tenant.",
        )



# ─── Sync Status ─────────────────────────────────────────────────────────────

@app.get("/sync-status")
def get_sync_status():
    """Return current background sync state from the scheduler."""
    return sync_scheduler.sync_status


@app.post("/sync-now")
async def trigger_sync_now(
    user: UserIdentity = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    """Admin-only: trigger an immediate full sync across all tenants."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    background_tasks.add_task(sync_scheduler.sync_all_tenants, app.state.db_pool)
    return {"status": "sync triggered"}



def detect_question_type(query_text: str) -> str:
    """Classifies the query as 'exploratory' or 'factual'."""
    q = query_text.lower().strip()
    
    # Common exploratory indicators
    exploratory_indicators = [
        r"\bexplain\b",
        r"\bsummarize\b",
        r"\bsummary\b",
        r"\bdescribe\b",
        r"\bcompare\b",
        r"\bdifference\b",
        r"\bdifferences\b",
        r"\bwhy\b",
        r"\bhow to\b",
        r"\bhow does\b",
        r"\bhow do i\b",
        r"\bhow can i\b",
        r"\btutorial\b",
        r"\bguide\b",
        r"\bwalkthrough\b",
        r"\boverview\b",
        r"\banalyze\b",
        r"\bevaluate\b",
        r"\bsynthesis\b",
        r"\bsynthesize\b",
        r"\bdetail\b",
        r"\bdetails of\b"
    ]
    
    for pattern in exploratory_indicators:
        if re.search(pattern, q):
            return "exploratory"
            
    return "factual"


@app.post("/query")
async def process_query(
    req: QueryRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    start_time  = time.perf_counter()
    tenant_id   = getattr(request.state, "tenant_id",  TENANT_NAMESPACE_UUID)
    user_email  = user.email
    user_domain = user.domain
    if not hasattr(app.state, "db_pool") or app.state.db_pool is None:
        raise HTTPException(status_code=500, detail="DB pool not initialized.")

    # ── Guard 1: Reject empty / too-short queries before hitting Ollama ────────────
    # An empty embedding generates a near-zero vector. pgvector's cosine distance
    # operator returns semantically random chunks that the LLM synthesises into
    # a confident-sounding but meaningless answer that passes the grounding check.
    query_text = req.query.strip()
    if len(query_text) < 3:
        raise HTTPException(
            status_code=422,
            detail="Query must be at least 3 characters. Please ask a specific question.",
        )

    # ── 1. Generate query embedding (raises 503 on Ollama failure) ────────────────
    query_emb = await get_async_ollama_embedding(query_text)

    # ── Guard: zero-norm vector crashes pgvector cosine operator ─────────────
    if not any(query_emb):
        raise HTTPException(
            status_code=503,
            detail="Embedding returned a zero vector — Ollama model may not be loaded. Run: ollama pull nomic-embed-text",
        )

    query_emb_str = f"[{','.join(map(str, query_emb))}]"

    # ── 2. Extract keywords (stop-word filtered) ───────────────────────────────────
    words    = re.findall(r"\w+", query_text)
    keywords = [w.lower() for w in words if w.lower() not in COMMON_STOPWORDS]

    # ── 3. Build parameterized hybrid SQL with tenant + permission + category filters ────────
    # $1 = embedding, $2 = user_email, $3 = user_domain, $4 = tenant_id
    # $5 = category (if active), $5/$6+ = keywords
    # Tenant filter: MANDATORY — ensures strict per-user data isolation.
    # Permission clause: return chunk if public OR user is in allowed_users OR
    # user's domain is in allowed_groups.
    permission_clause = (
        "tenant_id = $4::uuid "
        "AND (is_public = true "
        "OR $2 = ANY(allowed_users) "
        "OR $3 = ANY(allowed_groups))"
    )

    # Resolve tenant_id — must be a valid UUID string.
    # Falls back to TENANT_NAMESPACE_UUID only in dev mode (where user_id is derived).
    tenant_id_str = getattr(request.state, "tenant_id", None) or TENANT_NAMESPACE_UUID

    # Category filter — parameterized to prevent SQL injection.
    # $5 is reserved for the category when active; keywords start at $6 then.
    if req.category_filter:
        cat_value = req.category_filter.lower().strip()
        category_clause = "AND document_category = $5"
        base_param_idx  = 6   # keywords start at $6
        query_params_base: list = [query_emb_str, user_email, user_domain, tenant_id_str, cat_value]
    else:
        category_clause = ""
        base_param_idx  = 5   # keywords start at $5
        query_params_base = [query_emb_str, user_email, user_domain, tenant_id_str]

    if not keywords:
        text_score_expr = "0.0"
        query_params: list = query_params_base
    else:
        cases = []
        for i, kw in enumerate(keywords):
            param_idx = base_param_idx + i
            cases.append(f"CASE WHEN content ILIKE ${param_idx} THEN 1.0 ELSE 0.0 END")
        text_score_expr = f"({' + '.join(cases)}) / {float(len(keywords))}"
        query_params = query_params_base + [f"%{kw}%" for kw in keywords]

    # Clamp top_k to a safe range — context window budget allows up to 10 chunks at 700 chars each
    effective_top_k = max(1, min(req.top_k, 10))

    opt_hybrid_sql = f"""
        WITH candidates AS (
            SELECT
                source_id,
                source_type,
                channel_or_space,
                content,
                author_id,
                document_category,
                (1 - (embedding <=> $1::vector)) AS semantic_score
            FROM tenant_redwood_inference_prod.vector_chunks
            WHERE {permission_clause}
            {category_clause}
            ORDER BY embedding <=> $1::vector ASC
            LIMIT 50
        )
        SELECT
            source_id,
            source_type,
            channel_or_space,
            content,
            author_id,
            document_category,
            semantic_score,
            ({text_score_expr}) AS text_score,
            (0.7 * semantic_score + 0.3 * ({text_score_expr})) AS combined_score
        FROM candidates
        ORDER BY combined_score DESC
        LIMIT {effective_top_k};
    """

    # ── 4. Execute retrieval + graph expert query ─────────────────────────────────────
    retrieved_chunks = []
    experts = []

    try:
        async with app.state.db_pool.acquire() as conn:
            # CRITICAL: Use SET LOCAL inside a transaction so the tenant_id
            # resets automatically when the connection is returned to the pool.
            # Using bare SET causes the value to persist across pooled connection
            # reuse — a Tenant A context bleeds into Tenant B’s query (data breach).
            # Graph expert injection — inside the same transaction / tenant context.
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                rows = await conn.fetch(opt_hybrid_sql, *query_params)
                experts_raw = await fetch_top_experts(conn, top_n=3)

            for r in rows:
                retrieved_chunks.append({
                    "source":             r["source_id"],
                    "type":              r["source_type"],
                    "location":          r["channel_or_space"],
                    "content":           r["content"],
                    "author_id":         r["author_id"],
                    "document_category": r["document_category"],
                    "score":             float(r["combined_score"]),
                    "semantic_score":    float(r["semantic_score"]),
                })

            # Graph cold-start: if no experts yet, return a descriptive message
            # instead of an empty panel (which looks like a broken feature).
            if experts_raw:
                experts = experts_raw
            else:
                experts = [{"name": "_cold_start", "score": 0, "message":
                    "Not enough interaction data yet. Expert graph populates after "
                    "a few days of Slack activity."}]

    except Exception as e:
        print(f"Hybrid Postgres query error: {e}")
        raise HTTPException(status_code=500, detail=f"DB transaction failure: {e}")

    # Filter retrieved chunks based on a semantic relevance threshold
    if retrieved_chunks:
        max_semantic_score = max(c["semantic_score"] for c in retrieved_chunks)
        if max_semantic_score < 0.51:
            print(f"  [query] Top semantic score {max_semantic_score:.4f} < 0.51 threshold. Clearing retrieved chunks.")
            retrieved_chunks = []

    if not retrieved_chunks:
        admin_email = "admin.smritione@gmail.com"
        try:
            async with app.state.db_pool.acquire() as conn:
                admin_row = await conn.fetchrow(
                    "SELECT email FROM public.user_org_membership WHERE tenant_id = $1::uuid AND role = 'admin' LIMIT 1",
                    tenant_id_str,
                )
                if admin_row and admin_row["email"]:
                    admin_email = admin_row["email"]
        except Exception as e:
            print(f"Error fetching admin email: {e}")

        return {
            "query": req.query,
            "response": f"I don't have that information from the indexed documents, please contact {admin_email}",
            "citations": [],
            "retrieved_context": [],
            "experts": experts,
            "latency_seconds": round(time.perf_counter() - start_time, 4),
        }

    # ── 5. Assemble LLM prompt with context ──────────────────────────────────
    # Budget: num_ctx=3072 - 400 (reply) - 200 (prompts) = ~2472 tokens = ~9888 chars for all chunks
    MAX_CHUNK_CHARS = min(700, max(250, 9500 // max(len(retrieved_chunks), 1)))

    # Boost semantic data files (content.js, README) to top so the model sees
    # the person's identity/profile before generic code files.
    _PRIORITY_SOURCES = {"content.js", "readme.md", "README.md"}
    priority_chunks = [c for c in retrieved_chunks if any(p in c['source'] for p in _PRIORITY_SOURCES)]
    other_chunks    = [c for c in retrieved_chunks if not any(p in c['source'] for p in _PRIORITY_SOURCES)]
    ordered_chunks  = priority_chunks + other_chunks

    # HTML-tag stripper — portfolio content.js has <strong>...</strong> in bullet points
    _html_tag_re = re.compile(r'<[^>]+>')

    context_str = ""
    for idx, chunk in enumerate(ordered_chunks):
        clean_content = _html_tag_re.sub('', chunk['content'])
        content = clean_content[:MAX_CHUNK_CHARS]
        if len(clean_content) > MAX_CHUNK_CHARS:
            content += "…"
        context_str += (
            f"[{idx+1}] Source: {chunk['source']} | Location: {chunk['location']}\n"
            f"{content}\n\n"
        )


    question_type = detect_question_type(query_text)
    if question_type == "factual":
        system_prompt = (
            "You are Smriti, a friendly and knowledgeable assistant for this organization. "
            "You have access to the organization's documents and answer questions in a natural, conversational way.\n"
            "This is a FACTUAL query. Be extremely precise, concise, and direct.\n\n"
            "RULES:\n"
            "1. ALWAYS answer from the context provided. Focus on answering the question directly and precisely.\n"
            "2. Keep the response short and to the point. One clear paragraph or a single sentence is preferred. Avoid unnecessary wordiness.\n"
            "3. After each factual statement, add an inline citation: [Citation: filename, location]. "
            "Example: KGF stands for Knowledge Guardian Foundry [Citation: demo_script.md, line 1].\n"
            "4. Do NOT invent facts. If the context does not contain the answer to the question, or is unrelated to the question, you MUST say that you don't have information on that in the indexed documents.\n"
            "5. Never use bullet-point dumps unless specifically asked. Write naturally like a helpful colleague."
        )
        temperature = 0.0
    else:
        system_prompt = (
            "You are Smriti, a friendly and knowledgeable assistant for this organization. "
            "You have access to the organization's documents and answer questions in a natural, conversational way.\n"
            "This is an EXPLORATORY query. Provide a clear, comprehensive, and well-structured synthesis or explanation.\n\n"
            "RULES:\n"
            "1. Synthesize information from the provided context to explain concepts, processes, or summaries clearly.\n"
            "2. Organize the answer logically. Use paragraph breaks or clear bullet points/numbered lists if describing steps or multi-part comparisons.\n"
            "3. Add inline citations when referencing specific facts: [Citation: filename, location].\n"
            "4. Do NOT invent facts. Stick strictly to the context. If the context does not contain the answer, state clearly that you don't have information on that in the indexed documents.\n"
            "5. Keep the explanation engaging, comprehensive, and easy for a colleague to understand."
        )
        temperature = 0.3

    user_message = (
        f"CONTEXT:\n{context_str}\n"
        f"QUESTION: {req.query}"
    )

    # Build message list (system prompt + history + current query)
    messages = [{"role": "system", "content": system_prompt}]
    if req.history:
        # Keep last 6 messages to stay within context window
        for msg in req.history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_message})

    # ── 6. LLM generation (chat API) ─────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        chat_payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 3072,
                "num_predict": 512,
            },
        }
        try:
            response = await client.post(OLLAMA_CHAT_URL, json=chat_payload, timeout=180.0)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Ollama synthesis failure: {response.text}",
                )

            # chat API returns {"message": {"role": "assistant", "content": "..."}}
            resp_json = response.json()
            raw_response = (resp_json.get("message") or {}).get("content") or ""
            if not raw_response.strip():
                # Model returned empty — context likely exceeded token budget
                print("  ⚠ LLM returned empty response — context may be too large")
                raw_response = "I cannot find this in the indexed documents."


            # ── 7. Grounding firewall ─────────────────────────────────────────
            validated_response = validate_response(raw_response, retrieved_chunks)

            # Check if validation result is fallback / cannot find
            is_fallback = False
            normalized_val = validated_response.replace("’", "'").replace("`", "'").strip()
            if not normalized_val or normalized_val == "I cannot find the answer in the provided documents.":
                is_fallback = True
            else:
                _FALLBACK_RE = re.compile(
                    r"(cannot find (the answer|this)|unable to find|not find the answer|"
                    r"no information|no info|don't have information|don't have info|do not have information|do not have info|"
                    r"don't have that information|do not have that information|not have information on that|"
                    r"not mention|not contain|not provided|does not provide|"
                    r"do not know|don't know|no relevant info|cannot find this in|"
                    r"no relevant organizational history)",
                    re.IGNORECASE,
                )
                if _FALLBACK_RE.search(normalized_val):
                    is_fallback = True

            if is_fallback:
                # Fetch admin email
                admin_email = "admin.smritione@gmail.com"
                try:
                    async with app.state.db_pool.acquire() as conn:
                        admin_row = await conn.fetchrow(
                            "SELECT email FROM public.user_org_membership WHERE tenant_id = $1::uuid AND role = 'admin' LIMIT 1",
                            tenant_id_str,
                        )
                        if admin_row and admin_row["email"]:
                            admin_email = admin_row["email"]
                except Exception as e:
                    print(f"Error fetching admin email: {e}")
                validated_response = f"I don't have that information from the indexed documents, please contact {admin_email}"

            accessed_files = [c["source"] for c in retrieved_chunks]
            write_audit_log(user_email, req.query, accessed_files)

            citations = [] if is_fallback else extract_citations(validated_response)

            payload = {
                "query": req.query,
                "response": _sanitize(validated_response),
                "model": MODEL_NAME,
                "citations": citations,
                "retrieved_context": [
                    {
                        "source": c["source"],
                        "type": c["type"],
                        "location": _sanitize(c["location"]),
                        "content": _sanitize(c["content"]),
                        "author_id": c["author_id"],
                        "score": c["score"],
                    }
                    for c in retrieved_chunks
                ],
                "experts": experts,
                "latency_seconds": round(time.perf_counter() - start_time, 4),
            }
            return JSONResponse(content=payload)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Generation pipeline error: {e}",
            )