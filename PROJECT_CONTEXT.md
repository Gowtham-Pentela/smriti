# Smriti — Project Context

> A complete briefing for any LLM or engineer who has never seen this codebase. After reading this you should understand what Smriti is, why it exists, how every piece fits together, the runtime contract, the data model, and the conventions you must follow when modifying it.

---

## 1. What Smriti Is (One Paragraph)

**Smriti** is a single-tenant, on-premise AI knowledge assistant. Users drop documents (PDF, DOCX, code, images, audio, video) into an S3 bucket; Smriti parses, chunks, embeds, and indexes them automatically. Any team member can then ask natural-language questions through a chat UI and get back grounded, citation-backed answers in seconds. **No data leaves the customer's infrastructure** — embeddings (Ollama `nomic-embed-text`), generation (Ollama `phi4-mini`), reranking (ONNX cross-encoder), image description (llava), and speech-to-text (local Whisper) all run on the customer's own hardware. The product's name is Sanskrit for *memory* — the product is a queryable institutional memory for the company.

**Author / owner:** Gowtham Pentela. **License:** MIT. **Deployment target:** AWS (S3 + SQS + EventBridge + EC2/ECS + RDS Postgres+pgvector).

---

## 2. Why It Exists

Companies accumulate knowledge in PDFs, recordings, and slide decks. Search is brittle ("Ctrl-F the Slack channel where someone mentioned it"), and senior engineers leave. Smriti gives a company a single chat surface that answers questions grounded in their own documents, with citations to the source — running entirely on their own AWS, with no third-party AI calls.

---

## 3. Tech Stack (Authoritative)

| Layer | Tech | Why |
|---|---|---|
| API | **FastAPI** (Python 3.12, async) | Async, OpenAPI docs, lightweight |
| Database | **Postgres + pgvector** | HNSW vector index, full-text search in the same engine |
| Embeddings | **nomic-embed-text** (Ollama, local) | 768-dim, MTEB 62.4, fully offline |
| Generation | **phi4-mini** Q4_K_M (Ollama, local) | 3.8B params, ~3.2 GB RAM, fits in 8 GB systems |
| Vision (images) | **llava:7b** or **moondream** (Ollama) | Image description for standalone images and embedded PDF figures |
| Audio transcription | **openai-whisper** (local, `tiny`/`base`/...) | MP3/WAV/M4A/etc., and video audio (via ffmpeg) |
| Video extraction | **ffmpeg** | Extract mono 16 kHz audio from any video |
| Reranker | **cross-encoder/ms-marco-MiniLM-L6-v2** via sentence-transformers (CPU) | Final top-K after hybrid retrieval |
| Auth | Dev mode (X-Dev-User-Email, loopback) **or** Supabase JWT in prod | Skip auth in local dev; require a JWT in production |
| S3 ingestion | **boto3** + SQS long polling | EventBridge → SQS → worker |
| Frontend | **Vanilla HTML / CSS / JS**, no build step | No JS toolchain, no bundler, single static mount |

### Why phi4-mini Q4_K_M
The system is sized for a developer's laptop (8 GB RAM). Quantized 4-bit retains 95–98% of full-precision quality. The grounding firewall (below) compensates for the small model's variance — the model's only job is to paraphrase retrieved chunks, not to recall facts from its weights.

---

## 4. End-to-End Architecture

