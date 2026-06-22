import os

# Load .env before anything else — handles "KEY = VALUE" spacing that shell
# `source` can't parse. This ensures GOOGLE_CLIENT_ID, SLACK_CLIENT_ID, etc.
# are always available regardless of how uvicorn was launched.
from dotenv import load_dotenv
load_dotenv(override=True)  # override=True: .env settings take priority over environment variables set in the shell

import glob
import asyncio
import hashlib
import json
import re
import time
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, status, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
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

def _write_audit_log_sync(log_entry: dict):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audit_file = os.path.join(base_dir, "data", "audit_log.json")
    try:
        # Append-only NDJSON format: extremely fast, O(1) time
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"Failed to write audit log: {e}")

async def write_audit_log(user_email: str, query: str, accessed_files: list):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "user_email": user_email,
        "query": query,
        "accessed_files": list(set(accessed_files)),
    }
    await asyncio.to_thread(_write_audit_log_sync, log_entry)

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

# ─── ONNX Reranker ────────────────────────────────────────────────────────────
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
USE_RERANKER = True
_reranker = None

class ONNXReranker:
    def __init__(self, model_name):
        from transformers import AutoTokenizer
        from optimum.onnxruntime import ORTModelForSequenceClassification
        import logging
        logging.getLogger("optimum").setLevel(logging.ERROR)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = ORTModelForSequenceClassification.from_pretrained(model_name, export=True)

    def predict(self, pairs):
        inputs = self.tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
        outputs = self.model(**inputs)
        logits = outputs.logits.detach().cpu().numpy()
        if logits.ndim > 1 and logits.shape[1] == 1:
            return logits.squeeze(-1)
        return logits

def get_reranker():
    global _reranker
    if _reranker is None and USE_RERANKER:
        try:
            print(f"  [reranker] Loading {RERANKER_MODEL} via ONNX...")
            _reranker = ONNXReranker(RERANKER_MODEL)
            print(f"  [reranker] Loaded ONNX model.")
        except Exception as e:
            print(f"  [reranker] Failed to load ONNX model: {e}")
            _reranker = None
    return _reranker

DB_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
TENANT_NAMESPACE_UUID = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
TENANT_UUID = uuid.UUID(TENANT_NAMESPACE_UUID)

# ─── Subscription tier configuration ──────────────────────────────────────────
# Two-tier model. Free tier enforces a 10MB per-file upload limit; Pro bypasses
# it. Stripe integration is stubbed — keys/columns land tomorrow. Until then,
# every tenant is treated as "free" and the admin whitelist in auth.py grants
# the bypass. Do NOT enable production billing without filling the placeholders
# below AND adding the stripe_customer_id / stripe_subscription_id columns to
# the tenant_registry table.
STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")        # placeholder
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")    # placeholder
STRIPE_PRICE_ID_PRO    = os.getenv("STRIPE_PRICE_ID_PRO", "")      # placeholder
FREE_TIER_MAX_BYTES    = 10 * 1024 * 1024  # 10 MB

MODEL_NAME_EMBED = "nomic-embed-text"
OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embeddings"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"   # chat API, proper system/user roles

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


# ─── Connector ingestion status (shared across all connector types) ───────────
_connector_status: Dict[str, Any] = {
    "is_running": False,
    "connector": None,
    "ingested": 0,
    "skipped": 0,
    "errors": [],
    "message": "idle",
}


# Per-tenant sync lock — prevents concurrent sync_all_tenants() for the same tenant.
# Keys: tenant_id string. Value: True while a sync is in progress.
_active_syncs: Dict[str, bool] = {}

# Per-tenant tier cache. Key: tenant_id. Value: "free" or "pro".
# Populated by _resolve_tenant_tier(); avoids hitting the DB on every upload.
_tier_cache: Dict[str, str] = {}


async def _resolve_tenant_tier(conn, tenant_id: str) -> str:
    """
    Resolve the subscription tier for a tenant.

    Skeleton implementation: returns "free" until stripe_customer_id /
    stripe_subscription_id columns are added to tenant_registry and a real
    Stripe webhook populates them. The signature is stable so the caller
    (and the future Stripe webhook route) don't need to change once the
    columns exist.

    Returns one of: "free", "pro".
    """
    if tenant_id in _tier_cache:
        return _tier_cache[tenant_id]
    tier = "free"  # placeholder — every tenant starts on free tier
    _tier_cache[tenant_id] = tier
    return tier

