import os
import glob
import asyncio
import hashlib
import json
import re
import time
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import asyncpg
import uuid

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
from backend.db import check_and_mark_ingested, save_tenant_credentials, load_tenant_credentials
from backend.auth import get_current_user, UserIdentity
from backend import sync_scheduler
from backend import slack_oauth as _slack_oauth
from backend.doc_classifier import classify_document


DB_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
TENANT_NAMESPACE_UUID = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
TENANT_UUID = uuid.UUID(TENANT_NAMESPACE_UUID)

MODEL_NAME_EMBED = "nomic-embed-text"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"

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
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

@app.get("/", include_in_schema=False)
async def _root_redirect():
    """Redirect bare domain to landing page."""
    return RedirectResponse(url="/app/landing.html")

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

class QueryRequest(BaseModel):
    query:           str
    category_filter: Optional[str] = None  # e.g. "deployment", "requirements"


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

async def pg_ingest_chunks(chunks: list, db_pool: asyncpg.Pool):
    """
    Embeds and bulk-inserts parsed document chunks into vector_chunks.
    Replaces the old LocalVectorStore.add_chunks() path for /index-folder.
    """
    if not chunks:
        return

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

            try:
                async with db_pool.acquire() as conn:
                    # CRITICAL FIX: SET LOCAL inside a transaction prevents RLS
                    # tenant context from leaking to the next connection pool user.
                    async with conn.transaction():
                        await conn.execute(
                            f"SET LOCAL app.current_tenant_id = '{TENANT_NAMESPACE_UUID}'"
                        )
                        await conn.execute(
                            """
                            INSERT INTO tenant_redwood_inference_prod.vector_chunks
                                (event_id, tenant_id, source_id, source_type,
                                 channel_or_space, content, embedding,
                                 allowed_groups, allowed_users, is_public)
                            VALUES ($1, $2, $3, $4, $5, $6, $7::text::vector, $8, $9, $10)
                            ON CONFLICT DO NOTHING
                            """,
                            event_id,
                            TENANT_UUID,
                            source_id,
                            source_type,
                            channel_or_space,
                            content,
                            emb_str,
                            [],
                            [],
                            True,
                        )
            except Exception as e:
                print(f"  ⚠ DB insert failed for chunk {source_id}: {e}")

# ─── Background Indexing Task ─────────────────────────────────────────────────

def run_indexing(folder_path: str, db_pool: asyncpg.Pool):
    """
    Synchronous wrapper for the async indexing pipeline.
    Runs in a FastAPI BackgroundTask thread pool thread.
    """
    asyncio.run(_async_run_indexing(folder_path, db_pool))


async def _async_run_indexing(folder_path: str, db_pool: asyncpg.Pool):
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
        skip_dirs = {
            ".git", "node_modules", "dist", "build",
            ".venv", "venv", "env", ".gemini", "__pycache__",
        }

        all_files = []
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in doc_exts or ext in video_exts:
                    all_files.append(os.path.join(root, file))

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
            # Skip large non-video files > 1MB
            if ext not in video_exts and file_size > 1024 * 1024:
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
                await pg_ingest_chunks(chunks, db_pool)
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
def index_folder(req: IndexRequest, background_tasks: BackgroundTasks):
    if not os.path.exists(req.folder_path):
        raise HTTPException(status_code=404, detail="Folder path does not exist.")
    if indexing_status["is_indexing"]:
        raise HTTPException(status_code=400, detail="Indexing already in progress.")
    background_tasks.add_task(run_indexing, req.folder_path, app.state.db_pool)
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


@app.get("/ingest-status")
def get_ingest_status():
    """Poll connector ingestion progress."""
    return _connector_status


@app.get("/status")
async def get_status():
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(
                f"SET app.current_tenant_id = '{TENANT_NAMESPACE_UUID}'"
            )
            count = await conn.fetchval(
                "SELECT count(*) FROM tenant_redwood_inference_prod.vector_chunks"
            )
            rows = await conn.fetch(
                "SELECT DISTINCT source_id FROM tenant_redwood_inference_prod.vector_chunks"
            )
        return {
            "indexed_chunks_count": count,
            "indexed_files": [r["source_id"] for r in rows],
        }
    except Exception as e:
        return {"indexed_chunks_count": 0, "indexed_files": [], "error": str(e)}


@app.post("/clear")
async def clear_index():
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(
                f"SET app.current_tenant_id = '{TENANT_NAMESPACE_UUID}'"
            )
            await conn.execute(
                "DELETE FROM tenant_redwood_inference_prod.vector_chunks"
            )
            await conn.execute(
                "DELETE FROM tenant_redwood_inference_prod.graph_edges"
            )
            await conn.execute(
                "DELETE FROM tenant_redwood_inference_prod.graph_nodes"
            )
        _file_hash_cache.clear()
        return {"status": "success", "message": "Postgres index cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear index: {e}")

# ─── Identity & Auth Endpoints ─────────────────────────────────────────────────────

