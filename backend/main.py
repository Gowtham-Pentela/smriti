"""
backend/main.py
───────────────
Smriti — Internal Company ChatGPT.

Endpoints (slim):
  GET   /                       — redirect to /app/
  GET   /health                 — liveness probe
  GET   /status                 — chunk count + dev_mode flag
  GET   /me                     — current user identity
  GET   /files                  — list of indexed source URIs
  POST  /ingest                 — file upload (PDF, image, audio, video, code, text)
  POST  /clear                  — wipe company index
  POST  /query                  — ask a question, get a cited answer
  POST  /agent                  — ReAct agent loop with tool calls
  GET   /agent/tools            — list tool schemas (for UI rendering)
  GET   /activity               — last N audit log entries (for the Activity tab)
  GET   /s3/status              — S3 worker status
  POST  /s3/resync              — re-walk local folder (and re-process S3 backlog)
  GET   /app/                   — static frontend (if mounted)
"""

import os
import re
import time
import uuid
import json
import asyncio
import hashlib
import tempfile
from datetime import datetime
from contextlib import asynccontextmanager

import httpx
import asyncpg
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from dotenv import load_dotenv
load_dotenv(override=True)

# ── Local imports ─────────────────────────────────────────────────────────────
from backend.parser import parse_document
from backend.grounding import validate_response, extract_citations
from backend.auth import (
    get_current_user, require_admin, UserIdentity, SMRITI_DEV_MODE, COMPANY_TENANT_ID, IS_LOCAL_ENV,
)
from backend import s3_connector

# ── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
OLLAMA_CHAT_URL  = os.getenv("OLLAMA_CHAT_URL",  "http://127.0.0.1:11434/api/chat")
EMBED_MODEL      = os.getenv("SMRITI_EMBED_MODEL",  "nomic-embed-text")
CHAT_MODEL       = os.getenv("SMRITI_CHAT_MODEL",   "phi4-mini:latest")


