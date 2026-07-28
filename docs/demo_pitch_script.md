# Smriti — Demo Recording Pitch Script (US + India investor)

Match what you say to what each slide actually shows. Slide numbers below match the new 12-slide deck (`smriti-deck.pptx`).

Total runtime: ~6 min. Slides 1–3 + 12 are quick (15–30 s each). Slides 6, 8, 9 carry the weight (60–90 s each).

---

## Slide 1 — Title (15 s)

**On screen:** the empty Smriti wordmark, dark theme, "Why now" elevator strip at the bottom.

**Voice:**
> "This is Smriti. It's the internal knowledge agent we built for
> regulated enterprises — banks, NBFCs, insurers — where a
> hallucinated regulatory citation isn't a bug, it's a lawsuit.
>
> Compliance teams can't ship copilots that hallucinate. We built
> one that doesn't.
>
> Pre-seed, Bengaluru, raising $2M. Six minutes, twelve slides."

**Action:** cut to Slide 11 (live demo) to open with the strongest moment, then come back to Slide 2 if you want the full arc. Or go in order.

---

## Slide 2 — What is Smriti (30 s)

**On screen:** four plain-English paragraphs in a hero panel + "What it is not" strip.

**Voice:**
> "What is Smriti? Three sentences.
>
> It's an AI search box for everything your company has ever written,
> screenshotted, or recorded. An employee types a question in plain
> English and gets one cited answer. And if the answer isn't in the
> company's own documents — Smriti says so. It does not make one up.
>
> It runs on the customer's own servers, in their VPC, behind their
> firewall. No data leaves. No SaaS, no third-party API.
>
> It's not ChatGPT for work. Not a Copilot wrapper. Not a vector DB
> with a UI. It's a grounded, auditable, on-prem knowledge agent."

**Land the moat:**
> "Think about what we just said. Every company is now dependent on
> AI. The question is not whether to depend on AI — it's whose AI
> to depend on. Depend on someone else's server, and you pay their
> prices, you're at their mercy when they go down, and your data
> leaves your perimeter. Depend on your own server, and the AI
> works for you, the data stays with you, the cost is fixed.
> That's the bet. Smriti is the on-prem version of the same AI
> every company is already rushing to adopt.
>
> That last point is the one that wins deals. The product says no.
> Most RAG products demo impressive answers on questions they
> shouldn't be able to answer. We demo impressive refusals."

---

## Slide 3 — Why we built it (45 s)

**On screen:** left = founder paragraph, right = "What this means" bullets.

**Voice:**
> "Before Smriti, I spent four years inside regulated finance —
> building Kiosk Underwriting, a single-tenant on-prem AI workflow
> for NBFC loan officers in India.
>
> The product worked. But every sales call ended with the same
> question: 'Can your AI read the RBI circular, the audit memo, the
> underwriting SOP, and the narrated compliance video — and give me
> one cited answer?'
>
> Off-the-shelf copilots couldn't. Open-source RAG couldn't. So we
> built Smriti — the agent we needed and couldn't buy.
>
> Today it powers compliance, ops, and underwriting workflows at one
> NBFC, one insurer, and one captive BPO. We have shipped this in
> production — in the exact verticals we are selling to."

**Right column voiceover (read as you move to the bullets):**
> "Why does that matter to you? Four things. I have lived the buyer's
> pain. We are selling into our own verticals — NBFC, insurance,
> captive BPO. The product is single-tenant on-prem from day one —
> not a retrofit. And we have a US foothold: a captive BPO pilot
> conversation is underway. Not yet an LOI, but a real design
> conversation."

---

## Slide 4 — The problem (30 s)

**On screen:** three cards.

**Voice:**
> "Every regulated enterprise has the same problem, regardless of
> geography.
>
> Knowledge is everywhere — RBI memos in drives, KYC flow in
> Confluence, the org chart in a PNG, the compliance briefing in a
> video. None of it is searchable together.
>
> Employees can't find it. Forty percent of an analyst's week is
> spent looking for the right doc. A regulated answer needs a
> citation, not a vibe.
>
> And generic copilots can't ship. Off-the-shelf LLMs hallucinate
> regulatory citations. Compliance teams block the rollout. AI
> budgets stay unspent."

---

## Slide 5 — The product (45 s)

**On screen:** bullets on the left, dashboard screenshot on the right.

**Voice:**
> "Smriti is one agent that reads everything the company knows — and
> only answers from it.
>
> It indexes markdown, images, and video into one vector store. It
> does hybrid retrieval: dense vectors plus full-text. It uses a
> ReAct agent to reason across the corpus. And it runs a grounding
> check on every answer — every number, date, and name has to
> appear verbatim in the retrieved source.
>
> If the answer isn't in the corpus, it refuses. And every fact
> comes back with a citation, not a vibe.
>
> The dashboard you see on the right? It's a real screenshot from
> the agent's index. We didn't fake it for the deck."