```
                     ┌────────────────────────────┐
                     │  S3 bucket (ObjectCreated) │
                     │  PDF / DOCX / image /      │
                     │  audio / video / code      │
                     └─────────────┬──────────────┘
                                   │ EventBridge → SQS (raw delivery)
                                   ▼
                ┌─────────────────────────────────────┐
                │  s3_connector.py worker             │
                │  (long-poll SQS)                    │
                │                                     │
                │  Route by extension:                │
                │   .pdf/.docx/.txt/.md/.csv/.json/   │
                │     .yaml/.sql/code                 │
                │     → parser.parse_document()      │
                │   .mp4/.mov/.mkv/.webm             │
                │     → ffmpeg → Whisper              │
                │   .wav/.mp3/.m4a/.flac/.ogg         │
                │     → Whisper                       │
                │   .png/.jpg/.jpeg/.webp/.gif        │
                │     → llava / moondream             │
                └─────────────┬───────────────────────┘
                              │ chunks
                              ▼
                ┌─────────────────────────────────────┐
                │  Ollama nomic-embed-text            │
                │  768-dim vectors (per chunk)        │
                └─────────────┬───────────────────────┘
                              │
                              ▼
                ┌─────────────────────────────────────┐
                │  Postgres + pgvector                │
                │  - public.tenant_registry (1 row)   │
                │  - public.vector_chunks (HNSW)      │
                │  - public.ingestion_hashes (dedup)  │
                │  RLS via app.current_tenant_id      │
                └─────────────┬───────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   /status, /files,      POST /query          S3 worker
   /me, /clear,          (the main loop)
   /ingest (UI upload)
                              │
                              ▼
                ┌──────────────────────────┐
                │ 1. Embed query (Ollama)  │
                │ 2. Hybrid retrieval:     │
                │    - pgvector cosine     │
                │    - Postgres FTS (ILIKE)│
                │ 3. RRF fusion (k=60)    │
                │ 4. ONNX cross-encoder   │
                │    rerank → top 8       │
                │ 5. Ollama chat generate │
                │    (phi4-mini)          │
                │ 6. Grounding firewall   │
                │    validate_response()  │
                └────────┬─────────────────┘
                         ▼
                Cited answer + numbered source links
                         │
                         ▼
                data/audit_log.json (NDJSON, append-only)
```

---

## 5. Repository Layout

```
smriti/
├── backend/                       # All Python server code
│   ├── __init__.py
│   ├── main.py                    # FastAPI app: /query, /ingest, /status, /me, /files, /clear, /s3/*
│   ├── grounding.py               # Citation verification + hallucination firewall (UNCHANGED)
│   ├── parser.py                  # PDF / DOCX / TXT / MD / CSV / image / code parsing (UNCHANGED)
│   ├── transcription.py           # Whisper + ffmpeg audio extraction (UNCHANGED)
│   ├── vision_processor.py        # PDF image / diagram description (UNCHANGED)
│   ├── image_extractor.py         # Extract images from PDFs (UNCHANGED)
│   ├── vector_store.py            # Legacy LocalVectorStore; not used by /query but kept
│   ├── auth.py                    # Dev-mode + Supabase JWT; single company tenant
│   ├── crypto.py                  # Fernet encryption helpers (kept for future use)
│   ├── db.py                      # sha256 + check_and_mark_ingested (dedup)
│   ├── tenant.py                  # Single-tenant helper
│   └── s3_connector.py            # SQS long-poll worker + local-folder mode
│
├── frontend/                      # Vanilla HTML/CSS/JS, no build step
│   ├── index.html                 # Chat + side panel
│   ├── app.js                     # Chat, upload, S3 status
│   ├── style.css                  # Dark/light theme
│   └── images/                    # Logos
│
├── supabase/migrations/           # SQL schema (run in order on a fresh DB)
│   ├── 001_tenant.sql             # tenant_registry + pgvector extension
│   ├── 002_vector_chunks.sql      # vector_chunks table + HNSW + RLS
│   └── 003_ingestion_hashes.sql   # dedup table + RLS
│
├── deploy/aws/                    # Terraform: S3 + SQS + EventBridge + IAM
│   ├── main.tf
│   └── README.md
│
├── tests/
│   └── test_answer_quality.py     # Grounding firewall regression
│
├── data/                          # audit_log.json (NDJSON, append-only)
├── requirements.txt               # All Python deps pinned
├── .env.example                   # All env vars documented
├── start_local.sh                 # Local startup helper
├── deploy.sh                      # Build + push to ECR, optional ECS rollout
├── Dockerfile.backend             # Multi-stage; ffmpeg included
├── Dockerfile.ui                  # static site
├── README.md
└── PROJECT_CONTEXT.md             # ← you are here
```

---

## 6. The /query Pipeline (the load-bearing core)

`POST /query` with body `{query, top_k, history}`:

1. **Auth** — `Depends(get_current_user)` returns `UserIdentity`. Sets `request.state.tenant_id = COMPANY_TENANT_ID`.
2. **Sanity** — Reject queries shorter than 3 characters.
3. **Embed query** — `nomic-embed-text` via Ollama with `search_query: ` prefix. Reject zero-vectors (they crash pgvector cosine).
4. **Keyword extraction** — `re.findall(r"\w+", query_text)`, stopwords filtered.
5. **Hybrid retrieval** inside a transaction with `app.current_tenant_id` set via `set_config(...)`:
   - **Semantic top-60** — `ORDER BY embedding <=> $1::vector ASC`
   - **Keyword top-60** — `ILIKE` case per keyword, score = matched / total
   - **RRF fusion** — `1/(k + rank)` for each list, k=60, dedup by content
6. **Cross-encoder rerank** — `cross-encoder/ms-marco-MiniLM-L6-v2`, score each (query, chunk) pair, sort by score, take top 8.
7. **Similarity guard** — if max semantic score < 0.40 → return "I don't have that information…" with no chunks fed to the LLM.
8. **Build prompt** — context per chunk is truncated to `min(700, 5600/N)` chars. System prompt tuned for "factual" (temp 0.0) vs "exploratory" (temp 0.3) queries.
9. **Generate** — `phi4-mini` via Ollama `/api/chat` with `num_ctx=2048`, `num_predict=512`.
10. **Grounding firewall** (`backend/grounding.py:validate_response`):
    - Split into sentences
    - For each sentence: extract inline citation `[Citation: source, location]`, verify against retrieved chunks, compute stopword-excluded word overlap (≥ 60% required), strip if unsupported
    - Strip meta-commentary ("Based on the context…"), placeholder text (`[Job Title]`), and empty sentences
    - If all sentences stripped → fallback message
11. **Audit log** — append `{ts, user_email, query, accessed_files}` to `data/audit_log.json` (NDJSON).
12. **Return** — `{query, response, model, citations[], retrieved_context[], latency_seconds}`.

A second-LLM verification pass was considered and rejected: it added 20–90s latency and blocked the asyncio loop. The single-pass 60% word overlap is the chosen approach.

---

## 7. The S3 Ingestion Pipeline (`backend/s3_connector.py`)

Long-lived asyncio task. Started from `main.py`'s lifespan.

```
EventBridge rule: ObjectCreated on bucket → SQS (raw delivery)
                     ↓
s3_connector.start_worker():
  while True:
    sqs.receive_message(WaitTimeSeconds=20)   # long poll
    for msg in messages:
      info = parse_s3_event(msg.Body)
      if not info: delete; continue
      try:
        obj = s3.get_object(bucket, key)
        download to /tmp/<key>
        hash = sha256(file)
        chunks = _route_and_chunk(tmp_path, key)   # by extension
        if not chunks: record "empty"; delete; continue
        n = _ingest_chunks(db_pool, tenant_id, chunks, source, hash)
        # ↑ calls set_config('app.current_tenant_id', ...) on the conn,
        #   embeds via Ollama, INSERTs into vector_chunks
        record "ok"; delete from SQS
      except Exception:
        # Leave in flight; SQS visibility timeout re-delivers.
        # After 5 receives → dead-letter queue.
        record "failed"
```

### Routing table (`_route_and_chunk`)

| Extension(s) | Handler |
|---|---|
| `.pdf .docx .txt .md .csv .json .yaml .yml .sql` | `parser.parse_document()` |
| `.py .js .ts .tsx .jsx .java .go .cpp .c .h .rs .sh` | `parser.parse_document()` (function-block chunking) |
| `.png .jpg .jpeg .webp .gif` | `parser.parse_document()` (delegates to vision_processor) |
| `.mp4 .mov .mkv .webm .avi` | `transcription.transcribe_video()` (ffmpeg → Whisper) |
| `.wav .mp3 .m4a .flac .ogg` | `_transcribe_audio()` (Whisper direct) |

### Idempotency
`check_and_mark_ingested` is an UPSERT on `(tenant_id, file_hash)`. SQS re-deliveries (after a failed processing) are no-ops because the hash is already in the table.

### Local-folder mode (for testing without AWS)
```bash
python -m backend.s3_connector --local-folder ./test_uploads
```
Walks a directory, routes by extension, ingests everything. Returns a summary `{ok, skipped, failed, files[]}`.

---

## 8. Multi-Tenancy