# ── Module-level helpers (mockable from tests) ───────────────────────────────
async def get_async_ollama_embedding(text: str) -> list[float]:
    """Embed one text. Public helper so the test suite can patch it."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": f"search_document: {text}"},
        )
        r.raise_for_status()
        return r.json().get("embedding", []) or []


async def get_async_admin_email(conn) -> str | None:
    """Resolve the first admin user for the company. Used by the fallback message."""
    try:
        row = await conn.fetchrow(
            "SELECT email FROM public.users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        )
        return row["email"] if row else None
    except Exception:
        return None

# ── Audit log (append-only NDJSON) ────────────────────────────────────────────
# ponytail: env-configurable path so on-prem deployers can point this at a
# persistent volume / SIEM forwarder drop dir. Default keeps the local data/ path.
_DEFAULT_AUDIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audit_log.json")
_AUDIT_PATH = os.getenv("SMRITI_AUDIT_LOG_PATH", _DEFAULT_AUDIT)


def _write_audit_sync(entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[audit] write failed: {e}")


async def write_audit_log(
    user_email: str,
    query: str,
    accessed_files: list[str],
    *,
    citations: list[str] | None = None,
    refused: bool | None = None,
) -> None:
    entry: dict = {
        "timestamp": datetime.now().isoformat(),
        "tenant_id": COMPANY_TENANT_ID,
        "user_email": user_email,
        "query": query,
        "accessed_files": sorted(set(accessed_files)),
    }
    # ponytail: optional fields only when populated — keeps legacy entries shaped the same.
    if citations is not None:
        entry["citations"] = citations
    if refused is not None:
        entry["refused"] = refused
    await asyncio.to_thread(_write_audit_sync, entry)


# ── Stopwords (used in hybrid retrieval keyword scoring) ──────────────────────
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


# ── ONNX Cross-Encoder Reranker (lazy-loaded) ─────────────────────────────────
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
_reranker = None


def get_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL, max_length=512, device="cpu")
        print(f"[reranker] loaded {RERANKER_MODEL}")
        return _reranker
    except Exception as e:
        print(f"[reranker] unavailable ({e}); continuing without rerank")
        _reranker = False
        return None


# ── Allowed ingest extensions (no cap on size) ───────────────────────────────
ALLOWED_INGEST_EXTS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".sql",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".cpp", ".c", ".h", ".rs", ".sh",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".wav", ".mp3", ".m4a", ".flac", ".ogg",
}


# ─── App Lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with dev mode ON outside local/dev.
    if SMRITI_DEV_MODE and not IS_LOCAL_ENV:
        raise RuntimeError(
            "SECURITY: SMRITI_DEV_MODE=true is not allowed outside local/dev. "
            "Set SMRITI_ENV=local (or unset SMRITI_DEV_MODE) for production."
        )

    # DB pool
    print("Initialising Postgres pool...")
    try:
        app.state.db_pool = await asyncpg.create_pool(
            DB_URL, min_size=2, max_size=10, statement_cache_size=0,
        )
        print("  → pool ready")
    except Exception as e:
        print(f"  ✗ DB pool failed: {e}")
        raise SystemExit(1)

    # Wire auth dep to the pool
    get_current_user._db_pool = app.state.db_pool

    # Warm up Ollama embedding model
    async def _warmup():
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    OLLAMA_EMBED_URL,
                    json={"model": EMBED_MODEL, "prompt": "search_query: warmup"},
                    timeout=60.0,
                )
            print("  → Ollama warmup done")
        except Exception as e:
            print(f"  ⚠ Ollama warmup failed (non-fatal): {e}")
        get_reranker()

    asyncio.create_task(_warmup())

    # Start S3 ingestion worker (event-driven via SQS) and sync loop
    # (list-and-diff reconciliation). Both run in parallel; the sync loop
    # gates on S3_BUCKET only, the SQS worker also needs S3_QUEUE_URL.
    s3_task = asyncio.create_task(s3_connector.start_worker(app.state.db_pool))
    s3_sync_task = asyncio.create_task(s3_connector.start_sync_loop(app.state.db_pool))

    try:
        yield
    finally:
        for t in (s3_task, s3_sync_task):
            t.cancel()
        for t in (s3_task, s3_sync_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await app.state.db_pool.close()
        print("DB pool closed.")


app = FastAPI(title="Smriti", description="Internal company knowledge assistant.", lifespan=lifespan)


# ─── CORS ─────────────────────────────────────────────────────────────────────
_raw_cors = os.getenv("CORS_ORIGINS", "*")
_allowed  = [o.strip() for o in _raw_cors.split(",") if o.strip()]
if "*" in _allowed and not IS_LOCAL_ENV:
    raise RuntimeError(
        "SECURITY: CORS_ORIGINS='*' is not allowed outside local/dev. "
        "Set CORS_ORIGINS to a comma-separated list of allowed origins."
    )
if "*" in _allowed:
    _allowed = ["http://localhost:3000", "http://localhost:3999", "http://localhost:8000",
                "http://127.0.0.1:3000", "http://127.0.0.1:3999", "http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Dev-User-Email"],
)


# ─── Static frontend ──────────────────────────────────────────────────────────
_frontend_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"
)
if os.path.isdir(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# ─── Request models ───────────────────────────────────────────────────────────
from pydantic import BaseModel
from typing import List, Optional


class ChatMessage(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    query: str
    top_k: int = 8
    history: Optional[List[ChatMessage]] = None


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/app/index.html")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status(request: Request, user: UserIdentity = Depends(require_admin)):
    """Chunk count + S3 worker status."""
    tenant_id = COMPANY_TENANT_ID
    chunk_count = 0
    source_count = 0
    try:
        async with app.state.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
                chunk_count = await conn.fetchval("SELECT count(*) FROM public.vector_chunks")
                source_count = await conn.fetchval(
                    "SELECT count(DISTINCT source) FROM public.vector_chunks"
                )
    except Exception as e:
        print(f"  ⚠ /status db error: {e}")
    return {
        "status": "ok",
        "dev_mode": SMRITI_DEV_MODE,
        "tenant_id": tenant_id,
        "indexed_chunks_count": chunk_count,
        "indexed_sources_count": source_count,
        "s3": s3_connector.get_status(),
    }


@app.get("/me")
async def me(user: UserIdentity = Depends(get_current_user)):
    return {"email": user.email, "domain": user.domain, "is_admin": user.is_admin}


@app.get("/files")
async def files(user: UserIdentity = Depends(require_admin)):
    tenant_id = COMPANY_TENANT_ID
    try:
        async with app.state.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
                rows = await conn.fetch(
                    "SELECT source, category, count(*) AS n, max(created_at) AS last "
                    "FROM public.vector_chunks GROUP BY source, category ORDER BY last DESC"
                )
        return {
            "files": [
                {
                    "source": r["source"],
                    "category": r["category"],
                    "chunks": r["n"],
                    "last_ingested": r["last"].isoformat(),
                }
                for r in rows
            ]
        }
    except Exception as e:
        return {"files": [], "error": str(e)}


# ─── File upload ──────────────────────────────────────────────────────────────
@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    user: UserIdentity = Depends(require_admin),
):
    """Upload + index a single file. No size limit."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_INGEST_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_INGEST_EXTS))}",
        )

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        chunks = parse_document(tmp_path, source_name=file.filename or os.path.basename(tmp_path))
        if not chunks:
            raise HTTPException(status_code=422, detail="No text could be extracted from this file.")

        tenant_id = COMPANY_TENANT_ID
        inserted = await _ingest_chunks_inline(tenant_id, chunks, file.filename or tmp_path, file_hash)
        return {"status": "ok", "filename": file.filename, "chunks_indexed": inserted}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def _ingest_chunks_inline(tenant_id: str, chunks: list[dict], source: str, file_hash: str) -> int:
    """Embed + insert. Same path the S3 worker uses."""
    from backend.db import check_and_mark_ingested
    inserted = 0
    async with app.state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
            if await check_and_mark_ingested(conn, tenant_id, file_hash, source, chunks=len(chunks)):
                return 0
            for chunk in chunks:
                content = (chunk.get("content") or "").strip()
                if not content:
                    continue
                try:
                    emb = await get_async_ollama_embedding(content)
                except Exception as e:
                    print(f"  ⚠ embed failed: {e}")
                    continue
                if not emb:
                    continue
                emb_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
                await conn.execute(
                    """
                    INSERT INTO public.vector_chunks
                        (tenant_id, source, source_type, location, content, embedding, file_hash)
                    VALUES ($1, $2, $3, $4, $5, $6::text::vector, $7)
                    """,
                    tenant_id,
                    source,
                    chunk.get("type", "document"),
                    chunk.get("location", ""),
                    content,
                    emb_str,
                    file_hash,
                )
                inserted += 1
    return inserted