# File-hash deduplication cache (key: file path, value: md5 hash). Cleared by
# /clear and /admin/clear-my-data so a re-ingest re-embeds rather than being
# short-circuited as a duplicate.
_file_hash_cache: Dict[str, str] = {}


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
        app.state.db_pool = await asyncpg.create_pool(DB_URL, min_size=5, max_size=20, statement_cache_size=0)
        print("🚀 DB connection pool established.")
    except Exception as e:
        print(f"❌ Critical: DB pool failed: {e}")
        import sys; sys.exit(1)

    # Wire the DB pool into the auth dependency (avoids circular import)
    get_current_user._db_pool = app.state.db_pool

    # ── Ollama & Reranker warmup ───────────────────────────────────────────────────────────
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
            
        print("🔥 Pre-loading ONNX Cross-Encoder Reranker...")
        get_reranker()

    _warmup_task = asyncio.create_task(_warmup_ollama())

    # ── Background sync loop ───────────────────────────────────────────────────────
    _sync_task = asyncio.create_task(
        sync_scheduler.start_sync_loop(app.state.db_pool)
    )

    # ── Background IMAP sync loop ──────────────────────────────────────────────────
    from backend.imap_connector import start_imap_sync_loop
    _imap_sync_task = asyncio.create_task(
        start_imap_sync_loop(app.state.db_pool)
    )

    yield  # ← app is running here

    # Shutdown
    _sync_task.cancel()
    _imap_sync_task.cancel()
    _warmup_task.cancel()
    try:
        await _sync_task
    except asyncio.CancelledError:
        pass
    try:
        await _imap_sync_task
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
    from starlette.datastructures import MutableHeaders

    class ASGINoCacheMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            path = scope.get("path", "")
            if path.startswith('/app') or path.startswith('/app/'):
                async def send_wrapper(message):
                    if message["type"] == "http.response.start":
                        headers = MutableHeaders(scope=message)
                        headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                        headers['Pragma']        = 'no-cache'
                        headers['Expires']       = '0'
                    await send(message)
                await self.app(scope, receive, send_wrapper)
            else:
                await self.app(scope, receive, send)

    app.add_middleware(ASGINoCacheMiddleware)
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

@app.get("/", include_in_schema=False)
async def _root_redirect():
    """Redirect bare domain to landing page."""
    return RedirectResponse(url="/app/landing.html")


@app.get("/client-config", include_in_schema=False)
async def get_client_config():
    """
    Public endpoint — no auth required.
    Provides frontend JavaScript with the Supabase URL and anon key so that
    these values are never hard-coded in HTML files.

    Security note: The Supabase anon key is intentionally semi-public — it
    grants read-only access subject to Row Level Security policies. It is
    safe to serve here because it cannot be used to bypass RLS or gain
    elevated privileges. The real secrets (service role key, Fernet key,
    OAuth client secrets) are never exposed through any client-facing endpoint.
    """
    from backend.auth import SUPABASE_URL, SUPABASE_ANON
    return {
        "supabase_url":  SUPABASE_URL,
        "supabase_anon": SUPABASE_ANON,
        "SUPABASE_URL":  SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_ANON,
        "site_url":      os.getenv("SITE_URL", "http://127.0.0.1:8000"),
    }



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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(_frontend_dir, "images", "smriti_logo_32.png")
    if not os.path.isfile(favicon_path):
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(favicon_path, media_type="image/png")


# ─── CORS ────────────────────────────────────────────────────────────────────
# SECURITY: wildcard CORS is forbidden outside local/dev environments.
# Set CORS_ORIGINS to a comma-separated list of explicit origins in production,
# e.g. "https://smriti.one,https://www.smriti.one".
_raw_cors     = os.getenv("CORS_ORIGINS", "*")
_env_name     = os.getenv("KGF_ENV", "local").lower()
_is_local_env = _env_name in ("local", "dev", "development")
_ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_cors.split(",") if o.strip()]