There is exactly one tenant — the company. The UUID is set in `COMPANY_TENANT_ID` (default `00000000-0000-0000-0000-000000000001`). Every DB connection inside a request handler opens a transaction and runs:

```sql
SELECT set_config('app.current_tenant_id', '<COMPANY_TENANT_ID>', true);
```

This activates the RLS policy on `vector_chunks` and `ingestion_hashes`. `tenant_registry` is the one row for the company.

If you ever need multi-tenancy again, the migration from this single-tenant setup is non-breaking — add `tenants` rows, make the request's `tenant_id` depend on the authenticated user, and the RLS policy already does the rest.

---

## 9. Database Schema (high level)

```
public.tenant_registry         → tenant_id (PK), name
public.vector_chunks           → id (BIGSERIAL), tenant_id, source, source_type, location,
                                content, embedding(768), file_hash, created_at
                                HNSW index on embedding (vector_cosine_ops)
                                GIN index on to_tsvector(content)
                                RLS: tenant_id = current_setting('app.current_tenant_id')
public.ingestion_hashes        → (tenant_id, file_hash) PK, source, chunks, ingested_at
                                RLS: tenant_id = current_setting('app.current_tenant_id')
```

All three tables are created by `supabase/migrations/00{1,2,3}_*.sql`, applied in order.

> **Implementation note:** `app.current_tenant_id` is set via `SELECT set_config('app.current_tenant_id', $1, true)` rather than `SET LOCAL`, because asyncpg's prepared-statement protocol does not accept parameter placeholders in `SET` statements. Both forms scope the setting to the current transaction.

---

## 10. API Surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/` | none | Redirect to `/app/index.html` |
| GET  | `/health` | none | Liveness probe |
| GET  | `/status` | required | Chunk count + S3 worker status |
| GET  | `/me` | required | Current user identity |
| GET  | `/files` | required | List of indexed source URIs |
| POST | `/ingest` | required | File upload fallback (multipart) |
| POST | `/clear` | required | Wipe the company index |
| POST | `/query` | required | **Main endpoint** — ask a question |
| GET  | `/s3/status` | required | S3 worker status + recent ingestions |
| POST | `/s3/resync?folder=…` | required | Re-ingest a local folder (testing) |
| GET  | `/app/…` | none | Static frontend mount |

The OpenAPI schema is at `/docs` when the server is running.

---

## 11. Environment Variables (see `.env.example`)

| Var | Default | Notes |
|---|---|---|
| `COMPANY_TENANT_ID` | `00000000-…-0001` | Single company tenant UUID. Validated at startup. |
| `DATABASE_URL` | `postgresql://…54322/postgres` | Postgres + pgvector. |
| `SMRITI_EMBED_MODEL` | `nomic-embed-text` | Ollama model for embeddings. |
| `SMRITI_CHAT_MODEL` | `phi4-mini:latest` | Ollama model for generation. |
| `SMRITI_WHISPER_MODEL` | `tiny` | tiny / base / small / medium / large. |
| `SMRITI_DEV_MODE` | `true` | Skip JWT validation. **Local dev only.** |
| `SMRITI_ENV` | `local` | `local` / `dev` / `production`. |
| `SMRITI_DEV_USER_EMAIL` | `you@yourcompany.com` | Impersonated user in dev mode. |
| `SUPABASE_URL` | empty | Required in production. |
| `SUPABASE_ANON_KEY` | empty | Required in production. |
| `S3_BUCKET` | empty | Source bucket for the S3 worker. |
| `S3_QUEUE_URL` | empty | SQS queue (fed by EventBridge). |
| `AWS_REGION` | `us-east-1` | For boto3. |
| `CORS_ORIGINS` | `*` | Comma-separated. `*` is refused outside `local`/`dev`. |

### Safety guards baked into startup
- Refuses to start with `SMRITI_DEV_MODE=true` outside `local`/`dev`.
- Refuses to start with `CORS_ORIGINS=*` outside `local`/`dev`.
- Refuses to start in production without `SUPABASE_ANON_KEY`.
- Validates `COMPANY_TENANT_ID` is a real UUID.

---

## 12. Local Development

