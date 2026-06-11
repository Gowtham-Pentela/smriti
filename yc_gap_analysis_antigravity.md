# Smriti vs. Tom Blomfield's "Company Brain" Vision
### Gap Analysis + 45-Day YC Application Roadmap

---

## Context: What Is the Vision?

Tom Blomfield (YC Partner, Monzo co-founder) published the **"Company Brain"** as a formal entry in YC's **Request for Startups (Summer 2026)**. It is one of the most explicit signals YC has ever sent about what category they believe will produce the next wave of billion-dollar companies.

His thesis in one sentence:

> *"The most important organizational knowledge — the unwritten rules, the tribal memory, the why behind every decision — is fragmented, ephemeral, and currently inaccessible to AI. The Company Brain captures all of it, structures it, keeps it current, and turns it into an executable interface that AI agents can actually use to do real work."*

The key distinction: it is **not** a search tool, **not** a chatbot, **not** a wiki. It is a **living, structured, self-updating knowledge layer** that enables autonomous AI action.

---

## Part 1: What You Have Built ✅

Smriti (smriti.one) is a genuinely impressive prototype. Here is an honest inventory:

### ✅ Ingestion Layer (Partially Built)
- **PDF ingestion** — page-by-page parsing via `pypdf`, scanned pages via LLaVA vision pipeline (`llava:7b`)
- **Slack connector** — OAuth 2.0 flow, Fernet-encrypted token storage, channel history pull with deduplication
- **Code/text/markdown** — raw file parsing with extension-aware handling
- **30-minute background sync** scheduler for incremental re-ingestion (`sync_scheduler.py`)
- **File-hash deduplication** — skips unchanged files on re-index
- **Video transcription** — `transcribe_video()` in `transcription.py`

### ✅ Storage + Retrieval (Strong)
- **pgvector** in Supabase (local + cloud) with 768-dim `nomic-embed-text` embeddings
- **Hybrid search** — 70% semantic cosine + 30% BM25 keyword re-ranking
- **HNSW index** — p50 retrieval at 116ms, p95 at 144ms (benchmarked against 4,048 chunks)
- **Benchmark results** — 92.4% hit rate on 79 Slack questions from Microsoft's EnterpriseRAG-Bench

### ✅ Generation + Grounding (Unique Differentiator)
- **phi4-mini:latest** for generation — 2.4GB, superior reasoning compared to tinyllama/qwen2.5 at small RAM footprint
- **Grounding firewall** (`grounding.py`) — sentence-level hallucination prevention via word-overlap verification; strips unsupported claims instead of returning them
- **Citation extraction** — every answer links back to its source chunk
- **Fallback / Answer Quality Improvements** — returns clean contact-aware fallback containing workspace admin's email if similarity score < 0.51 or if grounding validation fails, with robust normalized quote handling.
- **Query Classification** — detects query type (factual vs. exploratory) to dynamically tune prompts and model temperature (0.0 vs 0.3) for lower hallucination risk.

### ✅ Privacy Architecture (Strong Moat)
- **100% offline inference** — no OpenAI/Anthropic/Gemini API calls
- **No data egress** — embeddings and LLM run locally via Ollama
- **Fernet-encrypted OAuth tokens** at rest
- **Per-user data isolation** (just implemented) — Supabase `user_id` UUID = private data silo
- **Cloudflare Tunnel** — zero-trust HTTPS without exposed ports
- **Terms of Service + Privacy Policy pages** (live on smriti.one)

### ✅ Infrastructure
- **FastAPI backend** with asyncpg connection pool (min 5 / max 20)
- **Supabase cloud auth** — Google OAuth via Supabase, PKCE flow, self-hosted `supabase.js`
- **Multi-tenant schema** — row-level isolation ready for org-level upgrade
- **Terraform/GCP infrastructure** — Cloud Run, Cloud SQL, Compute Engine configs in repo
- **Dockerfiles** — backend and UI containers defined
- **Evaluation harness** — `eval_harness.py` for retrieval accuracy testing
- **Document classifier** — `doc_classifier.py` for routing

### ✅ Product Surface
- **Live demo** at smriti.one (Cloudflare tunnel + MacBook Air M2)
- **Landing page** with problem/solution framing
- **Workspace UI** — dark/light theme, split-pane, citation display
- **Auth flow** — Google OAuth → Supabase → callback → workspace
- **Indexing progress UI** — real-time progress bar
- **Status endpoint** — chunk count + source count per user