if "*" in _ALLOWED_ORIGINS:
    if not _is_local_env:
        raise RuntimeError(
            "SECURITY: CORS_ORIGINS='*' is not allowed outside local/dev environments. "
            f"Current KGF_ENV={_env_name!r}. "
            "Set CORS_ORIGINS to an explicit comma-separated list of allowed origins "
            "(e.g. 'https://smriti.one') in your .env file."
        )
    else:
        # In local dev environment, expand "*" to allow loopbacks/local origins to prevent Starlette AssertionError
        _ALLOWED_ORIGINS = [
            "http://localhost:3000",
            "http://localhost:3999",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3999",
            "http://127.0.0.1:8000"
        ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Dev-User-Email"],
)
# ─── Stats and Rate Limiting monitoring ───────────────────────────────────────
import threading

class TokenBucketRateLimiter:
    def __init__(self, capacity: int, leak_rate: float):
        self.capacity = capacity
        self.leak_rate = leak_rate
        self.buckets = {}  # ip: {"tokens": float, "last_update": float}
        self.lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        with self.lock:
            now = time.time()
            if ip not in self.buckets:
                self.buckets[ip] = {
                    "tokens": float(self.capacity) - 1.0,
                    "last_update": now
                }
                return True

            bucket = self.buckets[ip]
            elapsed = now - bucket["last_update"]
            bucket["tokens"] = min(
                float(self.capacity),
                bucket["tokens"] + elapsed * self.leak_rate
            )
            bucket["last_update"] = now

            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True
            else:
                return False

# 100 requests per minute limit for general routes
global_limiter = TokenBucketRateLimiter(capacity=100, leak_rate=100/60.0)
# 10 requests per minute limit for query, ingestion, etc.
heavy_limiter = TokenBucketRateLimiter(capacity=10, leak_rate=10/60.0)


class StatsTracker:
    def __init__(self):
        self.lock = threading.Lock()
        # endpoint_stats format: {endpoint: {calls: X, total_latency_ms: Y, status_codes: {200: Z, ...}, errors: W}}
        self.endpoint_stats = {}
        self.recent_errors = []

    def record_request(self, endpoint: str, latency_ms: float, status_code: int, error_msg: str = None):
        with self.lock:
            if endpoint not in self.endpoint_stats:
                self.endpoint_stats[endpoint] = {
                    "calls": 0,
                    "total_latency_ms": 0.0,
                    "status_codes": {},
                    "errors": 0
                }
            stats = self.endpoint_stats[endpoint]
            stats["calls"] += 1
            stats["total_latency_ms"] += latency_ms
            
            sc_str = str(status_code)
            stats["status_codes"][sc_str] = stats["status_codes"].get(sc_str, 0) + 1
            
            if status_code >= 400 or error_msg:
                stats["errors"] += 1
                if error_msg:
                    self.recent_errors.append({
                        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        "endpoint": endpoint,
                        "error": error_msg
                    })
                    if len(self.recent_errors) > 50:
                        self.recent_errors.pop(0)

stats_tracker = StatsTracker()


TRUST_PROXY = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

class ASGIStatsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Skip tracking static frontend and landing page assets/favicon
        is_api = not (path.startswith("/app/") or path == "/app" or path == "/favicon.ico" or path == "/" or path in ("/auth-config", "/client-config"))
        
        if not is_api:
            await self.app(scope, receive, send)
            return

        # Rate limiting (bypass for local tests)
        headers = {k.decode("utf-8").lower(): v.decode("utf-8") for k, v in scope.get("headers", [])}
        client_ip = ""
        if TRUST_PROXY:
            client_ip = headers.get("x-real-ip", "").strip()
            if not client_ip:
                xff = headers.get("x-forwarded-for", "").strip()
                if xff:
                    client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"

        is_local = client_ip in ("127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1")
        if not is_local:
            if path in ("/query", "/index-folder", "/ingest-slack"):
                if not heavy_limiter.is_allowed(client_ip):
                    await send({
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [(b"content-type", b"application/json")]
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"detail": "Too many requests. Please slow down search and ingestion operations."}',
                        "more_body": False
                    })
                    return
            else:
                if not global_limiter.is_allowed(client_ip):
                    await send({
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [(b"content-type", b"application/json")]
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"detail": "Too many requests. Please wait a moment."}',
                        "more_body": False
                    })
                    return

        start_time = time.time()
        status_code_container = [500]
        
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code_container[0] = message["status"]
            await send(message)

        error_msg = None
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            error_msg = str(e)
            raise e
        finally:
            latency_ms = (time.time() - start_time) * 1000.0
            norm_path = path
            if path.startswith("/org/invites/"):
                norm_path = "/org/invites/{invite_id}"
            elif path.startswith("/org/members/"):
                norm_path = "/org/members/{user_id}"
            
            stats_tracker.record_request(norm_path, latency_ms, status_code_container[0], error_msg)