```bash
# 1. Setup
git clone https://github.com/Gowtham-Pentela/smriti
cd smriti
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Pull models
ollama pull nomic-embed-text
ollama pull phi4-mini
# optional:
ollama pull llava:7b    # for image description

# 3. Configure
cp .env.example .env
# (defaults work for local dev — SMRITI_DEV_MODE=true, SMRITI_ENV=local)

# 4. Postgres + pgvector
docker run -d --name smriti-db -p 54322:5432 \
    -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
# or: supabase start

# 5. Run
bash start_local.sh
# → applies migrations, starts uvicorn, opens http://127.0.0.1:8000/app/

# 6. Test the S3 worker without AWS
mkdir -p ./test_uploads
cp ~/some.pdf ./test_uploads/
python -m backend.s3_connector --local-folder ./test_uploads
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":"What does the test file say?"}' \
  http://127.0.0.1:8000/query | python3 -m json.tool
```

---

## 13. Production / AWS

- **Container:** `Dockerfile.backend` (multi-stage, includes ffmpeg, single uvicorn worker on CPU).
- **HTTPS / public access:** put an ALB or CloudFront in front of the ECS service. For an internal-VPC deployment, no public ingress.
- **Database:** RDS Postgres 16 with the pgvector extension. Sg must allow the backend's SG.
- **Ollama:** runs in the same task or a sidecar. On CPU, phi4-mini is single-threaded — for higher QPS, use a GPU instance with vLLM.
- **S3 ingestion:** `deploy/aws/main.tf` provisions the bucket, SQS queue with DLQ, EventBridge rule, and the least-privilege IAM role.

### Hardening checklist
- [ ] Restrict the S3 bucket policy to the VPC CIDR or VPC endpoint.
- [ ] Enable CloudTrail data events on the bucket.
- [ ] Add CloudWatch alarms on SQS `ApproximateNumberOfMessagesVisible > 100`.
- [ ] Set `CORS_ORIGINS` to explicit origins.
- [ ] Set `SMRITI_DEV_MODE=false` and provide real Supabase credentials.
- [ ] S3 lifecycle policy to move old objects to Glacier (optional).
- [ ] Periodic prune of `data/audit_log.json` (or move to CloudWatch Logs).

---

## 14. Frontend

- **No framework** — vanilla HTML/CSS/JS, served by FastAPI's `StaticFiles` mount at `/app/`.
- **Theme:** `data-theme` attribute on `<html>`, persisted to `localStorage`.
- **Layout:** 2-column — chat on the left, side panel (upload, file list, S3 status) on the right. Collapses to single-column under 900px.
- **Auth:** `authFetch` wraps all API calls. The backend decides whether dev mode is on; the frontend doesn't pre-check.
- **Citations:** numbered badges; clicking expands the source chunk.
- **Single page:** `index.html` + `app.js`. No build step.

---

## 15. Testing

### Backend (pytest)
```bash
pytest tests/test_answer_quality.py     # grounding firewall regression
```

### E2E (Playwright)
The Playwright harness from the legacy multi-tenant build was removed in the pivot. The minimal 2-column UI is small enough to exercise with the live curl-based smoke tests in §12 above. Add a Playwright spec back when the UI grows.

---

## 16. Known Limitations (and how to lift them)

| Limit | Today | When you need to fix it |
|---|---|---|
| phi4-mini variance on ambiguous queries | grounding firewall catches it | swap to a 7B/8B model on GPU: `SMRITI_CHAT_MODEL=llama3:8b` |
| Single-threaded generation | CPU phi4-mini is sequential | GPU instance with vLLM or Ollama server mode |
| First-run indexing latency | ~0.3s/chunk on CPU; 50k messages ≈ 15–30 min | batch embed with 5 async workers |
| S3 event ordering is best-effort | dedup table handles re-deliveries | n/a (already safe) |
| SQS DLQ requires manual inspection | `aws sqs receive-message --queue-url <dlq>` | add a CloudWatch alarm |
| No rate limiting on `/query` | anybody with a JWT can spam | add per-tenant middleware |
| Local logging only | audit log is a local NDJSON file | pipe to CloudWatch Logs |

---

## 17. Conventions & Style

When working in this codebase:

