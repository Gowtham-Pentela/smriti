# Smriti — 45-Day YC Sprint Checklist
> **Start date:** June 9, 2026 · **Submission target:** July 24, 2026
> **Goal:** Submit a compelling YC Summer 2026 application with at least 1 paying customer and a working demo.
> Based on Gemini's gap analysis against Tom Blomfield's "Company Brain" RFS.

---

## Legend
- `[x]` Done
- `[ ]` Not started
- `[/]` In progress

---

## 🏁 Milestone 0 — Foundation (Completed before sprint start)

> Everything built before June 9, 2026

- [x] FastAPI backend running on smriti.one
- [x] Supabase authentication (Google sign-in)
- [x] Per-user data isolation (tenant_id = Supabase user UUID)
- [x] Ollama local inference — nomic-embed-text + tinyllama (100% offline)
- [x] pgvector + BM25 hybrid search
- [x] File upload + PDF/DOCX/TXT/MD parsing
- [x] Manual document indexing (chunk + embed + store)
- [x] Slack OAuth 2.0 connector (ingest channels + DMs)
- [x] PII scrubbing on all ingested content
- [x] Deduplication via `ingestion_hashes` table (crash-safe)
- [x] Background sync scheduler (every 30 min)
- [x] Citation grounding (answers reference source files)
- [x] Landing page at smriti.one
- [x] Privacy policy page

---

## 📅 Week 1–2 — Traction Wedge (June 9–22)
**Goal: First paying customer. One person with a credit card.**

### Engineering
- [x] Google Drive OAuth 2.0 connector
  - [x] `gdrive_oauth.py` — CSRF state tokens, encrypted token storage, auto-refresh
  - [x] `gdrive_connector.py` — async Drive walker, Docs/Sheets/Slides/PDF/DOCX
  - [x] `/gdrive/oauth/start`, `/callback`, `/ingest-gdrive`, `/gdrive/status`, `/gdrive/disconnect` routes
  - [x] Drive connector card in the UI sidebar
  - [x] Auto-trigger indexing on connect (no manual Sync click needed)
  - [x] Live progress polling (`⏳ Indexing Drive... 42 chunks so far`)
  - [x] `🔄 Sync Now` button for re-syncing
- [x] Fix OAuth flow: use `authFetch` + JS redirect instead of `<a href>` (Bearer token fix)
- [x] `load_dotenv()` in `main.py` — handles `KEY = VALUE` spacing in `.env`
- [x] Google Drive env vars documented in `.env.example`

### Business / Traction
- [x] Send 10 outreach messages (10+ sent) (use [outreach_templates.md](outreach_templates.md))
  - [x] 3× LinkedIn DMs to Engineering Managers (Sent to Gokul @ Asha Health, Shaun @ Agent Integrator)
  - [x] 3× cold emails to startup CTOs (Sent to Shipra @ Corelayer, Fabio @ Tesorio, Chidi @ Rulebase, Ryan Chow @ Metalware, Lorenz N. @ Finto, David Sawyer @ TSOLife)
  - [x] 2× Twitter/X DMs to founders (Sent: Dereck @ Glass Health)
  - [x] 2× new cold emails/DMs (Sent to Pete Huang @ Healthtech-1, Santiago Pezzoni @ digi)
- [x] Post "Show HN" on Hacker News
- [ ] Book at least 1 demo call
- [ ] Follow up on all non-replies (Day 5)

### 🎯 Week 1–2 Milestone
> **1 booked demo call by June 22**

---

## 📅 Week 3–4 — Demo Polish + Second Connector (June 23 – July 6)
**Goal: Convert demo to paying customer. Add Confluence connector.**

### Engineering
- [x] **Confluence connector** (most requested by enterprise teams)
  - [x] Confluence OAuth (API token or OAuth 2.0)
  - [x] Page/Space walker → chunk + embed
  - [x] Confluence card in UI sidebar
- [x] **Multi-turn conversation memory**
  - [x] Store last 5 Q&A pairs per session (capped at last 6 messages to stay within local LLM context limits)
  - [x] Inject prior context into prompt (sent via Ollama chat history messages)
  - [x] "Follow-up question" UX in chat (added clear chat button in header)
- [x] **Better citation display**
  - [x] Clickable source links (open original file/URL) (Google Drive links open docs directly, Slack links redirect to channel)
  - [x] Source confidence score shown per citation (relevance percentage shown in canvas accordion cards)
  - [x] "View source chunk" expandable panel (collapsible canvas accordion card panels)
- [x] **Answer quality improvements**
  - [x] If no relevant chunks found → say "I don't know" cleanly (with dynamic admin email contact fallback, similarity thresholding (< 0.51), and robust quote normalization)
  - [x] Detect question type (factual vs. exploratory) and tune prompt/temperature parameters