app.add_middleware(ASGIStatsMiddleware)


# ─── Request Models ───────────────────────────────────────────────────────────



class ChatMessage(BaseModel):
    role:            str                 # "user" or "assistant"
    content:         str

class QueryRequest(BaseModel):
    query:           str
    top_k:           int = 8             # number of chunks to retrieve (capped at 8 in route)
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
    
    # 1. Concurrently generate embeddings (bounded to max 5 concurrent requests)
    sem = asyncio.Semaphore(5)
    async def embed_chunk(client, chunk):
        async with sem:
            try:
                resp = await client.post(
                    OLLAMA_EMBED_URL,
                    json={"model": MODEL_NAME_EMBED, "prompt": chunk["content"]},
                    timeout=20.0,
                )
                resp.raise_for_status()
                return resp.json().get("embedding", [0.0] * 768)
            except Exception as e:
                print(f"  ⚠ Embedding failed for chunk from {chunk.get('source')}: {e}")
                return [0.0] * 768

    async with httpx.AsyncClient() as client:
        tasks = [embed_chunk(client, chunk) for chunk in chunks]
        embeddings = await asyncio.gather(*tasks)

    # 2. Bulk insert inside a single database transaction/connection
    try:
        source_ids = list(set(str(chunk.get("source") or "").replace("\x00", "") for chunk in chunks if chunk.get("source")))
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL app.current_tenant_id = '{effective_tenant_id}'"
                )
                if source_ids:
                    await conn.execute(
                        """
                        DELETE FROM tenant_redwood_inference_prod.vector_chunks
                        WHERE tenant_id = $1::uuid AND source_id = ANY($2::text[])
                        """,
                        effective_tenant_uuid,
                        source_ids
                    )
                for chunk, emb in zip(chunks, embeddings):
                    emb_str = f"[{','.join(map(str, emb))}]"
                    event_id = uuid.uuid4()
                    source_id = str(chunk.get("source") or event_id).replace("\x00", "")
                    source_type = str(chunk.get("type") or "document").replace("\x00", "")
                    channel_or_space = str(chunk.get("location") or "local").replace("\x00", "")
                    content = str(chunk.get("content") or "").replace("\x00", "")
                    author_id = str(chunk.get("author_id") or chunk.get("author") or "system").replace("\x00", "")
                    document_title = str(chunk.get("title") or chunk.get("section") or source_id).replace("\x00", "")

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
        print(f"  ⚠ Bulk DB insert failed: {e}")



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
    Supported: PDF, TXT, MD, DOCX, CSV (up to 50 MB for Pro tier; 10 MB for Free tier).

    Tier gate: the admin bypass whitelist (see auth.ADMIN_BYPASS_EMAILS) and Pro
    tier tenants are exempt from the 10MB Free Tier limit.
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_INGEST_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_INGEST_EXTS))}",
        )

    # ── Subscription tier check: enforce Free Tier 10MB cap ─────────────────
    # Admin bypass whitelist always skips the gate. Pro tenants skip the gate.
    # The size check uses Content-Length when present (cheap) and falls back to
    # measuring the streamed bytes (works for chunked uploads where no length
    # header is sent).
    tier = "free"  # default — only ever changed to "pro" below
    if not user.is_admin_bypass:
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            try:
                async with app.state.db_pool.acquire() as conn:
                    tier = await _resolve_tenant_tier(conn, tenant_id)
            except Exception as e:
                # DB hiccup → fail closed on the safe side (treat as free)
                print(f"  ⚠ tier lookup failed, defaulting to free: {e}")
                tier = "free"

        if tier == "free":
            declared_size = request.headers.get("content-length")
            if declared_size is not None:
                try:
                    if int(declared_size) > FREE_TIER_MAX_BYTES:
                        raise HTTPException(
                            status_code=403,
                            detail="File size exceeds the 10MB Free Tier threshold. Please upgrade your workspace.",
                        )
                except ValueError:
                    pass  # malformed Content-Length — fall through to streamed check

    # Write upload to a temp file (parse_document needs a real path)
    bytes_written = 0
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            # Streamed size check for Free tier (when Content-Length was missing
            # or we skipped the upfront check due to admin bypass). The check
            # is short-circuited for admin-bypass and Pro tenants.
            if (not user.is_admin_bypass) and tier == "free" and bytes_written > FREE_TIER_MAX_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(
                    status_code=403,
                    detail="File size exceeds the 10MB Free Tier threshold. Please upgrade your workspace.",
                )
            tmp.write(chunk)
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