@app.get("/me")
async def get_me(user: UserIdentity = Depends(get_current_user)):
    """Return the currently authenticated user identity."""
    return {"email": user.email, "domain": user.domain, "is_admin": user.is_admin}


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
    Step 1: Redirect the user to Slack's OAuth consent screen.
    Called when user clicks "Connect Slack" in the UI.
    """
    tenant_id = getattr(request.state, "tenant_id", TENANT_NAMESPACE_UUID)
    redirect_uri = str(request.base_url).rstrip("/") + "/slack/oauth/callback"
    try:
        auth_url = _slack_oauth.build_authorization_url(tenant_id, redirect_uri)
        return RedirectResponse(url=auth_url, status_code=302)
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

    # ── 3. Build parameterized hybrid SQL with permission + category filters ────────
    # $1 = embedding, $2 = user_email, $3 = user_domain, $4+ = keywords
    # Permission clause: return chunk if public OR user is in allowed_users OR
    # user's domain is in allowed_groups.
    permission_clause = (
        "(is_public = true "
        "OR $2 = ANY(allowed_users) "
        "OR $3 = ANY(allowed_groups))"
    )

    # Category filter — parameterized to prevent SQL injection.
    # $4 is reserved for the category when active; keywords start at $5 then.
    if req.category_filter:
        cat_value = req.category_filter.lower().strip()
        category_clause = "AND document_category = $4"
        base_param_idx  = 5   # keywords start at $5
        query_params_base: list = [query_emb_str, user_email, user_domain, cat_value]
    else:
        category_clause = ""
        base_param_idx  = 4   # keywords start at $4
        query_params_base = [query_emb_str, user_email, user_domain]

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
            LIMIT 30
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
        LIMIT 4;
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
            # fetch_top_experts uses the connection directly, so it needs SET LOCAL active.
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

    if not retrieved_chunks:
        return {
            "query": req.query,
            "response": "No relevant organizational history found within the indexed knowledge base.",
            "citations": [],
            "retrieved_context": [],
            "experts": experts,
            "latency_seconds": round(time.perf_counter() - start_time, 4),
        }

    # ── 5. Assemble LLM prompt with context + expert grounding ───────────────
    context_str = ""
    for idx, chunk in enumerate(retrieved_chunks):
        context_str += (
            f"--- CONTEXT CHUNK {idx+1} "
            f"(Source: {chunk['source']}, Location: {chunk['location']}) ---\n"
            f"{chunk['content']}\n\n"
        )

    # Inject graph experts into prompt so LLM can attribute expertise
    expert_note = ""
    if experts:
        expert_lines = ", ".join(
            [f"{e['name']} (score: {e['score']})" for e in experts]
        )
        expert_note = (
            f"\n\nIDENTIFIED DOMAIN EXPERTS (from interaction graph): {expert_lines}."
            " Reference these individuals when appropriate for follow-up."
        )

    system_instructions = (
        "You are an elite, highly accurate Knowledge Transfer assistant.\n"
        "Your task is to answer the user query based ONLY on the Context provided below.\n"
        "\n"
        "STRICT RULES — follow these exactly:\n"
        "1. Do not use any external knowledge. If the context does not contain the answer, "
        "say exactly: 'I cannot find the answer in the provided documents/videos.'\n"
        "2. Only state information directly supported by the context chunks.\n"
        "3. CITATION FORMAT IS MANDATORY. After every factual statement append:\n"
        "   [Citation: source_id, location]\n"
        "   Use the exact source_id and location from the context chunk header.\n"
        "4. NEVER meta-comment. Do NOT write 'The provided text...', 'Based on context...', "
        "'The context mentions...'. Answer directly and factually.\n"
        "5. Do NOT restate the question.\n"
        "\n"
        "EXAMPLE:\n"
        "CONTEXT CHUNK 1 (Source: msg_9912, Location: eng-infra):\n"
        "Jane: We migrated auth caching from standalone Redis to an isolated Redis cluster.\n"
        "USER QUERY: What caching changes occurred?\n"
        "RESPONSE: Auth caching was migrated from standalone Redis to an isolated Redis "
        "cluster [Citation: msg_9912, eng-infra].\n"
        "END EXAMPLE"
    )

    full_prompt = (
        f"{system_instructions}\n\n"
        f"CONTEXT:\n\"\"\"\n{context_str}\"\"\"\n\n"
        f"USER QUERY: {req.query}\n\nRESPONSE:"
    )

    # ── 6. LLM generation ─────────────────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        payload = {
            "model": MODEL_NAME,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 2048,      # 2048 fits in 8GB RAM with Docker overhead
                "num_predict": 400,   # keep responses concise and fast
            },
        }
        try:
            response = await client.post(OLLAMA_GEN_URL, json=payload, timeout=150.0)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Ollama synthesis failure: {response.text}",
                )

            raw_response = response.json().get("response", "")

            # ── 7. Grounding firewall ─────────────────────────────────────────
            validated_response = validate_response(raw_response, retrieved_chunks)

            accessed_files = [c["source"] for c in retrieved_chunks]
            write_audit_log(user_email, req.query, accessed_files)

            citations = extract_citations(validated_response)

            payload = {
                "query": req.query,
                "response": _sanitize(validated_response),
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