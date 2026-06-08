# Knowledge Guardian Foundry (KGF)

**Your organization's institutional knowledge, made permanently queryable.**

KGF is a private, on-premise AI assistant that indexes your Slack workspace, Google Drive, Confluence pages, and PDF documents, then lets every engineer in your team ask natural language questions and get cited, grounded answers in under 10 seconds. No hallucinations. No data leaving your infrastructure. Every answer links back to its source.

Live demo: [smriti.one](https://smriti.one)

---

## The Problem

Every growing engineering team hits the same invisible wall:

**Knowledge Silos.** The answer to your question exists somewhere. It is in a Slack thread from 8 months ago, a Confluence page that was never updated, and the head of an engineer who left last quarter. Nobody can find it.

**Onboarding Friction.** A new engineer spends their first 3 weeks asking questions that were already answered somewhere. Every senior teammate loses hours explaining context that already exists.

**Brain Drain.** When a senior engineer leaves, they take 3 years of architectural decisions, deployment gotchas, and process knowledge with them. It is irretrievable. No documentation process fixes this retroactively.

KGF solves all three by indexing what already exists and making it instantly queryable.

---

## How It Works

```
Slack / Drive / Confluence / PDFs
          |
          v
  [Ingestion Pipeline]
  Chunking + Embedding (nomic-embed-text)
          |
          v
  [Supabase + pgvector]
  768-dim vectors stored per tenant schema
          |
          v
  User asks a question
          |
          v
  [Hybrid Retrieval]
  Cosine similarity + keyword matching
          |
          v
  [tinyllama generation]
  Synthesizes answer from retrieved chunks
          |
          v
  [Grounding Firewall]
  Every sentence verified against source chunks
  Unsupported sentences stripped before response
          |
          v
  Cited answer with source links
```

---

## Architecture

### Stack

| Layer | Technology | Why |
|---|---|---|
| API | FastAPI (Python) | Async, fast, clean OpenAPI docs |
| Database | Supabase + pgvector | Managed Postgres with native vector search |
| Embeddings | nomic-embed-text | MTEB 62.4, beats OpenAI ada-002, fully offline |
| Generation | tinyllama:1.1b | 638MB, runs on 8GB RAM CPU with no GPU |
| Encryption | Fernet (cryptography) | AES-128-CBC for OAuth token storage |
| Tunnel | Cloudflare Tunnel | Zero-trust HTTPS with no exposed ports |
| Frontend | Vanilla HTML/CSS/JS | No framework dependency, split-workspace UI |

### Multi-Tenant Data Isolation

Each organization gets its own Postgres schema named `tenant_{uuid}`. Row-level security prevents cross-tenant data access. A single KGF instance can serve multiple organizations without data leakage between them.

```sql
-- Each tenant's data lives in its own schema
CREATE SCHEMA IF NOT EXISTS tenant_1b87e7de_de9c_5f96_87d6_b163402ddd4c;

-- Chunks table with pgvector embedding column
CREATE TABLE tenant_{uuid}.vector_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       TEXT NOT NULL,
    source_type     TEXT NOT NULL,   -- 'slack', 'document', 'gdrive'
    channel_or_space TEXT,
    content         TEXT NOT NULL,
    embedding       vector(768),
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
);
```

### Retrieval Pipeline

Retrieval uses hybrid search: semantic cosine similarity from pgvector combined with PostgreSQL full-text search (tsvector). This ensures that exact keyword matches (like function names or ticket IDs) are surfaced even when the semantic similarity is low.

```python
# Simplified retrieval query
SELECT
    source_id,
    channel_or_space,
    content,
    1 - (embedding <=> $query_embedding) AS cosine_score,
    ts_rank(to_tsvector('english', content), plainto_tsquery($query)) AS keyword_score
FROM vector_chunks
ORDER BY (cosine_score * 0.7 + keyword_score * 0.3) DESC
LIMIT 4;
```

### Grounding Firewall

The grounding firewall (`backend/grounding.py`) is the hallucination prevention layer. It sits between the LLM output and the API response.

For every sentence the LLM generates:
1. Citations are extracted using a regex parser that handles both `[Citation: source, location]` and `[Cite: source (location)]` formats.
2. The cited source is cross-referenced against the retrieved chunks.
3. Word overlap between the sentence and its claimed source chunk is computed (stopwords excluded).
4. If overlap exceeds 60%, the sentence passes and is returned with its verified citation.
5. If overlap is below 60%, the sentence is stripped to prevent hallucination.
6. If all sentences are stripped, the response falls back to "I cannot find the answer in the provided documents."

This approach was chosen over a second LLM verification call because the verification call added 20-90 seconds of latency per query and blocked the asyncio event loop.

---

## Benchmark Results

KGF's retrieval pipeline was evaluated against the [EnterpriseRAG-Bench](https://github.com/microsoft/EnterpriseRAG-Bench) corpus, a realistic benchmark of 500 enterprise knowledge questions spanning Slack, Confluence, JIRA, GitHub, and Google Drive sources.

### Setup

- **Schema:** `tenant_redwood_inference_prod` provisioned via `rag_bench/01_migration.sql`
- **Indexed chunks:** 4,048 (96 targeted EnterpriseRAG-Bench Slack threads + existing KGF workspace data)
- **Embeddings:** `nomic-embed-text` running locally via Ollama
- **Retrieval:** Hybrid search (70% cosine similarity + 30% normalized keyword scoring)
- **Candidate architecture:** HNSW pre-selection (top 300) with re-ranking, `hnsw.ef_search = 200`

### Retrieval Performance

| Metric | Value |
|---|---|
| Questions evaluated (Slack-filtered) | 79 |
| **Slack Question Hit Rate** | **92.4% (73/79 questions retrieved)** |
| **Slack Mean Recall @ 10** | **74.84%** |
| Mean Recall @ 10 (all 345 questions) | 17.14% (depressed: 266 non-Slack docs not indexed) |
| Mean Precision @ 10 | 2.14% |
| **p50 retrieval latency** | **115.9ms** |
| **p95 retrieval latency** | **144.0ms** |

> **Hit Rate vs Mean Recall:** 73/79 = 92.4% is the fraction of Slack questions where at least one expected document was retrieved. Mean Recall of 74.84% is lower because some questions require multiple documents (e.g. a question needing 9 docs where only 4 are retrieved contributes 4/9 = 44% recall, not 100%). Both metrics are reported; hit rate is the operational number.
>
> The overall recall of 17.14% is intentionally low. Only Slack data was indexed. The 266 non-Slack questions (Confluence, JIRA, GitHub, Google Drive) score zero recall by design.

### Query Latency Optimization

The initial retrieval query ran inline substring checks across all 4,048 chunks, producing p50 latency of 1,430ms. Redesigning to a two-phase architecture (HNSW index pre-selects 300 candidates, then re-ranks with keyword scoring) reduced p50 to 116ms, a 12.3x improvement at equivalent recall.

```
Before: full scan + substring check on 4,048 rows  ->  p50 = 1,430ms
After:  HNSW top-300 + re-rank                      ->  p50 =   116ms  (12.3x faster)
```

### Remaining Slack Failures (6 of 79)

| Question | Root cause |
|---|---|
| `qst_0099` LB mitigation | Target sim 0.57, outranked by noise doc at 0.64 |
| `qst_0267` GPU load test | Target sim 0.66, outranked by noise doc at 0.70 |
| `qst_0296` Animated demo | Target sim 0.61, outranked by noise doc at 0.65 |
| `qst_0351` Canary mismatch | Requires 4 docs: 1 Slack + 3 Confluence/JIRA (not indexed) |
| `qst_0365` Fast tier SLO | Requires 9 docs: all from Confluence/JIRA (not indexed) |
| `qst_0366` Dedicated tenant tail latency | Requires 9 docs: all from Confluence/JIRA (not indexed) |

The 3 "outranked" failures are a signal-to-noise problem from mixing 3,952 existing workspace docs with 96 benchmark-specific docs. Indexing Confluence and JIRA alongside Slack resolves all 6 remaining failures.

---

## Model Selection

### Why tinyllama for Generation

KGF is designed to run on hardware that engineering teams actually have: a developer laptop or a small cloud instance. The constraint is RAM, not model quality preference.

| Model | Size | Outcome on 8GB RAM |
|---|---|---|
| mistral:7b | 4.1GB | OOM crash alongside Docker + FastAPI |
| qwen2.5-coder:3b | 1.9GB | OOM under concurrent load |
| llama3.2:3b | 2.0GB | Same RAM ceiling issue as qwen |
| tinyllama:1.1b | 638MB | Selected: fits with headroom, 4-7s responses |

tinyllama's smaller size is compensated by the grounding firewall. The model does not need to recall facts from training data. Its only job is to synthesize and paraphrase content from the retrieved chunks. Factual accuracy is enforced by the grounding layer, not the model.

In production on a GPU instance (A10, L4, or similar), swapping to Llama 3.1 8B or Mistral 7B is a one-line config change. The pipeline is fully model-agnostic.

### Why nomic-embed-text for Embeddings

The embedding model is the accuracy ceiling of the entire system. If the wrong chunks are retrieved, no generation model can produce the right answer.

| Model | Dimensions | MTEB Score | Size | Privacy |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 384 | 56.3 | 80MB | Local |
| nomic-embed-text | 768 | 62.4 | 274MB | Local |
| OpenAI ada-002 | 1536 | 61.0 | API only | Cloud |

nomic-embed-text outperforms OpenAI's ada-002 on the MTEB retrieval benchmark for technical content, runs entirely offline (zero data egress), and its 768-dimensional vectors give pgvector sufficient resolution to distinguish semantically similar but contextually different engineering discussions.

---

## Slack OAuth Integration

KGF uses Slack's standard OAuth 2.0 flow to connect workspaces. No Slack credentials are ever stored in plaintext. The bot token received after authorization is encrypted using Fernet (AES-128-CBC) before being written to Postgres.

**Required bot token scopes:**
- `channels:history` - read message history
- `channels:read` - list channels
- `users:read` - resolve user IDs to display names
- `channels:join` - join public channels to read them

**OAuth flow:**
1. User clicks "Connect Slack" in the UI
2. Backend redirects to Slack authorization URL with CSRF state token (HMAC-signed, 10-minute TTL)
3. User authorizes in their Slack workspace
4. Slack redirects to `/slack/oauth/callback` with an authorization code
5. Backend verifies CSRF state, exchanges code for bot token via Slack API
6. Token is Fernet-encrypted and stored in `tenant_credentials` table
7. User is redirected back to the UI with a success banner

---

## Privacy Architecture

KGF is designed for organizations that cannot send internal data to third-party AI APIs.

- **No OpenAI, Anthropic, or Gemini API calls.** Inference runs entirely on your machine via Ollama.
- **No cloud embedding services.** nomic-embed-text runs locally.
- **No data egress.** Slack messages, documents, and embeddings never leave your infrastructure.
- **Encrypted credentials.** All OAuth tokens are Fernet-encrypted at rest.
- **Tenant isolation.** Each organization's data is schema-isolated in Postgres with a unique UUID namespace.
- **HTTPS everywhere.** Cloudflare Tunnel provides TLS termination without exposing ports or IPs.

---

## Prerequisites

- macOS or Linux
- Python 3.11+
- Docker Desktop (for Supabase)
- [Ollama](https://ollama.ai) installed
- [Supabase CLI](https://supabase.com/docs/guides/cli) installed
- [Cloudflare Tunnel CLI](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) (optional, for public URL)

---

## Local Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/Gowtham-Pentela/smriti.git
cd smriti
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull Ollama models

```bash
# Required
ollama pull nomic-embed-text   # 274MB, embeddings
ollama pull tinyllama           # 638MB, generation

# Optional (for PDF image processing)
ollama pull llava:7b
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```bash
# Generate a Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate OAuth state secret
python -c "import secrets; print(secrets.token_hex(32))"
```

For Slack OAuth, create an app at [api.slack.com/apps](https://api.slack.com/apps) with these bot scopes: `channels:history`, `channels:read`, `users:read`, `channels:join`. Set the redirect URL to `http://localhost:8000/slack/oauth/callback`.

### 4. Start Supabase

```bash
supabase start
```

Wait for the "Started supabase local development setup" message. The database runs on port 54322.

### 5. Start the backend

```bash
source venv/bin/activate
export $(grep -v '^#\|^$' .env | xargs)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
```

### 6. Open the UI

Open `frontend/index.html` in your browser, or serve it:

```bash
cd frontend && python -m http.server 3000
```

Navigate to `http://localhost:3000`.

### 7. Verify

```bash
curl -s http://localhost:8000/status | python3 -m json.tool
```

Expected response:
```json
{
  "indexed_chunks_count": 0,
  "indexed_files": [],
  "status": "ok"
}
```

---

## Startup Commands (after system restart)

Run these in order:

```bash
# 1. Open Docker Desktop first, then:
cd /path/to/smriti && supabase start

# 2. Start the backend
source venv/bin/activate
export $(grep -v '^#\|^$' .env | xargs)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level warning &

# 3. (Optional) Start Cloudflare tunnel for public access
cloudflared tunnel run your-tunnel-name &

# 4. Verify
sleep 5
curl -s http://localhost:8000/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'OK: {d[\"indexed_chunks_count\"]} chunks indexed')
"
```

---

## Ingesting Data

### Slack

Click "Connect Slack" in the assistant UI sidebar, authorize the OAuth flow, then trigger ingestion from the connectors panel. For programmatic ingestion:

```bash
curl -X POST http://localhost:8000/ingest-slack \
  -H "Content-Type: application/json" \
  -H "X-Dev-User-Email: you@yourcompany.com" \
  -d '{
    "bot_token": "xoxb-your-token",
    "channel_ids": ["C01234ABCDE", "C09876ZYXWV"],
    "days_back": 90
  }'
```

### Documents (PDF, text)

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-Dev-User-Email: you@yourcompany.com" \
  -F "file=@/path/to/document.pdf"
```

---

## Querying

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-Dev-User-Email: you@yourcompany.com" \
  -d '{"query": "How does authentication work?"}'
```

Response:
```json
{
  "response": "In production, Identity-Aware Proxy validates Google SSO at the load balancer [Citation: auth.py, kgf-backend].",
  "citations": [
    {"source": "auth.py", "location": "kgf-backend"}
  ],
  "retrieved_context": [...],
  "latency_seconds": 5.2
}
```

---

## Production Deployment (GCP)

The `gcp_infrastructure/terraform/` directory contains Terraform configs for a production GCP deployment:

| Component | Service |
|---|---|
| Backend API | Cloud Run |
| Database | Cloud SQL (Postgres 15 + pgvector) |
| Inference | Compute Engine (A10 or L4 GPU) |
| Authentication | Identity-Aware Proxy (replaces KGF_DEV_MODE) |
| Secrets | Secret Manager |
| Container registry | Artifact Registry |

Estimated monthly cost for a 100-engineer team: under $400 USD.

To deploy:
```bash
cd gcp_infrastructure/terraform
terraform init
terraform plan
terraform apply
```

Then build and push containers:
```bash
docker build -f Dockerfile.backend -t gcr.io/YOUR_PROJECT/kgf-backend .
docker push gcr.io/YOUR_PROJECT/kgf-backend
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Health check + chunk count |
| POST | `/query` | Ask a question, get cited answer |
| POST | `/ingest` | Ingest a document (PDF, text) |
| POST | `/ingest-slack` | Ingest Slack channels by bot token |
| GET | `/slack/oauth/start` | Begin Slack OAuth flow |
| GET | `/slack/oauth/callback` | Slack OAuth callback handler |

---

## Environment Variables

See `.env.example` for the full reference. Key variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `KGF_ENCRYPTION_KEY` | Fernet key for token encryption |
| `KGF_DEV_MODE` | Set to `true` to skip GCP IAP auth |
| `KGF_DEV_USER_EMAIL` | Email used as identity in dev mode |
| `KGF_FORCE_TENANT_ID` | UUID to force all users into one tenant (demo/single-tenant) |
| `SLACK_CLIENT_ID` | Slack app client ID |
| `SLACK_CLIENT_SECRET` | Slack app client secret |
| `SLACK_OAUTH_STATE_SECRET` | CSRF state signing secret (64-char hex) |

---

## Project Structure

```
smriti/
├── backend/
│   ├── main.py              # FastAPI app, query pipeline, API routes
│   ├── grounding.py         # Hallucination firewall + citation verification
│   ├── ingestion.py         # Document and Slack ingestion pipeline
│   ├── slack_connector.py   # Slack API client for message fetching
│   ├── slack_oauth.py       # OAuth 2.0 flow (install + callback)
│   ├── vector_store.py      # pgvector hybrid search
│   ├── auth.py              # Identity extraction (dev mode + GCP IAP)
│   ├── crypto.py            # Fernet encryption utilities
│   ├── db.py                # Database connection pool + credential store
│   ├── tenant.py            # Tenant UUID resolution
│   ├── parser.py            # Document parsing (PDF, text, Markdown)
│   ├── image_extractor.py   # PDF image extraction (PyMuPDF)
│   ├── vision_processor.py  # Image OCR via LLaVA
│   ├── graph_analytics.py   # Expert routing from interaction graph
│   ├── sync_scheduler.py    # Background re-sync scheduler
│   ├── eval_harness.py      # Retrieval accuracy evaluation
│   └── doc_classifier.py    # Document type classifier
├── frontend/
│   ├── index.html           # AI assistant split-workspace UI
│   ├── app.js               # Query logic, OAuth param handling, UI state
│   ├── style.css            # Assistant UI styles (dark/light theme)
│   ├── landing.html         # Public landing page
│   ├── landing.css          # Landing page styles
│   ├── landing.js           # Landing page interactions
│   ├── privacy.html         # Privacy policy
│   ├── terms.html           # Terms of service
│   └── images/              # Section illustration PNGs
├── supabase/
│   └── migrations/          # SQL schema migrations (pgvector, indexes, RLS)
├── gcp_infrastructure/
│   ├── terraform/           # GCP resource definitions
│   ├── kubernetes/          # UI deployment manifest
│   └── database/            # Production schema SQL
├── data/
│   └── benchmark_results.md # Retrieval accuracy evaluation results
├── Dockerfile.backend        # Backend container
├── Dockerfile.ui             # Frontend container
├── requirements.txt          # Python dependencies
├── demo.sh                  # One-command demo startup
├── run.sh                   # Development startup
├── deploy.sh                # Production deployment helper
└── .env.example             # Environment variable reference
```

---

## Known Limitations

- **tinyllama response variance.** The 1.1B model occasionally generates vague or meta-commentary responses for ambiguous queries. The grounding firewall catches these and returns "I cannot find the answer" rather than a wrong answer. Using a larger model (Llama 3.1 8B, Mistral 7B) on a GPU instance resolves this.

- **Indexing latency.** CPU-only embedding generation takes approximately 0.3 seconds per chunk. A 90-day Slack history with 50,000 messages takes 15-30 minutes to index on first run. Subsequent syncs are incremental (only new messages).

- **Image OCR quality.** Low-resolution scans (below 150 DPI) are upscaled 2x before processing. LLaVA OCR accuracy on complex diagrams is limited compared to dedicated OCR services.

- **Single-threaded generation.** tinyllama runs on a single CPU thread. Concurrent queries are queued, not parallelized. Production deployments should use GPU inference with a proper inference server (vLLM, TGI, or Ollama in server mode).

- **Retrieval signal-to-noise at mixed corpora.** When a corpus mixes high-volume general workspace messages with targeted benchmark documents, general messages can outrank specific target documents by a cosine similarity margin of 0.04 to 0.07. Increasing `hnsw.ef_search` to 200 and the candidate pool to 300 mitigates this. For production, a per-source retrieval weight or a source-aware re-ranking pass would eliminate it.

---

## Scaling to 10,000 Users

The current architecture bottleneck is single-threaded tinyllama generation. Here is the production scaling path:

| Layer | Current (dev) | At 10k users |
|---|---|---|
| Generation | tinyllama:1.1b, 1 CPU thread | vLLM on A10/L4 GPU, multi-slot batched inference |
| Embeddings | nomic-embed-text, sequential | Batched async, 5 concurrent workers (already implemented) |
| Vector index | HNSW cosine, pgvector | Same index, HNSW scales to 100M vectors at sub-200ms p95 |
| Database | Single Supabase instance | Read replicas + connection pooling via PgBouncer |
| API | 1 uvicorn worker | Horizontal autoscaling on Cloud Run, stateless workers |
| Ingestion | Sync, per-request | Queue-based (Pub/Sub or SQS), async worker pool |

The retrieval pipeline (embedding + HNSW search + re-rank) is already well within latency budget at scale: p95 is 144ms with 4,048 chunks. HNSW index complexity is O(log n), so scaling to 10 million chunks adds approximately 30ms at p95.

The generation step (4-7 seconds on tinyllama CPU) is the only serial bottleneck. Replacing with vLLM on a single L4 GPU ($0.80/hr on GCP) reduces generation to under 1 second and enables concurrent request batching. At 10,000 daily active users with an average of 5 queries each at business hours, peak QPS is approximately 7. A single L4 instance handles 20+ QPS at under 1s p95.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Copy `.env.example` to `.env` and fill in your values
4. Run `supabase start` and `uvicorn backend.main:app --reload`
5. Submit a pull request with a clear description of the change

---

## License

MIT License. See `LICENSE` for details.

---

## Author

Built by [Gowtham Pentela](https://github.com/Gowtham-Pentela).

Live at [smriti.one](https://smriti.one).
