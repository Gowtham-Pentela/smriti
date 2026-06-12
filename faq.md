# Smriti FAQ: Architectural Integrity, Retrieval, and Resource Manual

This FAQ addresses the core questions regarding Smriti's hallucination prevention, chunking/retrieval strategy, noise management, privacy guidelines, and deployment specifications.

---

### 1. How do you make sure the LLM is not hallucinating?

Smriti implements a strict multi-layer **Grounding Firewall** (`backend/grounding.py`) that acts as an active validation filter between the raw LLM output and the user response:

* **Sentence-Level Validation:** The engine splits the LLM response into individual sentences. For every factual claim, it extracts the inline citation token (e.g., `[Citation: source_id, location]`).
* **Source Verification:** The firewall cross-references the cited source directly against the raw text of the document chunks retrieved from the database for that query.
* **Overlap Calculation:** It computes a character-level word overlap (with common stopwords excluded). If the sentence's content has a matching word overlap of **60% or higher** with the verified source chunk, it passes.
* **Hallucination Stripping:** Any sentence that cannot be verified or lacks matching source chunks is dynamically stripped from the response before it reaches the client.
* **Deterministic Fallback:** If all sentences are stripped, or if the initial retrieval returns no chunks above our relevance threshold, the system returns a safe admission of ignorance: *"I don't have that information from the indexed documents, please contact <admin_email>"*.
* **Parameter Tuning:** We set the local LLM (`phi4-mini:latest`) temperature to `0.0` for factual queries, eliminating creative temperature-based sampling.

---

### 2. What is your chunking and retrieval strategy?

* **Chunking Strategy:**
  - **Structured Conversational Ingestion:** For chat logs (e.g., Slack), messages are not chopped randomly. They are grouped dynamically by channel and thread structure to preserve conversation threads, participants, and timelines.
  - **Sentence-Boundary Text Chunking:** For documents (PDF, DOCX, TXT, Confluence pages), we employ a recursive character text splitter. This splits text at logical paragraph and sentence boundaries rather than cutting words in half, maintaining an overlap of ~200 characters to keep context across splits.
* **Retrieval Strategy:**
  - **Hybrid Search Ranking:** We combine two distinct search algorithms:
    1. **Vector Semantic Search:** Dense 768-dimension embeddings generated locally via `nomic-embed-text` and queried using pgvector's cosine distance operator.
    2. **Keyword Full-Text Search:** Postgres standard full-text indices using English dictionaries and `ts_rank` matching.
  - **Hybrid Scoring:** Candidates are ranked using a weighted score: `(0.7 * Cosine Similarity) + (0.3 * Keyword Match Score)`. This ensures that exact matches (such as specific code function names or ticket numbers) surface even if semantic similarity is low.
  - **Deduplication Re-ranking:** Prior to serving, candidate chunks are cross-compared using vector cosine similarity. Chunks with a cross-similarity $> 0.95$ (like duplicate boilerplate disclaimers) are automatically pruned.

---

### 3. As we are giving access to chat data, how do you make sure the model is not capturing noise? And is there any mechanism to not store noise in the data?

We handle noise in two stages: at ingestion time (storage level) and query time (retrieval level).

* **Ingestion-Time Noise Filtering:**
  - **System/Bot Message Scrubbing:** System events (e.g., *"User joined the channel"*, *"Application updated"*), bot messages, empty lines, and conversational filler are automatically filtered out during ingestion.
  - **PII & Credentials Scrubbing:** A pre-ingestion scanner replaces high-noise sensitive items (keys, passwords, tokens) with generic redaction labels.
* **Query-Time Noise Filtering:**
  - **Strict Similarity Scoring:** We enforce a similarity score guard. Any retrieved chunk with a hybrid score below `0.51` is thrown out. If no chunks pass this threshold, the model is not fed any data, avoiding unrelated chat chatter.
  - **Grounding Verification:** The Grounding Firewall ensures that conversational noise (like a colleague saying *"yeah, I think so"* or *"ok sounds good"*) is never treated as a factual database statement.

---

### 4. How are you handling PII/SPI?

Smriti treats Personally Identifiable Information (PII) and Sensitive Personal Information (SPI) as strict security boundaries:

* **Local Pre-Scrubbing:** All incoming data passes through an on-premise PII/SPI scrubber before indexing. We use local regular expressions and lightweight Named Entity Recognition (NER) models to redact:
  - Credentials (API keys, database URLs, auth tokens, passwords).
  - Identifiers (SSNs, credit card numbers, phone numbers).
* **Zero Egress:** Because the parsing, scrubbing, embedding, and storage occur entirely within your local infrastructure (no calls to OpenAI, Microsoft, or Google APIs), your raw text, PII, and SPI never cross the network boundary.

