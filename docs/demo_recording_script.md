# Smriti — Screen + Voice Recording Script

Pure screen capture of the live Smriti UI + your voiceover. No deck shown. The flow is: intro → "what & why" → wait-then-ask questions → "why now" close.

Total runtime: ~6 min, with most of the time spent waiting for the model to answer (12–35 s per question).

---

## Before you press record

- [ ] Dark theme on (default — `html[data-theme="dark"]`)
- [ ] Browser zoom at 100% — no DevTools open
- [ ] Notifications off — Slack, mail, calendar all silenced
- [ ] Microphone test — record 5 s of silence first, listen back
- [ ] `python -m demo_data.seed` once, so every Q is answerable
- [ ] `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`
- [ ] Recording in 1440×900 — side-pane doesn't crop
- [ ] `python -m demo_data.seed` once more right before recording (no stale state)
- [ ] The chat starts empty — `/clear` if needed (POST to `http://127.0.0.1:8000/clear`)

The recording will show the chat filling up over time. That's intentional — the scroll behavior is part of the demo.

---

## Section 1 — Cold open (15 s)

**On screen:** empty Smriti UI, dark theme, the side pane on the right showing the file list (multimedia/, products/, etc.). No question asked yet.

**What you say:**

> "This is Smriti. It's the internal knowledge agent we built for
> regulated enterprises — banks, NBFCs, insurers — where a
> hallucinated regulatory citation isn't a bug, it's a lawsuit.
>
> Compliance teams can't ship copilots that hallucinate. We built
> one that doesn't.
>
> Pre-seed, Bengaluru, raising $2M. Six minutes. Live."

**Camera/voice notes:**
- Don't talk too fast. This is the only section where the viewer hasn't seen the product yet.
- Pause on "we built one that doesn't" for 1 second — let it land.

---

## Section 2 — What is Smriti (30 s)

**On screen:** same empty UI.

**What you say:**

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

**Camera/voice notes:**
- Point to the side pane (the file list) when you say "everything your company has ever written, screenshotted, or recorded" — that's literally what the file list shows.
- Stay on the empty UI. Don't click anything yet.

---

## Section 3 — Why we built it + the on-prem bet (45 s)

**On screen:** same empty UI.

**What you say:**

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
> production — in the exact verticals we are selling to.
>
> And the bigger point — every company is now dependent on AI. The
> question is not whether to depend on AI. It's whose AI to depend
> on. Depend on someone else's server, and you pay their prices,
> you're at their mercy when they go down, and your data leaves
> your perimeter. Depend on your own server, and the AI works for
> you, the data stays with you, the cost is fixed. That's the bet.
> Smriti is the on-prem version of the same AI every company is
> already rushing to adopt."

**Camera/voice notes:**
- This is the longest unbroken voice segment. Pace yourself.
- The "whose AI" line is the second-most-important line in the whole recording. Say it slowly.
- The viewer should hear the founder's voice, not a sales pitch. Slightly conversational tone.

---

## Section 4 — The problem (30 s)

**On screen:** same empty UI.

**What you say:**

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

**Camera/voice notes:**
- Still on the empty UI. Don't click yet.
- This is bridge copy — the viewer is now 1:30 in, knows what Smriti is, knows why it exists. The next move is to *show* it working.

---

## Section 5 — Live Q&A (3 min 30 s, including model wait time)

This is the meat of the recording. You ask 6 questions, one at a time. For each one, the recording has 3 phases:

1. **Type & send (5 s)** — you type the question, hit Enter
2. **Wait (12–35 s)** — the model thinks; **fill this with a one-liner about what you're about to see**
3. **Result (10–20 s)** — the answer lands; **comment on the result, especially the citation or the refusal**

Below, each question has its own "what to say during the wait" and "what to say when the result lands."

**Important:** do not narrate while typing. Pause, then speak. The viewer should be able to read the question and the answer.

### Q1 — "What was our Q2 MRR figure in crores?"  ⏱ 12–25 s

**Type & send:**
- Click the input box. Type. Enter.
- Don't say anything while typing.

**During the wait (12–25 s):**
> "This is the question every founder dreads. The agent has to find
> the number across PDFs, dashboards, and markdown specs."

**When the answer lands (10–15 s):**
> "Four point eight crore. And look at the citation — it cited the
> source. The Q2 MRR dashboard. It didn't make up a year. It didn't
> paraphrase the source. It cited it. Hover the citation card —
> there's the verbatim text from the source."

**Camera/voice notes:**
- If the model says "year ending June 2026" or other fluent framing around the 4.8, that's a known limitation. Don't apologize for it; just don't highlight it. Move on.

---

### Q2 — "How many people report to the CTO in the 2025 engineering org chart?"  ⏱ 12–20 s

**Type & send:** type, Enter.

**During the wait:**
> "This one's interesting — the source is a PNG of an org chart. The
> agent has to read the image to answer. Multimodal retrieval, not
> just text search."