def _send_invite_email_sync(to_email: str, invite_link: str, company_name: str):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_sender = os.getenv("SMTP_SENDER", "sutra@smriti.one")

    if not (smtp_host and smtp_port and smtp_user and smtp_password):
        print("[SMTP] Credentials not set in .env. Skipping invite email dispatch (manual fallback available).")
        return

    print(f"[SMTP] Sending invite email to {to_email}...")
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Invitation to join {company_name} on Smriti"
        msg['From'] = smtp_sender
        msg['To'] = to_email

        text = f"You have been invited to join the {company_name} workspace on Smriti.\n\nClick the link below to accept the invitation:\n{invite_link}"
        
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Invitation to join {company_name}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #030014; color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #030014;">
    <tr>
      <td align="center" style="padding: 40px 10px;">
        <table width="550" border="0" cellspacing="0" cellpadding="0" style="background-color: #080710; border: 1px solid #1f1a3a; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
          <tr>
            <td style="background: linear-gradient(135deg, #1e1b4b 0%, #31106a 100%); padding: 32px 40px; text-align: center; border-bottom: 1px solid #2e1065;">
              <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 800;">Smriti Workspace Invitation</h1>
            </td>
          </tr>
          <tr>
            <td style="padding: 40px; font-size: 16px; line-height: 1.6; color: #e2e8f0;">
              <p>Hello,</p>
              <p>You have been invited to join the <strong>{company_name}</strong> workspace on Smriti as a team member.</p>
              <p style="margin-top: 30px; margin-bottom: 30px; text-align: center;">
                <a href="{invite_link}" style="display: inline-block; padding: 12px 24px; background-color: #6366f1; color: #ffffff; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);">Accept Invitation</a>
              </p>
              <p style="font-size: 13px; color: #64748b; text-align: center; margin-top: 20px;">
                Or copy and paste this link into your browser:<br>
                <a href="{invite_link}" style="color: #818cf8; text-decoration: underline;">{invite_link}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color: #02000a; border-top: 1px solid #1e1b4b; padding: 20px 40px; text-align: center; font-size: 12px; color: #64748b;">
              <p style="margin: 0;">Smriti — Capture requirements, prevent regression.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_sender, [to_email], msg.as_string())
        print(f"[SMTP] Invite email successfully sent to {to_email}.")
    except Exception as e:
        print(f"[SMTP] Failed to send email to {to_email}: {e}")