- [x] **Org-level workspace** (transition from per-user to per-org isolation)
  - [x] Add `org_id` concept to DB schema
  - [x] Invite team member flow (email invite → shared tenant)
  - [x] Admin can see all members' indexed sources

### Business / Traction
- [ ] Run first demo call — show indexing + Q&A on prospect's own docs
- [ ] Ask for credit card on call: "Design partner: $100/month, I'll set it up with you"
- [ ] Send 10 more outreach messages (second wave)
- [ ] Collect written feedback from anyone who tries the demo

### 🎯 Week 3–4 Milestone
> **1 paying customer at $50–200/month by July 6**

---

## 📅 Week 5–6 — YC Application Writing (July 7–20)
**Goal: Submit a strong YC application. Record demo video.**

### Engineering
- [ ] **60-second demo video** (most important deliverable for YC)
  - [ ] Script: Problem → Demo → Traction → Ask
  - [ ] Show: sign in → connect Drive → ask a question → get cited answer
  - [ ] Upload to YouTube (unlisted) or Loom
- [ ] **Usage analytics** (YC wants to see engagement metrics)
  - [ ] Log query count per tenant per day
  - [ ] Log chunks indexed per source
  - [ ] Simple `/admin/stats` endpoint
- [ ] **Reliability fixes**
  - [ ] Graceful error messages when Ollama is slow/down
  - [ ] Retry logic on embed failures
  - [ ] Health check page visible to user

### YC Application
- [ ] **Company description** (50 words max)
  - Template: "Smriti is a private AI that indexes a company's Slack, Drive, and docs, then answers questions with citations — like a senior engineer who has read everything, but running 100% on your own machine."
- [ ] **What does your company do?** (1 sentence)
- [ ] **Problem** — describe the pain (knowledge loss, onboarding friction, tribal knowledge)
- [ ] **Solution** — what you built and why it's different (100% offline, no OpenAI)
- [ ] **Traction** — paying customers, users, chunks indexed, demo calls
- [ ] **Why you?** — Gowtham's background, why this problem, why now
- [ ] **Market size** — B2B SaaS knowledge management ($X billion)
- [ ] **Business model** — $100–500/month per team, self-hosted option
- [ ] **Competitors** — Notion AI, Guru, Confluence, Glean → differentiate on privacy
- [ ] **Equity split** — fill in founder details
- [ ] **Incorporation** — confirm legal entity status

### 🎯 Week 5–6 Milestone
> **YC application draft complete + demo video recorded by July 20**

---

## 📅 Week 7 — Review + Submit (July 21–24)
**Goal: Submit YC application. Have someone read it.**

- [ ] Get a trusted person (founder/YC alum) to read the application
- [ ] Incorporate feedback
- [ ] Final review of demo video — is it under 60 seconds?
- [ ] Confirm traction metrics are accurate and up to date
- [ ] **Submit YC application** by July 24, 2026
- [ ] Apply to other accelerators as backup (Pioneer, Antler, Techstars)

### 🎯 Final Milestone
> **YC application submitted by July 24, 2026** 🚀

---

## 📊 KPIs to Track Weekly

| Metric | Now | Week 2 Target | Week 4 Target | Week 7 Target |
|--------|-----|--------------|--------------|--------------|
| Outreach messages sent | 0 | 10 | 25 | 40 |
| Demo calls booked | 0 | 1 | 3 | 5 |
| Paying customers | 0 | 0 | 1 | 2–3 |
| MRR | $0 | $0 | $100 | $300 |
| Active users (weekly) | 1 | 2 | 5 | 10 |
| Chunks indexed (total) | — | — | 10k+ | 50k+ |
| Data sources connected | 2 (Drive, Files) | 2 | 3 (+ Confluence) | 3+ |

---

## 🔧 Known Technical Debt (don't block on these, fix opportunistically)

- [ ] Google Drive API verification (submit to Google for non-test users)
- [x] Add `python-dotenv` to `requirements.txt` (currently installed but not pinned)
- [ ] Email connector (Gmail / Outlook)
- [x] Meeting transcript ingestion (Zoom / Google Meet)
- [ ] Mobile-responsive UI tweaks
- [ ] Rate limiting on `/query` endpoint
- [ ] Proper logging/monitoring (Sentry or similar)

---

## 💡 YC Pitch Framing (Gemini's recommendation)

> **The one-liner:** "Smriti is a private Company Brain — it indexes your Slack, Drive, and docs and answers questions like a senior engineer who's read everything, running 100% on your own machine."

**Key differentiators to emphasize in application:**
1. **100% offline** — no data to OpenAI, Google, or any cloud API
2. **Self-hosted moat** — enterprise security teams will demand this
3. **Aligned with Tom Blomfield's RFS** — directly targets the "Company Brain" thesis
4. **Working product** — not a prototype, it's live at smriti.one

---

*Last updated: June 9, 2026 — Update this file as tasks are completed.*