---

## Part 2: What Is Missing vs. Tom Blomfield's Vision ❌

### ❌ 1. It Is Still a Chatbot, Not a "Living Map"

**Blomfield's vision:** A Company Brain is not a question-answering tool. It is a *structured, versioned, semantic graph* of how the organization actually works — capturing not just documents but **decisions, relationships, outcomes, and context changes over time**.

**What you have:** A very good RAG pipeline. Users upload documents, ask questions, get cited answers. This is the *ingestion layer*, not the brain itself.

**What is missing:**
- No knowledge graph of entities (people, projects, systems, decisions)
- No versioning — when knowledge changes, old chunks persist alongside new ones
- No proactive knowledge capture — the system only knows what you explicitly upload
- No structured facts extraction — chunks are unstructured text, not semantic facts
- No relationship mapping between concepts across sources

---

### ❌ 2. No "Executable Skills File" — The AI Can't *Do* Anything

**Blomfield's vision:** The Company Brain doesn't just answer questions. It provides an *executable interface* for AI agents — structured context that allows agents to actually perform business processes end-to-end.

**What you have:** Answers. Excellent, cited, grounded answers. But the workflow stops there.

**What is missing:**
- No agent layer — no tool-use, no multi-step task execution
- No integration with action systems (sending emails, creating tickets, updating wikis)
- No workflow automation triggered by queries
- No "this is how we do X" → agent that actually does X

---

### ❌ 3. No Google Drive / Confluence / Notion / Jira Connectors

**Blomfield's vision:** The Company Brain ingests from *every* digital source — "all the company's digital exhaust."

**What you have:** Slack + PDFs/text files. The README mentions Google Drive and Confluence but they are not implemented.

**What is missing:**
- Google Drive connector (Docs, Sheets, Slides)
- Confluence connector
- Notion connector
- Jira/Linear/GitHub Issues connector
- Email connector (Gmail/Outlook)
- Calendar connector

---

### ❌ 4. No Self-Updating / Continuous Ingestion

**Blomfield's vision:** The brain "keeps itself current as the business evolves." Knowledge doesn't go stale because the system is continuously monitoring and re-ingesting.

**What you have:** A 30-minute sync scheduler exists in `sync_scheduler.py` but it only re-syncs Slack. The folder indexing is manual (user clicks "Index Folder"). There is no webhook-driven real-time update.

**What is missing:**
- Webhook listeners (Slack Events API for real-time messages)
- File-change watchers for local documents
- Auto-detection of stale/outdated knowledge
- Version-aware updates (when a doc changes, old version is deprecated, not just overwritten)

---

### ❌ 5. No Knowledge Deduplication / Conflict Resolution

**Blomfield's vision:** A brain that "structures and deduplicates information." If two Slack threads say contradictory things about the deployment process, the brain resolves this — it doesn't store both as equally valid.

**What you have:** Content-hash deduplication (same content → skip). But two different chunks saying contradictory things about the same topic will both be stored and both be retrieved.

**What is missing:**
- Semantic deduplication across sources
- Conflict detection ("this doc says X but this Slack thread says Y")
- Confidence scoring for competing facts
- "Last updated by" provenance tracking

---

### ❌ 6. No Team Collaboration Layer

**Blomfield's vision:** A Company Brain is fundamentally a *team* tool. It surfaces "who knows what" across the organization, and allows colleagues to trust and build on each other's shared knowledge.

**What you have:** Per-user isolation (just built). Each user has their own private silo. This is the *opposite* of a shared company brain.

**What is missing:**
- Workspace/team model — users belong to an organization
- Shared knowledge spaces within an org
- "Who knows about X" — expert routing (partially in `graph_analytics.py` but not surfaced in UI)
- Annotation and correction layer — colleagues can flag wrong answers
- Role-based access control — some docs are finance-only, some are engineering-only

---

### ❌ 7. No Paying Customers

**YC's primary signal:** Revenue. A single paying customer — even at $100/month — is worth 100x more than a polished demo in a YC application.

**What you have:** A live demo at smriti.one, benchmarks, and a clean architecture. No paying customers. No pilot commitments. No LOIs.

---

### ❌ 8. Weak on the "Why Now" / Market Timing Story