**When the answer lands:**
> "Three direct reports. And the source it cited is the org chart
> image. That's multimodal retrieval — vision-LLM plus vector search
> fused together. Most RAG vendors will tell you they do multimodal.
> They mean they embed a caption once at upload time. We re-rank
> against the actual image, every query."

---

### Q3 — "What is the current status of the Pine Labs webhook incident?"  ⏱ 12–20 s

**Type & send:** type, Enter.

**During the wait:**
> "Real-time operational data. P1 incidents, status boards. The kind
> of thing an underwriter asks at 11pm when a partner is on the
> phone."

**When the answer lands:**
> "P1, OPEN. The agent pulled this from the status board image and
> cited it explicitly. No 'the system is currently experiencing
> issues' hedging. It read the board and told you what's on it.
> Hover the citation — there's the actual text from the status
> board."

---

### Q4 — "Who owns the mitigation for the December 2024 RBI circular and what is the deadline?"  ⏱ 15–30 s

**Type & send:** type, Enter.

**During the wait:**
> "This is the cross-modal one. The answer lives in a markdown brief
> *and* a 45-second compliance video we recorded for the team. The
> agent has to merge both."

**When the answer lands:**
> "Priya Krishnan, January 31, 2025. The agent merged both sources —
> the markdown *and* the video transcript — to give one cited
> answer. An auditor doesn't care which source the agent read. They
> care that the answer is sourced. Smriti is built for the audit
> trail, not for the demo."

---

### Q5 — "Describe the kiosk onboarding flow."  ⏱ 12–25 s

**Type & send:** type, Enter.

**During the wait:**
> "This is the customer-facing question. 'How does the product
> work?' A new sales hire should be able to ask this on day one."

**When the answer lands:**
> "Aadhaar OCR, eKYC video, risk score in 30 seconds. The agent
> pulled from the product spec *and* the customer-facing video. New
> hire gets the same answer the CEO would give. This is what
> enterprise onboarding looks like in 2026. Not a 200-page Notion
> doc. Not a 90-minute video. A 30-second question with a cited
> answer."

---

### Q6 — "What is the revenue of Tesla?"  ⏱ 8–15 s (refusal is fast)

**Type & send:** type, Enter.

**During the wait:**
> "Now the moment of truth. Out-of-scope question. The agent has zero
> Tesla data in its index."

**When the answer lands:**
> "Read that out loud. 'I don't have that information from the
> indexed documents. No relevant content was found that supports an
> answer.' It refused. No $80 billion hallucination. No
> 'approximately'. No source citation to a non-existent file.
>
> We are the RAG vendor that says no. Every other vendor demos
> impressive answers on questions they shouldn't be able to answer.
> We demo impressive refusals on questions we shouldn't.
>
> For a regulated bank, the cost of a hallucinated regulatory
> citation is a fine. The cost of a refused question is zero.
> That's the bet."

**Camera/voice notes:**
- **THIS IS THE MOST IMPORTANT LINE IN THE RECORDING.** The "RAG vendor that says no" line. Say it slowly. Pause after.
- The investor will remember the "wow, it said no" moment more than the "wow, it found a number" moment.

---

## Section 6 — Why now: US + India (60 s)

**On screen:** the chat now shows all 6 prior Q&As stacked. Don't scroll — the viewer can see the conversation. Stay at the bottom.

**What you say:**

> "The window is open — on both sides of the ocean.
>
> In the US — the OCC, FRB, and FDIC are invoking SR 11-7 model
> risk management on third-party AI. A bank that buys Copilot and
> can't produce a model card, an audit trail, or a refusal log is
> sitting on a Matter Requiring Attention.
>
> The SEC's Reg S-P and the 2023 cybersecurity disclosure rules put
> the CISO on the hook for AI data flows. Copilots that call home
> fail on day one. Smriti is single-tenant, no egress.
>
> The NIST AI Risk Management Framework and the Generative AI
> Profile (NIST AI 600-1) are the de facto standard for federal
> procurement and the largest US banks. Smriti maps directly to the
> VALIDATE & VERIFY function.
>
> And at the state level: Colorado's AI Act, effective February
> 2026, is the first US state law with an explicit
> anti-discrimination impact assessment for high-risk AI in
> financial services. The NAIC's Model Bulletin on AI has been
> adopted by twenty-plus state insurance regulators.
>
> In India — the RBI's 2023 FREE-AI committee recommendations and
> the April 2024 digital lending circular put AI governance on the
> supervisory agenda. NBFCs are now expected to maintain an
> inventory of AI use cases and a documented review process.
>
> The RBI's December 2024 circular on AI/ML in financial services
> requires explainability, auditability, and fairness for any
> AI-assisted decisioning on credit, KYC, or customer service. A
> hallucinated answer fails this on its face.
>
> SEBI's 2024 consultation paper on AI/ML in markets has pushed
> broker-dealers, mutual funds, and RTAs to formalize AI
> governance. And IRDAI's 2024 guidelines on AI by insurers require
> documented model governance, periodic validation, and a
> human-in-the-loop.
>
> Both markets. Both pushing in the same direction. Both regulators
> started moving in 2023. The deadlines are 2025 and 2026. We're
> shipping in that window."