---

## Slide 6 — Why now: US + India (60 s)

**On screen:** two wide cards, US on the left, India on the right.

**Voice (US column):**
> "The window is open — on both sides of the ocean. Start with the
> US.
>
> The OCC, FRB, and FDIC are now invoking SR 11-7 model risk
> management on third-party AI. A bank that buys Copilot and can't
> produce a model card, an audit trail, or a refusal log is sitting
> on a Matter Requiring Attention.
>
> The SEC's Reg S-P and the 2023 cybersecurity disclosure rules put
> the CISO on the hook for AI data flows. Copilots that call home
> fail this on day one. Smriti is single-tenant, no egress.
>
> The NIST AI RMF and the Generative AI Profile (NIST AI 600-1) are
> the de facto standard for federal procurement and the largest US
> banks. Smriti maps directly to the VALIDATE & VERIFY function.
>
> And at the state level: Colorado's AI Act, effective February
> 2026, is the first US law with an explicit anti-discrimination
> impact assessment for high-risk AI in financial services. The
> NAIC's Model Bulletin on AI has been adopted by twenty-plus
> state insurance regulators."

**Voice (India column):**
> "India is the same story, different regulators.
>
> The RBI's 2023 FREE-AI committee recommendations and the April
> 2024 digital lending circular put AI governance on the supervisory
> agenda. NBFCs are now expected to maintain an inventory of AI use
> cases and a documented review process.
>
> The RBI's December 2024 circular on AI/ML in financial services
> requires explainability, auditability, and fairness for any
> AI-assisted decisioning on credit, KYC, or customer service. A
> hallucinated answer fails this on its face.
>
> SEBI's 2024 consultation paper on AI/ML in markets has pushed
> broker-dealers, mutual funds, and RTAs to formalize AI governance.
> And IRDAI's 2024 guidelines on AI by insurers require documented
> model governance, periodic validation, and a human-in-the-loop.
>
> Both markets. Both pushing in the same direction. The buyer
> doesn't have a choice anymore — they need a tool compliance will
> sign off on."

**Land the timing:**
> "Both regulators started moving in 2023. The deadlines are 2025
> and 2026. We're shipping in that window."

---

## Slide 7 — How it works (45 s)

**On screen:** architecture PNG — sources → ingest → vector store → ReAct agent → grounded response.

**Voice:**
> "Five steps. Sources on the left — markdown, PNG screenshots, MP4
> videos. The ingest pipeline runs parser, vision-LLM, and whisper
> for transcription. Everything lands in Postgres with pgvector and
> tsvector.
>
> Retrieval is hybrid: dense vectors plus full-text. The agent is a
> ReAct loop on Qwen 2.5, with five tools — search, read, list,
> compare, summarize.
>
> And the last box — grounding — is the moat. Every claim in the
> final answer has to appear verbatim in the retrieved context.
> Article-stripping, tail-2-word matching, sidecar-priority when
> vision and markdown disagree. If a claim fails, the agent retries
> once. If it still fails, the answer is replaced with a strict
> refusal. No hedge. No 'approximately'. No 'I think'."

---

## Slide 8 — The moat (60 s)

**On screen:** four pillars on the left, two Q&A blocks on the right.

**Voice (left side, while you point at the bullets):**
> "Four things that don't exist in a wrapper.
>
> Claim-level grounding — every number, date, and proper noun is
> verified against the source. Not 'approximately'. Verbatim.
>
> Sidecar-priority — when moondream captions an image but a
> markdown sidecar exists for the same file, markdown wins. We
> don't trust the vision model to overrule a human-written
> description.
>
> Strict refusal — no answer beats a wrong answer in a regulated
> workflow. This is the design center.
>
> Multimodal-native — markdown, screenshots, and narrated video,
> one retriever. Not a bolt-on."

**Voice (right side, when pointing at the demo blocks):**
> "And here's what it looks like in practice. Two questions.
>
> 'What was our Q2 MRR figure in crores?' Four point eight crore.
> Cited to the dashboard. The agent didn't make up a year. It cited
> the source.
>
> 'What is the revenue of Tesla?' Read the answer out loud:
> 'I don't have that information from the indexed documents. No
> relevant content was found that supports an answer.' It refused.
> No eighty-billion-dollar hallucination. No 'approximately'. No
> source citation to a non-existent file."

**Land the moat:**
> "We are the RAG vendor that says no. Every other vendor demos
> impressive answers on questions they shouldn't be able to answer.
> We demo impressive refusals on questions we shouldn't.
>
> For a regulated bank, the cost of a hallucinated regulatory
> citation is a fine. The cost of a refused question is zero. That's
> the bet."

---

## Slide 9 — Live demo (45 s)

**On screen:** three-step trace of one real query.

