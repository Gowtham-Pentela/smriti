# Smriti

**Your company's private ChatGPT. Drop files in S3, ask questions, get cited answers.**

Smriti is a single-tenant, on-premise AI knowledge assistant for companies that don't want their internal documents leaving their own infrastructure. Users drop PDFs, Word docs, images, audio, and video into an S3 bucket; Smriti parses, embeds, and indexes them automatically. Anyone on the team can then ask natural-language questions and get back grounded answers with numbered source citations.

No OpenAI. No Anthropic. No Google AI. No SaaS. Everything — embeddings, generation, reranking, image understanding, speech-to-text — runs locally on your hardware via Ollama and open-source models.

```
            S3 bucket (your AWS account)
                     │  ObjectCreated → SQS
                     ▼
            ┌──────────────────────┐
            │  Smriti worker       │  parse → chunk → embed (nomic-embed-text)
            │  (FastAPI + Ollama)  │  → store in Postgres + pgvector
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │  Hybrid retrieval    │  pgvector cosine ∥ Postgres FTS
            │  + RRF + rerank      │  → RRF → ONNX cross-encoder → top 8
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │  Generation          │  phi4-mini (Ollama) + grounding firewall
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │  Cited answer        │  every sentence is verified against sources
            └──────────────────────┘
```

## Quick start (local)

```bash
# 1. Clone + install
git clone https://github.com/Gowtham-Pentela/smriti
cd smriti
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Pull Ollama models
ollama pull nomic-embed-text   # 274 MB — embeddings
ollama pull phi4-mini          # ~2.5 GB — generation (Q4_K_M)

# 3. Configure
cp .env.example .env
# (defaults work for local dev)

# 4. Start Postgres + pgvector (any of these works)
docker run -d --name smriti-db -p 54322:5432 \
    -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
# or: supabase start
# or: use a managed RDS instance

# 5. Run
bash start_local.sh
# → http://127.0.0.1:8000/app/
```

## Deploy to AWS

```bash
# 1. Provision the bucket, queue, EventBridge rule, IAM role
cd deploy/aws
terraform init
terraform apply -var "bucket_name=acme-internal-docs"

# 2. Build and push the backend image
cd ../..
./deploy.sh
# (set ECS_SERVICE and ECS_CLUSTER env vars to trigger an automatic rollout)

# 3. Set the env vars from the Terraform outputs on your ECS task:
#      S3_BUCKET, S3_QUEUE_URL
```

See `deploy/aws/README.md` for hardening notes.

## API

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | Liveness probe |
| GET  | `/status` | Chunk count + S3 worker status |
| GET  | `/me` | Current user identity |
| GET  | `/files` | List indexed sources |
| POST | `/ingest` | File upload fallback (PDF, image, audio, video, text, code) |
| POST | `/clear` | Wipe the company index |
| POST | `/query` | **Main endpoint** — ask a question, get a cited answer |
| GET  | `/s3/status` | S3 worker state + recent ingestions |
| POST | `/s3/resync?folder=…` | Local-folder ingest (testing) |

## What Smriti indexes

Anything in the S3 bucket (or uploaded through the UI) that Smriti can parse:

- **Documents** — PDF, DOCX, TXT, MD, CSV, JSON, YAML, SQL
- **Code** — Python, JS/TS, Go, Java, C/C++, Rust, Shell
- **Images** — PNG, JPG, JPEG, WEBP, GIF (described via llava:7b or moondream)
- **Audio** — MP3, WAV, M4A, FLAC, OGG (transcribed via local Whisper)
- **Video** — MP4, MOV, MKV, WEBM (audio extracted via ffmpeg → Whisper)

## What you can ask

Anything you'd ask a colleague who's read everything. Smriti:

- Pulls relevant chunks from pgvector + Postgres full-text search
- Combines them with Reciprocal Rank Fusion
- Reranks with an ONNX cross-encoder (`ms-marco-MiniLM-L6-v2`)
- Generates an answer with `phi4-mini`
- Runs every sentence through a **grounding firewall** that strips anything not supported by the retrieved sources
- Returns numbered citations to the original files

If the answer isn't in the documents, Smriti says so — it does not hallucinate.

## Configuration

All settings are environment variables. See `.env.example` for the full list. The most important:

| Var | Default | What it does |
|---|---|---|
| `COMPANY_TENANT_ID` | `00000000-…-0001` | The single company tenant UUID. |
| `DATABASE_URL` | `postgresql://…54322/postgres` | Postgres + pgvector. |
| `SMRITI_CHAT_MODEL` | `phi4-mini:latest` | Ollama model for generation. |
| `SMRITI_EMBED_MODEL` | `nomic-embed-text` | Ollama model for embeddings. |
| `SMRITI_WHISPER_MODEL` | `tiny` | tiny / base / small / medium / large. |
| `SMRITI_DEV_MODE` | `true` | Skips Supabase JWT validation. **Local dev only.** |
| `SMRITI_ENV` | `local` | `local` / `dev` / `production`. |
| `S3_BUCKET` | empty | Bucket the worker reads from. |
| `S3_QUEUE_URL` | empty | SQS queue (fed by EventBridge). |
| `AWS_REGION` | `us-east-1` | For boto3. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. `*` is forbidden in production. |

## Privacy

- **No data egress.** All inference runs on your hardware (Ollama). No calls to OpenAI, Anthropic, Google, or any third-party AI API.
- **Local embeddings.** `nomic-embed-text` runs on your machine.
- **Encrypted at rest.** Tokens are AES-encrypted with Fernet. (Note: S3 ingestion does not require tokens.)
- **Tenant isolation.** Every chunk is RLS-gated to your company's tenant UUID.
- **No telemetry.** The audit log (`data/audit_log.json`) is local NDJSON. Delete it any time.

## Local resources

The active stack fits in ~3.5 GB RAM:

| Component | RAM |
|---|---|
| phi4-mini (Q4_K_M) | ~3.2 GB |
| nomic-embed-text | ~300 MB |
| Postgres + FastAPI | ~500 MB |
| **Total** | **~4 GB** |

Production (with GPU): swap phi4-mini for a 7B/8B model and run with vLLM for batched inference.

## Author

Built by [Gowtham Pentela](https://github.com/Gowtham-Pentela).

## License

MIT.