**YC wants to understand:** Why is this the exact right moment? What changed in the last 12 months that makes this possible/necessary?

**What you have:** Good product narrative, but no explicit "why now" argument in your pitch materials.

**The real answer you should be making:**
- LLMs crossed the capability threshold in 2023–2024 (GPT-4, Claude 3)
- Local models are now viable for enterprise privacy constraints (Ollama, llama.cpp)
- The enterprise AI adoption wave is being blocked by the knowledge gap problem — not model capability
- Every company is now desperate to "AI-ify" their operations but can't because their knowledge is inaccessible

---

## Part 3: 45-Day YC Readiness Roadmap

**Deadline assumption:** YC W27 application window. You have 45 days from today (June 9) → July 24.

**Prioritization principle:** YC invests in people + traction first, product second. Every hour you spend on features that don't get you a paying customer or a better application is wasted.

---

### 🔴 Week 1–2: Get One Paying Customer (Days 1–14)
*This is the most important thing you can do. Everything else is secondary.*

**Actions:**
1. **Identify 10 specific people** who could be your first customer. Not companies — specific humans at companies who feel the pain. Think: engineering managers, startup CTOs, founders with 5–15 person teams where "who knows X" is a daily friction point.
2. **Send 10 direct messages** (LinkedIn, email, Twitter/X, Slack communities). Subject: "I built something that might fix [specific problem you observed at their company]." Do NOT send a pitch deck. Send a 2-sentence problem description and ask for 15 minutes.
3. **Charge $50–200/month** even for a prototype. Frame it as "design partner access." The amount matters less than the act of paying.
4. **Target verticals:** Legal teams (discovery, precedent search), engineering teams at seed-stage startups (onboarding friction), consulting firms (institutional knowledge loss).

**Success metric:** 1 person with a credit card on file.

---

### 🔴 Week 1–2: Fix the Biggest Product Gap (Days 1–14)
*Do this in parallel with customer outreach. Pick one connector that your target customer already uses.*

**Recommended: Google Drive connector**
- Most teams with PDFs also have Google Drive
- Google has a well-documented Python API
- A working Drive connector turns your "PDF tool" into a "company knowledge tool"
- Estimated effort: 3–4 days

**OR: Slack webhook (real-time)**
- Change from pull (30-min sync) to push (Slack Events API webhook)
- Every new message is ingested in real-time
- Turns KGF from "historical archive" to "live brain"
- Estimated effort: 2–3 days

---

### 🟡 Week 3: Build the Team/Workspace Model (Days 15–21)
*Currently per-user isolated. YC's "Company Brain" is a team tool, not a personal tool.*

**Actions:**
1. Add `organizations` table to Postgres — `org_id`, `org_name`, `created_at`
2. Add `org_members` — `user_id`, `org_id`, `role` (admin/member)
3. Change `tenant_id` in vector_chunks from `user_id` to `org_id` for shared workspaces
4. Add simple "Invite teammate" UI (generate invite link, user signs up → joins org)
5. Keep personal mode for individual users (freelancers, researchers)

**Why this matters:** A single-user knowledge tool is Notion. A team knowledge tool is Confluence + AI. The latter is 10x the market and 10x the price.

---

### ✅ Week 3: Upgrade the Generation Model & Implement Answer Quality Improvements (Days 15–21)
*tinyllama produces inconsistent output. For a demo that converts investors, you need reliability.*

**Actions:**
1. Upgraded local Ollama generation model to `phi4-mini:latest` (2.4GB) for superior reasoning capability.
2. Implemented dynamic query classification (factual vs. exploratory) with tailored system prompts and tuned generation parameters (temperature 0.0 vs 0.3) to maximize accuracy and logic control.
3. Implemented a vector similarity score threshold (similarity < 0.51) to instantly trigger a clean fallback response on completely out-of-scope questions.
4. Set up an email-containing fallback message: `"I don't have that information from the indexed documents, please contact <admin_email>"` using workspace configuration.
5. Standardized response formatting and added robust handling/normalization for curly quotes (`’` to `'`) to ensure seamless regex matching in grounding checks.

---

### 🟡 Week 4: Polish the Demo & Onboarding (Days 22–28)

**The YC demo video is as important as the application text.**