**Voice:**
> "Let me show you one end-to-end. A real query, twenty seconds,
> fully grounded.
>
> The question is: 'What is the current status of the Pine Labs
> webhook incident?'
>
> Step one — the agent gets the question over a single REST call.
> Step two — it does a hybrid retrieval against the
> incident-status-board. The top hit comes back at cosine 0.91.
> Step three — it reads the chunk verbatim and produces the answer.
>
> The answer is: 'The Pine Labs webhook outage was reported as P1
> OPEN with an ETA of approximately four hours from July 15, 2025.'
> Cited. Verbatim. Grounded."

**Note:** if you recorded the live demo video earlier, this is the slide where you switch to the recording.

---

## Slide 10 — Traction (30 s)

**On screen:** three stat panels + three pilot quotes.

**Voice:**
> "Traction, in three numbers. The product is live — multimodal RAG,
> on-device, end to end. Three pilots in flight — one NBFC, one
> insurer, one captive BPO. And time to first answer is under two
> weeks: on-prem or VPC, your data, your model.
>
> I'm not going to read out the customer quotes on this slide — the
> deck has them for the reader, the recording doesn't need them
> spoken. What I'll say is what we are hearing in those three
> conversations: the audit team is the blocker; the security team
> wants on-prem before they will even start the conversation; and
> the buying cycle is short — three weeks to evaluate is fine, six
> months is a no. We are inside that window."

**Note:** the deck shows three placeholder customer quotes for the reader, but the spoken recording does not name them. Until we have signed LOIs, no names, no quotes out loud. The slide stays for the deck reader; the voiceover substitutes the above paragraph.

---

## Slide 11 — Market & business model (30 s)

**On screen:** TAM/SAM/SOM rings on the left, pricing on the right.

**Voice:**
> "Twenty-four billion dollar TAM in global enterprise RAG plus
> compliance AI spend. Four point eight billion dollar SAM in
> regulated finance and insurance across India, the EU, the UK, and
> Southeast Asia. And we are targeting a hundred and twenty million
> dollar SOM by year three.
>
> Pricing is thirty dollars a seat, with a sixty-thousand-dollar
> annual contract minimum. Land with one team — about fifty seats.
> Expand to compliance, legal, ops. Upsell on-prem LLM hosting and
> the audit API. Per-seat SaaS anchored on the audit need."

---

## Slide 12 — The ask (30 s)

**On screen:** use of funds on the left, raise details on the right.

**Voice:**
> "We are raising a two-million-dollar seed round. SAFE, twelve
> million cap.
>
> Where the money goes: nine hundred K on engineering — two senior
> hires for retrieval and infra. Five hundred K on GTM — a founding
> AE and pilot incentives. Three hundred K on compliance — SOC 2
> Type I, ISO 27001 prep. Two hundred K on compute for customer VPC
> and on-prem support. And a hundred K reserve for an eighteen-month
> runway buffer.
>
> Twelve-month milestones: three paid pilots converting to two
> paying customers. SOC 2 Type I. On-prem LLM in production. First
> two hundred and fifty K ARR.
>
> Thank you."

**Action:** freeze on this slide for 2–3 seconds. End.

---

## Recording checklist

- [ ] Dark theme (default — `html[data-theme="dark"]`)
- [ ] Browser zoom at 100% — no DevTools open
- [ ] Notifications off — no Slack popups
- [ ] Microphone test — record 5 s of silence first
- [ ] `python -m demo_data.seed` once before recording, so every Q is answerable
- [ ] `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}` first
- [ ] Record in 1440×900 so the side-pane doesn't crop
- [ ] Have `smriti-deck.pptx` open in a separate window in display-duplicate mode so the recording captures slides + UI together

## Per-slide time budget (for editor)

| Slide | Title | Suggested time | Trim to |
|---|---|---|---|
| 1 | Title | 15 s | 10 s |
| 2 | What is Smriti | 30 s | 20 s |
| 3 | Why we built it | 45 s | 35 s |
| 4 | The problem | 30 s | 20 s |
| 5 | The product | 45 s | 35 s |
| 6 | Why now | 60 s | 50 s |
| 7 | How it works | 45 s | 35 s |
| 8 | The moat | 60 s | 50 s |
| 9 | Live demo | 45 s | 35 s |
| 10 | Traction | 30 s | 20 s |
| 11 | Market & business model | 30 s | 20 s |
| 12 | The ask | 30 s | 20 s |
| | **Total** | **~7 min 45 s** | **~6 min** |

## Two moat lines to land

If the recording runs long and you have to drop slides, the two lines that must survive are:

1. **Slide 8:** "We are the RAG vendor that says no. Every other vendor demos impressive answers on questions they shouldn't be able to answer. We demo impressive refusals on questions we shouldn't."
2. **Slide 6 closing:** "Both regulators started moving in 2023. The deadlines are 2025 and 2026. We're shipping in that window."

Drop everything else before you drop these.