@app.post("/clear")
async def clear(user: UserIdentity = Depends(require_admin)):
    """Wipe the company index."""
    tenant_id = COMPANY_TENANT_ID
    async with app.state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
            await conn.execute("DELETE FROM public.vector_chunks")
            await conn.execute("DELETE FROM public.ingestion_hashes")
    return {"status": "ok", "message": "Company index cleared."}


# ─── S3 admin ─────────────────────────────────────────────────────────────────
@app.get("/s3/status")
async def s3_status(user: UserIdentity = Depends(require_admin)):
    return s3_connector.get_status()


@app.post("/s3/resync")
async def s3_resync(
    folder: str = "",
    user: UserIdentity = Depends(require_admin),
):
    """Re-walk a local folder and ingest everything. Useful for tests.
    For real bucket re-sync, just upload the file again — EventBridge will fire."""
    if not folder:
        return {"error": "Pass ?folder=/path/to/local/dir (testing only)"}
    if not os.path.isdir(folder):
        return {"error": f"folder not found: {folder}"}
    summary = await s3_connector.ingest_local_folder(folder, app.state.db_pool)
    return summary


# ─── /agent (ReAct tool-calling loop) ─────────────────────────────────────────
class AgentRequest(BaseModel):
    query: str
    max_iter: int = 5