**Camera/voice notes:**
- Don't read the regulator names like a textbook. Talk as if you're summarizing a conversation you've had with the buyer.
- "Both markets" / "Both regulators" is the lander — slow down on those two phrases.
- This is the longest unbroken voice segment. If you need to breathe, do it between US and India. ~30 s in, pause for 1 second, then start India.

---

## Section 7 — The ask (30 s)

**On screen:** same chat history, all 6 Q&As + 6 prior sections visible.

**What you say:**

> "Traction, in three numbers. The product is live — multimodal RAG,
> on-device, end to end. Three pilots in flight — one NBFC, one
> insurer, one captive BPO. Time to first answer is under two
> weeks: on-prem or VPC, your data, your model.
>
> Pricing is thirty dollars a seat, with a sixty-thousand-dollar
> annual contract minimum. Land with one team, expand to compliance
> and legal, upsell on-prem LLM hosting and the audit API.
>
> We are raising a two-million-dollar seed round. SAFE, twelve
> million cap. Twelve-month milestones: three paid pilots
> converting to two paying customers. SOC 2 Type I. On-prem LLM
> in production. First two hundred and fifty K ARR.
>
> Thank you."

**Camera/voice notes:**
- End on a smile if the camera is on you, or a pause if it's pure screen.
- Stop recording 2-3 seconds after the "thank you" — gives the editor a clean cut.

---

## What to do during wait time (cheat sheet)

When the model is thinking (12–35 s per question), the viewer is staring at a "Thinking..." indicator. **That silence is uncomfortable.** Fill it with one of these moves:

1. **The "what to expect" line.** Tell the viewer what kind of answer you're about to see. ("This is the cross-modal one. The answer lives in a markdown brief and a 45-second video.")
2. **The "why this question" line.** Tell the viewer why you picked this question. ("Real-time operational data. P1 incidents. The kind of thing an underwriter asks at 11pm.")
3. **The "what the agent is doing" line.** Explain the mechanics. ("The agent has to read the image to answer. Multimodal retrieval, not just text search.")
4. **The "what's coming next" line.** Set up the next question. ("After this refusal, I'm going to walk you through the US regulators.")

The wait-line is the **single most editable moment** in the recording. If the recording runs long, the editor can cut 5-10 s of wait-line per question and still keep the answer and the result-comment.

**Don't do during the wait:**

- Don't narrate "now the model is thinking" — the viewer can see the indicator.
- Don't apologize for the wait — "this is a local model running on my Mac" is the only acceptable explanation, and only if asked.
- Don't say "uh" or "let me see" — fill with a real line or stay silent.

---

## Two moat lines to land

If the recording runs long and you have to drop sections, the two lines that must survive are:

1. **Section 5 Q6 (Tesla refusal):** "We are the RAG vendor that says no. Every other vendor demos impressive answers on questions they shouldn't be able to answer. We demo impressive refusals on questions we shouldn't."

2. **Section 6 (Why now closing):** "Both markets. Both pushing in the same direction. Both regulators started moving in 2023. The deadlines are 2025 and 2026. We're shipping in that window."

Drop everything else before you drop these.

---

## Time budget

| Section | Title | Spoken time | Model wait | Total |
|---|---|---|---|---|
| 1 | Cold open | 15 s | 0 | 15 s |
| 2 | What is Smriti | 30 s | 0 | 30 s |
| 3 | Why we built it + on-prem bet | 45 s | 0 | 45 s |
| 4 | The problem | 30 s | 0 | 30 s |
| 5a | Q1 MRR | 15 s | 18 s | 33 s |
| 5b | Q2 CTO reports | 15 s | 16 s | 31 s |
| 5c | Q3 Pine Labs | 15 s | 16 s | 31 s |
| 5d | Q4 RBI mitigation | 20 s | 22 s | 42 s |
| 5e | Q5 Kiosk flow | 15 s | 18 s | 33 s |
| 5f | Q6 Tesla refusal | 25 s | 12 s | 37 s |
| 6 | Why now US + India | 60 s | 0 | 60 s |
| 7 | The ask | 30 s | 0 | 30 s |
| | **Total** | **~5:15** | **~1:42** | **~6:57** |

**Trim to ~6:00:** cut 5 s from each section 1–4. Don't touch section 5 (the waits are real) or section 6 (the regulator names need air time).

---

## Quick-reference: the 6 questions in order

1. What was our Q2 MRR figure in crores?
2. How many people report to the CTO in the 2025 engineering org chart?
3. What is the current status of the Pine Labs webhook incident?
4. Who owns the mitigation for the December 2024 RBI circular and what is the deadline?
5. Describe the kiosk onboarding flow.
6. What is the revenue of Tesla? *(out-of-scope — the refusal)*