---

### 5. How much resources should we require on-premise to deploy and maintain this model?

Because our stack uses quantized local models, the resource footprint is compact:

* **Minimum Specifications (Local Developer / Sandbox):**
  - **CPU:** 4-core modern CPU (Intel, AMD, or Apple Silicon).
  - **RAM:** **8 GB RAM** minimum. Our active stack fits in ~3.2 GB RAM (3.2 GB for quantized `phi4-mini` + ~300 MB for `nomic-embed-text`), leaving ample room for Postgres/Supabase and the FastAPI backend.
  - **Storage:** 10 GB SSD space.
* **Recommended Specifications (Production Team Deployment):**
  - **CPU:** 8-core CPU.
  - **GPU (optional but highly recommended):** Apple Silicon (M-series) or NVIDIA GPU with at least **8 GB VRAM** to accelerate inference speeds.
  - **RAM:** **16 GB RAM** or more to support parallel query execution.
  - **Storage:** 50+ GB SSD space (depending on the size of your indexed Slack/Drive history).

Maintenance is low: the stack runs containerized (via Docker Compose) and database indexes self-manage.

---

### 6. What are the updates you are planning and how the support system works?

* **Product Roadmap:**
  - **CI/CD Architectural Policy Guard:** Integrations for GitHub/GitLab actions to check pull requests and fail builds if new code contradicts decisions captured by Sutra.
  - **Enterprise Meeting Clients:** Native desktop bot wrappers for Zoom, Microsoft Teams, and Webex.
* **Support System:**
  - **Admin Fallback Routing:** When Smriti cannot resolve a query, it routes the user to their designated internal IT/Workspace admin email.
  - **Local Health Dashboard:** Exposes endpoints like `/health` and `/status` for server health monitoring.
  - **Private Enterprise SLA:** Premium support contracts provide direct developer support, hotfixes, and custom data connector integrations.

---

### 7. How do workspace invitations work, and how does the email notification get sent?

When an administrator invites a user via their email address (e.g. at `/org/invite`), Smriti:
* Generates a unique, secure invitation token stored in the database.
* Constructs a join URL pointing to `https://smriti.one/app/auth.html?invite={invite_id}`.
* **SMTP Dispatch:** If SMTP settings are configured in `.env`, Smriti sends a styled HTML invitation email directly to the recipient's inbox.
* **Manual Fallback:** If SMTP settings are not configured, the invite is still created successfully and recorded as pending. The administrator can copy the link from the UI and send it to the colleague manually.
* When the invitee opens the link and authenticates, Smriti automatically links their new profile to the organization workspace.

---

### 8. What is the IMAP Email Connector, and how does it index our emails?

To safely sync and query team email history without using cloud-hosted Google OAuth APIs or paying for third-party security audits, Smriti supports an **on-premise IMAP connector**:
* **Connection & Security:** Connects to any standard mail server (Gmail, Outlook, local Exchange, postfix) via standard IMAP over TLS/SSL (usually port 993) using credentials configured locally in `.env`.
* **Private Tenant Resolution:** As unread emails are fetched, Smriti determines which workspace they belong to by matching the sender's email or domain against active memberships (`public.user_org_membership`). Unresolved senders default to a private tenant silo, ensuring user isolation.
* **PII & Data Sanitization:** Email bodies are processed through a local regex scrubbing layer to redact credentials, phone numbers, and typical personal identifiers before they are saved.
* **Contextual Chunking:** Emails are split into overlapping character-based chunks. Each chunk is prefixed with the sender's email and the email subject to preserve RAG context.
* **Local Vectors:** Chunks are embedded locally using `nomic-embed-text` and stored in `public.vector_chunks` on your pgvector instance.

---

### 9. How does the Sutra Meeting Bot automatically discover scheduled meetings?

Instead of relying on Google Calendar webhooks or service accounts, the Sutra Meeting Bot uses **ICS calendar invite auto-discovery** via IMAP:
* **The Invite Flow:** When a user schedules a meeting on their calendar (Google Calendar, Microsoft Outlook, Apple Calendar) and adds the bot as an invitee, their calendar system automatically sends a standard email invitation to the bot's inbox.
* **ICS Processing:** The background IMAP worker polls the bot's mailbox for unread messages. If it detects a calendar invite (either a `text/calendar` body type or a `.ics` file attachment), it parses the raw iCalendar data.
* **Meeting Registration:** The parser extracts the event title, start time, attendee emails, and the virtual meeting URL (Google Meet, MS Teams, Zoom, or Webex link) and automatically inserts a new row in `public.meetings` with a `scheduled` status.
* When the scheduled time arrives, the background scheduler fires up the headless Playwright crawler (`sutra_bot.js`) to join the meeting and stream the caption transcript.