**Actions:**
1. **Record a 60-second Loom** showing:
   - Problem statement (15 sec) — "Engineers spend X hours/week searching for answers that already exist"
   - Indexing a real folder (10 sec)
   - Asking 3 different questions, getting cited answers (25 sec)
   - Brief mention of privacy / no data leaving machine (10 sec)
2. **Improve the landing page:**
   - Add a "Request Early Access" email capture form
   - Add 2–3 testimonial quotes (even from your test users)
   - Add a "How it Works" animated diagram
3. **Add one-click demo environment:** Pre-indexed corpus so any visitor can try the product without uploading docs

---

### 🟡 Week 4–5: Expert Routing UI (Days 22–35)

The `graph_analytics.py` file already does expert scoring from interaction patterns. This is a **unique differentiator** that competitors don't have.

**Actions:**
1. Surface the "Ask the Expert" feature in the UI — after a query, show "3 people at your company know most about this topic"
2. Make it clickable — clicking opens a Slack DM or email draft
3. This is the "social layer" that moves Smriti from "search engine" to "team intelligence"

---

### 🔵 Week 5–6: Write the YC Application (Days 29–45)

**Key application questions and how to answer them:**

**"What are you building?"**
> Smriti is the Company Brain for engineering teams. We index your Slack, Google Drive, and documents, then let anyone ask questions and get cited answers in under 10 seconds — with zero data leaving your infrastructure. Your new engineers stop asking questions that were already answered. Your senior engineers stop repeating the same context. When someone leaves, their knowledge stays.

**"Why now?"**
> Local LLMs crossed the quality threshold in 2024 (Ollama + nomic-embed-text beats OpenAI ada-002 on retrieval benchmarks). For the first time, a fully private, on-premise Company Brain is possible without enterprise contracts or data exposure. The constraint was never model capability — it was always the knowledge layer.

**"What is your traction?"**
> [Fill in with your paying customer / pilot / early access sign-ups]. We benchmarked against Microsoft's EnterpriseRAG-Bench and achieved 92.4% hit rate on Slack questions at 116ms p50 retrieval latency.

**"Why you?"**
> [Your personal story here — why does Gowtham care about this? Personal experience of losing tribal knowledge? Watching a team's institutional memory walk out the door?]

**"How do you make money?"**
> $199/month per team (up to 20 users), $499/month for larger teams. Enterprise: self-hosted deployment license + support contract. At 100 teams: $240k ARR. Target: 1,000 teams in 18 months = $2.4M ARR.

---

## Part 4: Honest Assessment for YC W27

| Criterion | Score | Notes |
|-----------|-------|-------|
| **Team** | ?/5 | Unknown — solo founder is a flag. Co-founder search advised. |
| **Idea / Market** | 4/5 | Directly on YC's RFS. Company Brain is explicitly what Tom Blomfield wants to fund. |
| **Product** | 3/5 | Strong technical foundations. Missing team model, connectors, agent layer. |
| **Traction** | 1/5 | No paying customers yet. This is the biggest gap. |
| **Pitch Clarity** | 3/5 | README is strong. Application narrative needs sharpening. |

**Realistic probability of acceptance with current state:** ~10–15%  
**Realistic probability with 45-day roadmap completed:** ~35–45%  
**The one thing that would move the needle most:** One paying customer who tells YC "I can't do my job as well without this."

---

## Appendix: 45-Day Sprint Calendar

| Days | Priority | Task |
|------|----------|------|
| 1–3 | 🔴 | Customer outreach — 10 targeted DMs |
| 1–5 | 🔴 | Google Drive OR Slack webhook connector |
| 4–7 | 🔴 | First customer demo call |
| 8–14 | 🔴 | Close first paying customer ($50–200/month) |
| 15–18 | 🟡 | Org/workspace team model |
| 19–21 | ✅ | Upgrade generation model & implement Answer Quality Improvements (Query detection, fallback contact) |
| 22–25 | 🟡 | 60-second YC demo video |
| 22–28 | 🟡 | Expert routing surfaced in UI |
| 26–28 | 🟡 | Landing page — email capture + social proof |
| 29–35 | 🔵 | Write YC application (all questions) |
| 36–40 | 🔵 | Peer review application with 3 founders |
| 41–44 | 🔵 | Finalize video, submit application |
| 45 | 🔵 | Submit 🚀 |

---

*Document prepared: June 9, 2026. Based on YC's published Request for Startups (Summer 2026) and Tom Blomfield's "Company Brain" vision.*