@app.post("/org/invite")
async def invite_org_member(
    req: OrgInviteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
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
    if role == "admin":
        raise HTTPException(
            status_code=400,
            detail="There can only be one admin per organization. You cannot invite someone with the admin role."
        )
    if role != "member":
        raise HTTPException(status_code=400, detail="Invalid role specified. Use 'member'.")

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

            # Create or update invite (RETURNING id so we can form the URL and email)
            invite_id = await conn.fetchval(
                """
                INSERT INTO public.org_invites (tenant_id, invited_email, role, invited_by)
                VALUES ($1::uuid, $2, $3, $4)
                ON CONFLICT ON CONSTRAINT uq_org_invites_tenant_email
                DO UPDATE SET
                    role = EXCLUDED.role,
                    created_at = NOW(),
                    accepted_at = NULL
                RETURNING id
                """,
                tenant_uuid,
                invited_email,
                role,
                user_uuid,
            )

            # Retrieve company name for email customization
            company_name = await conn.fetchval(
                "SELECT company_name FROM public.tenant_registry WHERE tenant_id = $1::uuid",
                tenant_uuid,
            ) or "Smriti Workspace"

        invite_link = f"https://smriti.one/app/auth.html?invite={invite_id}"
        
        # Enqueue SMTP email sending task
        background_tasks.add_task(_send_invite_email_sync, invited_email, invite_link, company_name)
        
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

    # Clamp top_k to a safe range — context window budget allows up to 8 chunks at 700 chars each
    effective_top_k = max(1, min(req.top_k, 8))

    # ── 4. Execute retrieval + RRF + Reranker + graph expert query ────────────────
    retrieved_chunks = []
    experts = []

    try:
        async with app.state.db_pool.acquire() as conn:
            # CRITICAL: Use SET LOCAL inside a transaction so the tenant_id
            # resets automatically when the connection is returned to the pool.
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                
                sem_sql = f"""
                    SELECT event_id, source_id, source_type, channel_or_space, content, author_id, document_category,
                           (1 - (embedding <=> $1::vector)) AS semantic_score,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector ASC) AS sem_rank
                    FROM tenant_redwood_inference_prod.vector_chunks
                    WHERE {permission_clause}
                    {category_clause}
                    LIMIT 60
                """
                
                if not keywords:
                    kw_rows = []
                else:
                    kw_sql = f"""
                        WITH kw_scored AS (
                            SELECT event_id, source_id, source_type, channel_or_space, content, author_id, document_category,
                                   (1 - (embedding <=> $1::vector)) AS semantic_score,
                                   ({text_score_expr}) AS text_score
                            FROM tenant_redwood_inference_prod.vector_chunks
                            WHERE {permission_clause}
                            {category_clause}
                            ORDER BY text_score DESC
                            LIMIT 60
                        )
                        SELECT *, ROW_NUMBER() OVER (ORDER BY text_score DESC) AS kw_rank
                        FROM kw_scored
                    """

                async def fetch_sem():
                    return await conn.fetch(sem_sql, *query_params_base)
                
                async def fetch_kw():
                    if not keywords: return []
                    return await conn.fetch(kw_sql, *query_params)
                
                sem_rows = await fetch_sem()
                kw_rows = await fetch_kw()
                
                # RRF Fusion
                scores = {}
                row_map = {}
                k = 60
                for r in sem_rows:
                    eid = r["event_id"]
                    scores[eid] = scores.get(eid, 0) + 1.0 / (k + r["sem_rank"])
                    if eid not in row_map:
                        row_map[eid] = dict(r)
                for r in kw_rows:
                    eid = r["event_id"]
                    scores[eid] = scores.get(eid, 0) + 1.0 / (k + r["kw_rank"])
                    if eid not in row_map:
                        row_map[eid] = dict(r)
                        
                # Sort by RRF score and deduplicate by content
                candidates = []
                seen_contents = set()
                for eid, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                    c = row_map[eid]
                    normalized_content = c["content"].strip().lower()
                    if normalized_content in seen_contents:
                        continue
                    seen_contents.add(normalized_content)
                    candidates.append({
                        "source":             c["source_id"],
                        "type":              c["source_type"],
                        "location":          c["channel_or_space"],
                        "content":           c["content"],
                        "author_id":         c["author_id"],
                        "document_category": c["document_category"],
                        "score":             score,
                        "semantic_score":    float(c.get("semantic_score", 0)),
                    })
                    if len(candidates) >= 20:
                        break

                experts_raw = await fetch_top_experts(conn, top_n=3)

            # Reranker phase
            reranker = get_reranker()
            if reranker and candidates:
                pairs = [[req.query, c["content"]] for c in candidates]
                logits = await asyncio.to_thread(reranker.predict, pairs)
                if not hasattr(logits, "__iter__"):
                    logits = [logits]
                for c, logit in zip(candidates, logits):
                    c["score"] = float(logit) # overwrite score with reranker score
                candidates.sort(key=lambda x: x["score"], reverse=True)
            
            # Final top_k
            retrieved_chunks = candidates[:effective_top_k]
            print("  [query] Retrieved chunks passing to LLM:")
            for idx, c in enumerate(retrieved_chunks):
                print(f"    [{idx+1}] Source: {c['source']} | Score: {c.get('score')} | SemScore: {c.get('semantic_score')} | Content: {c['content'][:100]!r}")

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
        if max_semantic_score < 0.40:
            print(f"  [query] Top semantic score {max_semantic_score:.4f} < 0.40 threshold. Clearing retrieved chunks.")
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
    # Budget: num_ctx=2048 - 400 (reply) - 200 (prompts) = ~1448 tokens = ~5792 chars for all chunks
    MAX_CHUNK_CHARS = min(700, max(250, 5600 // max(len(retrieved_chunks), 1)))

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
                "num_ctx": 2048,
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

            print(f"  [query] Raw response:\n{raw_response}\n")
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
            await write_audit_log(user_email, req.query, accessed_files)

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

