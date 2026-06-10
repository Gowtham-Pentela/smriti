# Smriti

**Your organization's institutional knowledge, permanently queryable.**

Smriti is a private, on-premise AI knowledge assistant that indexes your Slack workspace, Google Drive, Confluence pages, and PDF documents. Every engineer on your team can ask natural language questions and get cited, grounded answers in seconds — with no data leaving your infrastructure.

Live demo: [smriti.one](https://smriti.one)

---

## The Problem

Every growing engineering team hits the same invisible wall:

**Knowledge Silos.** The answer exists somewhere — buried in a Slack thread from eight months ago, a Confluence page that was never updated, and the head of an engineer who left last quarter. Nobody can find it.

**Onboarding Friction.** A new engineer spends their first three weeks asking questions that were already answered somewhere. Every senior teammate loses hours explaining context that already exists in writing.

**Brain Drain.** When a senior engineer leaves, they take years of architectural decisions, deployment gotchas, and process knowledge with them. No documentation process fixes this retroactively.

Smriti solves all three by indexing what already exists and making it instantly queryable — privately, on your own hardware.

---

## How It Works

```
Slack / Google Drive / Confluence / PDFs
          |
          v
  [Ingestion Pipeline]
  Chunking + Embedding (nomic-embed-text, local)
          |
          v
  [Supabase + pgvector]
  768-dim vectors stored per-tenant
          |
          v
  User asks a question
          |
          v
  [Hybrid Retrieval]
  Cosine similarity + keyword matching
          |
          v
  [phi4-mini · Q4_K_M quantization]
  Synthesizes answer from retrieved chunks
          |
          v
  [Grounding Firewall]
  Every sentence verified against source chunks
  Hallucinated sentences stripped before response
          |
          v
  Cited answer with numbered source links
```

---

## Architecture

### Stack

| Layer | Technology | Notes |
|---|---|---|
| API | FastAPI (Python) | Async, clean OpenAPI docs |
| Database | Supabase + pgvector | Managed Postgres with native vector search |
| Embeddings | nomic-embed-text | MTEB 62.4, beats OpenAI ada-002, fully offline |
| Generation | **phi4-mini · Q4_K_M** | 3.8B params, ~3.2GB RAM, 95–98% quality vs FP16 |
| Encryption | Fernet (AES-256) | OAuth token storage at rest |
| Tunnel | Cloudflare Tunnel | Zero-trust HTTPS, no exposed ports |
| Frontend | Vanilla HTML / CSS / JS | No framework dependency, split-workspace UI |
| Auth | Supabase + Google OAuth | JWT tokens, PKCE flow |

### Why phi4-mini (Q4_K_M)

Smriti runs on hardware engineering teams actually have. The constraint is RAM.

| Model | RAM Usage | Outcome on 8 GB system |
|---|---|---|
| mistral:7b | ~4.1 GB | OOM crash alongside Docker + FastAPI |
| qwen2.5-coder:3b | ~1.9 GB | OOM under concurrent load |
| tinyllama:1.1b | ~638 MB | Too weak for multi-step reasoning |
| **phi4-mini Q4_K_M** | **~3.2 GB** | ✅ Selected: fits with headroom, strong reasoning |

Q4_K_M quantization retains 95–98% of full-precision quality while reducing memory by ~60%. The model is confirmed via `ollama show phi4-mini:latest` — Ollama ships this model in Q4_K_M by default.

phi4-mini's reasoning capability is compensated by the grounding firewall: the model's only job is to synthesize and paraphrase retrieved chunks. Factual accuracy is enforced by the grounding layer, not the model weights.

### Multi-Tenant & Org-Level Isolation

To isolate data across organizations and users, Smriti partitions all indexed files and vector chunks using a tenant UUID:
1. **Shared Partitioned Table**: All vector chunks are stored in `tenant_redwood_inference_prod.vector_chunks`, secured with `tenant_id` partitioning.
2. **Access Control**: Queries enforce membership permissions by matching the user's email or domain against the workspace's registry (`public.user_org_membership`).
3. **Session Context**: Database operations utilize `SET LOCAL app.current_tenant_id` inside scoped transactions to prevent cross-tenant data leakage.

### Retrieval Pipeline

Hybrid search: semantic cosine similarity from pgvector + PostgreSQL full-text search. Exact keyword matches (function names, ticket IDs) surface even when semantic similarity is low.

```python
SELECT
    source_id, channel_or_space, content,
    1 - (embedding <=> $query_embedding) AS cosine_score,
    ts_rank(to_tsvector('english', content), plainto_tsquery($query)) AS keyword_score
FROM vector_chunks
ORDER BY (cosine_score * 0.7 + keyword_score * 0.3) DESC
LIMIT 4;
```

### Grounding Firewall

`backend/grounding.py` is the hallucination prevention layer between LLM output and the API response.

For every generated sentence:
1. Citations are extracted (`[Citation: source, location]` format)
2. Cited source is cross-referenced against retrieved chunks
3. Word overlap (stopwords excluded) is computed between sentence and source
4. If overlap ≥ 60% → sentence passes, returned with verified citation
5. If overlap < 60% → sentence is stripped
6. If all sentences are stripped → fallback: "I cannot find the answer in the provided documents"

A second LLM verification call was considered and rejected: it added 20–90 seconds of latency and blocked the asyncio event loop.

### Answer Quality & Fallback Handling

Smriti implements a multi-stage answer quality filter to prevent hallucination and ensure helpful answers:
1. **Question Type Detection**: The pipeline analyzes the user query to distinguish between **Factual** (direct questions) and **Exploratory** (summaries, tutorials, comparisons) queries.
2. **Dynamic Prompt & Parameter Tuning**:
   - *Factual*: Uses a highly precise system prompt and sets `temperature = 0.0` to enforce strict accuracy.
   - *Exploratory*: Encourages synthesis and structuring, setting `temperature = 0.3` for detailed explanations.
3. **Similarity Score Guard**: Queries with a top vector similarity score below `0.51` are rejected immediately to screen out completely unrelated topics.
4. **Admin Fallback Admission**: If the database contains no relevant documents, or if the grounding verification fails, the response is overridden with: `"I don't have that information from the indexed documents, please contact <admin_email>"`, dynamically resolving the email of the active workspace admin.

---

## Benchmark Results

Retrieval pipeline evaluated against [EnterpriseRAG-Bench](https://github.com/microsoft/EnterpriseRAG-Bench) (500 enterprise knowledge questions).

### Retrieval Performance

| Metric | Value |
|---|---|
| Questions evaluated (Slack-filtered) | 79 |
| **Slack Question Hit Rate** | **92.4% (73/79)** |
| **Slack Mean Recall @ 10** | **74.84%** |
| **p50 retrieval latency** | **115.9 ms** |
| **p95 retrieval latency** | **144.0 ms** |

Two-phase HNSW architecture (top-300 candidates → keyword re-rank) reduced retrieval latency from 1,430 ms to 116 ms — a **12.3× improvement** at equivalent recall.

---

## Connectors

### Google Drive OAuth

Smriti reads Google Drive files using two OAuth scopes:

| Scope | Use |
|---|---|
| `drive.readonly` | List, download, and export files (PDFs, Docs, Sheets, Slides) |
| `drive.metadata.readonly` | Detect new/modified files during incremental sync |

**OAuth flow:**
1. User clicks "Google Drive" pill in the UI
2. Backend redirects to Google's consent screen (PKCE + HMAC-signed CSRF state, 10-min TTL)
3. User authorizes read-only access
4. Google redirects to `/gdrive/oauth/callback`
5. Backend verifies state, exchanges code for token
6. Access + refresh token encrypted with AES-256 (Fernet) before storage
7. UI shows green connected dot; auto-sync runs every 30 minutes

File content is chunked, embedded locally with nomic-embed-text, and stored in pgvector. Raw bytes are not persisted. Drive access can be revoked from the Smriti UI or from [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

> **Verification status:** `drive.readonly` is a restricted scope. During the verification review period, add pilot users as [Test Users](https://console.cloud.google.com/apis/credentials/consent) in Google Cloud Console (up to 100). See `google_drive_verification_guide.md` for the full submission steps.

### Slack OAuth

Required bot token scopes: `channels:history`, `channels:read`, `users:read`, `channels:join`

**OAuth flow:** HMAC-signed CSRF state → Slack authorization → bot token Fernet-encrypted at rest → 30-minute auto-sync

---

## Privacy

Smriti is built for organizations that cannot send internal data to third-party AI APIs.

- **No OpenAI / Anthropic / Gemini API calls.** Inference runs on your machine via Ollama.
- **No cloud embedding services.** nomic-embed-text runs locally.
- **No data egress.** Messages, documents, embeddings never leave your infrastructure.
- **Encrypted credentials.** All OAuth tokens are AES-256 encrypted at rest.
- **Tenant isolation.** Each user's data is schema-isolated with a unique UUID namespace.
- **HTTPS everywhere.** Cloudflare Tunnel provides TLS without exposing ports.

Full policy: [smriti.one/app/privacy.html](https://smriti.one/app/privacy.html)

---

## Prerequisites

- macOS or Linux
- Python 3.11+
- [Ollama](https://ollama.ai) installed
- [Supabase CLI](https://supabase.com/docs/guides/cli) (for local development)

---

## Local Setup

### 1. Clone and install

```bash
git clone https://github.com/Gowtham-Pentela/smriti.git
cd smriti
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull Ollama models

```bash
ollama pull nomic-embed-text   # 274 MB — embeddings
ollama pull phi4-mini          # ~2.5 GB — generation (Q4_K_M by default)
```

### 3. Configure environment

```bash
cp .env.example .env
```

Generate secrets:

```bash
# Fernet encryption key (for OAuth token storage)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# CSRF state signing secret
python -c "import secrets; print(secrets.token_hex(32))"
```

For Slack OAuth: create an app at [api.slack.com/apps](https://api.slack.com/apps) with scopes `channels:history`, `channels:read`, `users:read`, `channels:join`. Set redirect URL to `http://localhost:8000/slack/oauth/callback`.

For Google Drive OAuth: create an OAuth 2.0 Client ID at [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials). Set redirect URI to `http://localhost:8000/gdrive/oauth/callback`.

### 4. Start Supabase

```bash
supabase start
```

### 5. Start the backend

```bash
source venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --log-level info
```

### 6. Verify

```bash
curl -s http://localhost:8000/status | python3 -m json.tool
```

Expected:
```json
{ "status": "ok", "indexed_chunks_count": 0 }
```

---

## Startup Commands (after system restart)

```bash
# 1. Start backend (Supabase not needed if using hosted Postgres)
cd /path/to/smriti
source venv/bin/activate
nohup uvicorn backend.main:app --host 0.0.0.0 --port 8000 > uvicorn.log 2>&1 &

# 2. (Optional) Cloudflare Tunnel for public HTTPS
cloudflared tunnel run your-tunnel-name &

# 3. Verify
sleep 4 && curl -s http://localhost:8000/status
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Health check + indexed chunk count |
| POST | `/query` | Ask a question, get cited answer |
| POST | `/ingest` | Ingest a document (PDF, text) |
| GET | `/slack/oauth/start` | Begin Slack OAuth flow |
| GET | `/slack/oauth/callback` | Slack OAuth callback |
| DELETE | `/slack/disconnect` | Revoke Slack credentials |
| GET | `/gdrive/oauth/start` | Begin Google Drive OAuth flow |
| GET | `/gdrive/oauth/callback` | Google Drive OAuth callback |
| GET | `/gdrive/status` | Check Drive connection status |
| POST | `/gdrive/sync` | Trigger manual Drive re-sync |
| DELETE | `/gdrive/disconnect` | Revoke Drive credentials |
| GET | `/connections` | List all active connector connections |
| GET | `/auth-config` | Return Supabase auth config for frontend |

---

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `SMRITI_ENCRYPTION_KEY` | Fernet key for token encryption |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase public anon key |
| `SLACK_CLIENT_ID` | Slack OAuth app client ID |
| `SLACK_CLIENT_SECRET` | Slack OAuth app client secret |
| `SLACK_OAUTH_STATE_SECRET` | HMAC CSRF signing secret (64-char hex) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 client secret |
| `GOOGLE_OAUTH_STATE_SECRET` | HMAC CSRF signing secret for Drive OAuth |
| `GDRIVE_REDIRECT_URI` | Full redirect URI for Drive OAuth callback |

See `.env.example` for the complete reference.

---

## Project Structure

```
smriti/
├── backend/
│   ├── main.py              # FastAPI app, query pipeline, API routes
│   ├── grounding.py         # Hallucination firewall + citation verification
│   ├── ingestion.py         # Document and Slack ingestion pipeline
│   ├── slack_connector.py   # Slack API client for message fetching
│   ├── slack_oauth.py       # Slack OAuth 2.0 flow
│   ├── gdrive_oauth.py      # Google Drive OAuth 2.0 flow
│   ├── gdrive_connector.py  # Drive file listing, download, and export
│   ├── vector_store.py      # pgvector hybrid search
│   ├── auth.py              # JWT identity extraction (Supabase)
│   ├── crypto.py            # Fernet encryption utilities
│   ├── db.py                # Connection pool + credential store
│   ├── tenant.py            # Tenant UUID resolution
│   ├── parser.py            # Document parsing (PDF, text, Markdown)
│   ├── graph_analytics.py   # Expert routing from interaction graph
│   ├── sync_scheduler.py    # Background re-sync scheduler (30-min interval)
│   └── eval_harness.py      # Retrieval accuracy evaluation
├── frontend/
│   ├── index.html           # AI assistant split-workspace UI
│   ├── app.js               # Query logic, OAuth flows, UI state
│   ├── style.css            # Dark/light theme styles
│   ├── auth.html            # Google sign-in page
│   ├── callback.html        # OAuth callback handler
│   ├── landing.html         # Public marketing landing page
│   ├── privacy.html         # Privacy policy (GDPR + Google API compliance)
│   ├── terms.html           # Terms of service
│   └── images/              # Logo and section illustrations
├── supabase/
│   └── migrations/          # SQL schema migrations (pgvector, indexes, RLS)
├── tests/
│   ├── integration_test.py   # E2E integration test suite
│   ├── test_org_workspace.py # Organization workspace unit tests
│   └── test_answer_quality.py # Answer quality unit tests
├── requirements.txt          # Python dependencies (all pinned)
├── .env.example             # Environment variable reference
└── README.md
```

---

## Known Limitations

- **phi4-mini response variance.** Like all small models, phi4-mini can generate vague answers for highly ambiguous queries. The grounding firewall catches these and returns a "cannot find" fallback rather than a wrong answer. Swapping to a larger model (Llama 3 8B, Mistral 7B) on a GPU instance is a one-line config change.

- **Indexing latency on first run.** CPU-only embedding generation takes ~0.3s per chunk. A 90-day Slack history with 50,000 messages takes 15–30 minutes on initial index. Subsequent syncs are incremental.

- **Drive scope verification.** `drive.readonly` is a Google-restricted scope. Until verification completes, only manually added test users (up to 100) can connect Drive. See the Drive OAuth section above.

- **Single-threaded generation.** phi4-mini runs on a single CPU thread. Concurrent queries are queued. Production deployments should use a GPU instance with vLLM or Ollama in server mode.

---

## Scaling Path

| Layer | Current (dev) | At 10k users |
|---|---|---|
| Generation | phi4-mini, 1 CPU thread | vLLM on L4 GPU, batched inference |
| Embeddings | nomic-embed-text, sequential | Batched async, 5 concurrent workers |
| Vector index | HNSW cosine, pgvector | Same index — HNSW scales to 100M vectors at sub-200ms p95 |
| Database | Single Supabase instance | Read replicas + PgBouncer connection pooling |
| API | 1 uvicorn worker | Horizontal autoscaling on Cloud Run |

---

## License

MIT License. See `LICENSE` for details.

---

## Author

Built by [Gowtham Pentela](https://github.com/Gowtham-Pentela).

Live at [smriti.one](https://smriti.one) · Privacy: [smriti.one/app/privacy.html](https://smriti.one/app/privacy.html)