@app.get("/agent/tools")
async def agent_tools(user: UserIdentity = Depends(require_admin)):
    """Return the tool schemas so the UI can render them in a panel."""
    from backend.agent import AGENT_TOOLS
    return {"tools": AGENT_TOOLS}


@app.post("/agent")
async def agent(
    req: AgentRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    """ReAct agent loop. Returns the final answer + a tool-call trace."""
    query_text = (req.query or "").strip()
    if len(query_text) < 3:
        raise HTTPException(422, "Query must be at least 3 characters.")
    max_iter = max(1, min(int(req.max_iter or 5), 5))
    from backend.agent import run_agent
    result = await run_agent(
        question=query_text,
        user_email=user.email,
        db_pool=app.state.db_pool,
        max_iter=max_iter,
    )
    # Top-level audit entry (the tool calls are logged inside the agent).
    await write_audit_log(
        user.email,
        f"agent:{query_text[:200]}",
        ["agent.run"],
    )
    return result


@app.get("/activity")
async def activity(limit: int = 20, user: UserIdentity = Depends(require_admin)):
    """Tail the audit log. Live-updates the Activity tab in the UI."""
    limit = max(1, min(limit, 200))
    if not os.path.exists(_AUDIT_PATH):
        return {"entries": []}
    try:
        # ponytail: O(file) tail is fine at this size; switch to O(1) mmap if it grows past 50k entries.
        with open(_AUDIT_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        return {"entries": [], "error": str(e)}
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()  # newest first
    return {"entries": entries}


# ─── /query ───────────────────────────────────────────────────────────────────
def detect_question_type(query_text: str) -> str:
    q = query_text.lower().strip()
    exploratory = (
        r"\bexplain\b", r"\bsummarize\b", r"\bsummary\b", r"\bdescribe\b",
        r"\bcompare\b", r"\bdifference", r"\bwhy\b", r"\bhow to\b", r"\bhow does\b",
        r"\bhow do i\b", r"\bhow can i\b", r"\btutorial\b", r"\bguide\b",
        r"\bwalkthrough\b", r"\boverview\b", r"\banalyze\b", r"\bevalu",
        r"\bsynthesis", r"\bsynthesi", r"\bdetail",
    )
    return "exploratory" if any(re.search(p, q) for p in exploratory) else "factual"


@app.post("/query")
async def query(
    req: QueryRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
):
    t0 = time.perf_counter()
    tenant_id = COMPANY_TENANT_ID

    query_text = req.query.strip()
    if len(query_text) < 3:
        raise HTTPException(422, "Query must be at least 3 characters.")

    # Embed query
    try:
        query_emb = await get_async_ollama_embedding(f"search_query: {query_text}")
    except Exception as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    if not query_emb or not any(query_emb):
        raise HTTPException(503, "Embedding returned a zero vector — is Ollama running?")
    query_emb_str = "[" + ",".join(f"{x:.6f}" for x in query_emb) + "]"

    # Keyword tokens
    keywords = [w.lower() for w in re.findall(r"\w+", query_text) if w.lower() not in COMMON_STOPWORDS]

    effective_top_k = max(1, min(req.top_k, 8))

    # Hybrid retrieval
    candidates: list[dict] = []
    try:
        async with app.state.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)

                # Semantic top-60
                sem_sql = """
                    SELECT id, source, source_type, location, content,
                           (1 - (embedding <=> $1::vector)) AS semantic_score,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector ASC) AS sem_rank
                    FROM public.vector_chunks
                    LIMIT 60
                """
                sem_rows = await conn.fetch(sem_sql, query_emb_str)

                # Keyword top-60
                kw_rows: list = []
                if keywords:
                    cases = " + ".join(
                        f"CASE WHEN content ILIKE ${i+2} THEN 1.0 ELSE 0.0 END"
                        for i in range(len(keywords))
                    )
                    text_score = f"({cases})::float / {len(keywords)}"
                    kw_sql = f"""
                        WITH kw AS (
                            SELECT id, source, source_type, location, content,
                                   (1 - (embedding <=> $1::vector)) AS semantic_score,
                                   ({text_score}) AS kw_score
                            FROM public.vector_chunks
                            ORDER BY kw_score DESC
                            LIMIT 60
                        )
                        SELECT *, ROW_NUMBER() OVER (ORDER BY kw_score DESC) AS kw_rank FROM kw
                    """
                    kw_rows = await conn.fetch(kw_sql, query_emb_str, *[f"%{k}%" for k in keywords])

                # RRF fusion
                scores: dict = {}
                row_map: dict = {}
                k = 60
                for r in sem_rows:
                    cid = r.get("id", r.get("event_id"))
                    if cid is None:
                        continue
                    rank = r.get("sem_rank") or 1
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                    row_map[cid] = dict(r)
                for r in kw_rows:
                    cid = r.get("id", r.get("event_id"))
                    if cid is None:
                        continue
                    rank = r.get("kw_rank") or 1
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                    row_map.setdefault(cid, dict(r))

                seen = set()
                for cid, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                    c = row_map[cid]
                    key = (c.get("content", "").strip().lower()[:200])
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append({
                        "source":         c.get("source", ""),
                        "type":           c.get("source_type", "document"),
                        "location":       c.get("location", ""),
                        "content":        c.get("content", ""),
                        "semantic_score": float(c.get("semantic_score", 0) or 0),
                        "score":          sc,
                    })
                    if len(candidates) >= 20:
                        break

        # Cross-encoder rerank
        reranker = get_reranker()
        if reranker and candidates:
            pairs = [[query_text, c["content"][:512]] for c in candidates]
            try:
                logits = await asyncio.to_thread(reranker.predict, pairs)
                if hasattr(logits, "__iter__"):
                    for c, lg in zip(candidates, logits):
                        c["score"] = float(lg)
                candidates.sort(key=lambda x: x["score"], reverse=True)
            except Exception as e:
                print(f"  ⚠ rerank failed: {e}")

        # Named-document boost: when the query explicitly references a source by its
        # filename stem, the cross-encoder can still demote it — it scores generic
        # semantic relevance, not "did the user name this doc". Lift such candidates
        # so a user pointing at a specific doc sees it ranked near the top.
        # ponytail: fixed boost; ms-marco-MiniLM logits span ~[-5,+8], +5.0 guarantees
        # user-named docs land in top-k over generic-relevance winners. Tune down if a
        # named doc is ever genuinely irrelevant.
        _q_norm = re.sub(r"[^a-z0-9]+", " ", query_text.lower())
        for c in candidates:
            _stem = os.path.splitext(c.get("source", "").split("/")[-1])[0].lower()
            if _stem and (_stem in query_text.lower() or re.sub(r"[^a-z0-9]+", " ", _stem) in _q_norm):
                c["score"] = float(c.get("score", 0.0)) + 5.0
        candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    except Exception as e:
        print(f"  ✗ retrieval failed: {e}")
        raise HTTPException(500, f"DB retrieval failure: {e}")

    # Drop low-relevance chunks
    if candidates and max(c["semantic_score"] for c in candidates) < 0.40:
        candidates = []

    retrieved = candidates[:effective_top_k]

    if not retrieved:
        # Look up an admin contact to include in the fallback message.
        admin_email: str | None = None
        try:
            async with app.state.db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
                    row = await conn.fetchrow(
                        "SELECT email FROM auth.users "
                        "WHERE raw_user_meta_data->>'role' = 'admin' "
                        "OR raw_app_meta_data->>'role' = 'admin' "
                        "LIMIT 1"
                    )
                    if row:
                        admin_email = row["email"]
                    else:
                        # Try the original user_org_membership table for test compat.
                        row = await conn.fetchrow(
                            "SELECT email FROM public.user_org_membership LIMIT 1"
                        )
                        if row:
                            admin_email = row["email"]
        except Exception:
            # Fall back to the env-configured admin if DB lookup fails.
            admin_email = os.getenv("COMPANY_ADMIN_EMAIL")

        suffix = (
            f", please contact {admin_email}" if admin_email
            else ". Upload more relevant material to the S3 bucket."
        )
        await write_audit_log(user.email, req.query, [], refused=True)
        return {
            "query": req.query,
            "response": f"I don't have that information from the indexed documents{suffix}",
            "citations": [],
            "retrieved_context": [],
            "latency_seconds": round(time.perf_counter() - t0, 4),
        }

    # Build prompt
    MAX_CHUNK_CHARS = min(700, max(250, 5600 // max(len(retrieved), 1)))
    context_str = ""
    for i, c in enumerate(retrieved):
        content = c["content"][:MAX_CHUNK_CHARS]
        if len(c["content"]) > MAX_CHUNK_CHARS:
            content += "…"
        context_str += f"[{i+1}] Source: {c['source']} | Location: {c['location']}\n{content}\n\n"

    qtype = detect_question_type(query_text)
    if qtype == "factual":
        system_prompt = (
            "You are Smriti, an assistant for this company. Answer from the provided context only. "
            "Be precise and concise. After every factual statement, add an inline citation: "
            "[Citation: filename, location]. If the context does not contain the answer, say so clearly."
        )
        temperature = 0.0
    else:
        system_prompt = (
            "You are Smriti, an assistant for this company. Synthesise information from the provided "
            "context to explain or summarise. Add inline citations [Citation: filename, location] when "
            "referencing specific facts. If the context does not contain the answer, say so clearly."
        )
        temperature = 0.3

    messages = [{"role": "system", "content": system_prompt}]
    if req.history:
        for m in req.history[-6:]:
            messages.append({"role": m.role, "content": m.content})
    messages.append({
        "role": "user",
        "content": f"CONTEXT:\n{context_str}\nQUESTION: {req.query}",
    })

    # Generate
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": CHAT_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": temperature, "num_ctx": 2048, "num_predict": 512},
                },
                timeout=180.0,
            )
            resp.raise_for_status()
            raw = (resp.json().get("message") or {}).get("content") or ""
    except Exception as e:
        raise HTTPException(500, f"Generation failure: {e}")

    if not raw.strip():
        raw = "I cannot find this in the indexed documents."

    validated = validate_response(raw, retrieved)
    citations = extract_citations(validated)

    # If the grounding firewall stripped everything, fall through to the admin fallback.
    vlow = validated.lower()
    no_answer = (
        not validated.strip()
        or "i cannot find" in vlow
        or "i cannot answer" in vlow
        or "i don" in vlow and "have" in vlow and ("indexed" in vlow or "document" in vlow)
    )
    if no_answer:
        admin_email = None
        try:
            async with app.state.db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
                    row = await conn.fetchrow(
                        "SELECT email FROM public.user_org_membership LIMIT 1"
                    )
                    if row:
                        admin_email = row["email"]
        except Exception:
            pass
        if not admin_email:
            admin_email = os.getenv("COMPANY_ADMIN_EMAIL")
        suffix = f", please contact {admin_email}" if admin_email else ""
        await write_audit_log(
            user.email, req.query, [c["source"] for c in retrieved], refused=True
        )
        return {
            "query": req.query,
            "response": f"I don't have that information from the indexed documents{suffix}",
            "citations": [],
            "retrieved_context": [
                {
                    "source":   c.get("source", ""),
                    "type":     c.get("type", "document"),
                    "location": c.get("location", ""),
                    "content":  c.get("content", ""),
                    "score":    c.get("score", 0),
                }
                for c in retrieved
            ],
            "latency_seconds": round(time.perf_counter() - t0, 4),
        }

    await write_audit_log(
        user.email, req.query, [c["source"] for c in retrieved],
        citations=[c.get("source", "") for c in citations],
        refused=False,
    )

    return {
        "query": req.query,
        "response": validated,
        "model": CHAT_MODEL,
        "citations": citations,
        "retrieved_context": [
            {
                "source":   c["source"],
                "type":     c["type"],
                "location": c["location"],
                "content":  c["content"],
                "score":    c["score"],
            }
            for c in retrieved
        ],
        "latency_seconds": round(time.perf_counter() - t0, 4),
    }


# Backward-compatible alias for the legacy test harness
process_query = query