1. **Ponytail mode is on by default** — write the smallest diff that works. Reach for native APIs before adding new dependencies. Mark shortcuts with `# ponytail: <upgrade path>`.
2. **Pydantic for request/response models** at every route boundary.
3. **`Depends(get_current_user)`** on every authenticated route. Dev mode only when `SMRITI_ENV` is `local`/`dev`.
4. **`SELECT set_config('app.current_tenant_id', $1, true)`** inside every DB transaction that touches `vector_chunks` or `ingestion_hashes`. (Equivalent to `SET LOCAL`, but works with asyncpg's prepared-statement protocol.)
5. **Tenant ID comes from `auth.COMPANY_TENANT_ID`**, not from request parameters. The whole product is single-tenant; if a code path is computing a different tenant_id, something is wrong.
6. **Idempotent ingestion.** Every ingestion path must go through `db.check_and_mark_ingested` before embedding. SQS re-deliveries and double-uploads must be no-ops.
7. **The grounding firewall is sacred.** Any change to the prompt format or generation model must be matched with a review of `validate_response` in `grounding.py`. The 60% word-overlap threshold is load-bearing.
8. **The S3 worker is the only ingestion path that matters.** `/ingest` is a UI fallback. When adding a new file type, extend the routing in `_route_and_chunk`.
9. **Audit log is append-only NDJSON** — `data/audit_log.json`. New audit events go through `write_audit_log`, never direct file writes.
10. **Frontend = no build step.** Keep it that way. New JS goes in `frontend/app.js` (or a new file loaded via `<script src>` in `index.html`).
11. **Tests** — every new endpoint gets a Playwright spec under `tests/e2e/` (if it has UI surface) and/or an integration test under `tests/`. Every new backend module gets at least one test.
12. **Migrations are append-only** — new SQL goes in `supabase/migrations/NNN_*.sql`. Never edit a past migration.
13. **No external AI API calls.** This is the entire point. If you find yourself reaching for `openai.Anthropic()` / `google.generativeai`, stop.

---

## 18. What "Done" Looks Like for a Change

Before considering any non-trivial change complete:

- [ ] Code change is minimal and matches existing style (see §17).
- [ ] Pydantic models at route boundaries; `Depends(get_current_user)` on authed routes; `set_config('app.current_tenant_id', …)` inside DB transactions.
- [ ] New env var documented in `.env.example`.
- [ ] New migration in `supabase/migrations/NNN_*.sql` (append-only).
- [ ] Playwright spec under `tests/e2e/` for any UI change.
- [ ] Integration test under `tests/` for any backend logic change.
- [ ] No new third-party dependency unless absolutely necessary.
- [ ] `curl -s http://localhost:8000/status` returns ok after the change.
- [ ] If the grounding firewall is touched: re-run `tests/test_answer_quality.py`.
- [ ] No `print()` for production telemetry — use the logger or audit log.

---

## 19. Quick-Reference Cheatsheet

| Want to... | Look in |
|---|---|
| Add a new file type to ingestion | `backend/s3_connector.py` `_route_and_chunk` + extension sets + `backend/parser.py` |
| Add a new API route | `backend/main.py` near the existing route cluster; add `Depends(get_current_user)` |
| Tweak retrieval ranking | `backend/main.py` `/query` — RRF + rerank |
| Change the answer prompt | `backend/main.py` `detect_question_type` + the system_prompt blocks in `/query` |
| Tweak the grounding threshold | `backend/grounding.py` `verify_substring_or_words` (60% overlap) |
| Add a DB table | New `supabase/migrations/00N_*.sql`; add RLS policy using `current_setting('app.current_tenant_id', true)::uuid` |
| Add a UI panel | `frontend/index.html` + `frontend/style.css` + `frontend/app.js` (no build step) |
| Add a frontend dep | **Don't.** The whole point is zero JS build pipeline |
| Run the test suite | `pytest tests/` and `npx playwright test` |
| Ship to prod | `cd deploy/aws && terraform apply`; then `./deploy.sh` |
| Find the live status | `curl https://<internal-dns>/status` |
| Read the user-facing pitch | `README.md` and `frontend/index.html` |
| Read the technical deep-dive | This file |

---

*Last updated: 2026-07-24. If you change architecture, update this file in the same commit.*
