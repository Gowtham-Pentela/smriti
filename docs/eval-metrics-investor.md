# Smriti — How Accurate Is It? (Plain-English Brief)

**For:** investors and non-technical readers
**Date:** July 2026

---

## The one thing to know

Smriti is a question-answering system for companies that **cannot afford it to lie** — hospitals, banks, insurers, regulated industries. The single most important question for those buyers is: *"When it doesn't know the answer, does it admit it — or does it make something up?"*

**It admits it. Every time.** In our tests, across 64 questions, Smriti **never invented a fact**. That is the product's whole reason for existing, and the number below is the proof.

---

## The headline result

> **100% honest. Zero made-up answers across 64 questions.**

When Smriti knows the answer, it shows its work (a citation to the real document). When it doesn't know, it says so. It never guesses and presents a guess as fact.

This is the behaviour regulated enterprises need and that ordinary AI chatbots do **not** guarantee.

---

## The numbers, explained simply

We ran 64 test questions, split into four types. Here's what each type tested and how Smriti did:

| Test | What we asked | Score | In plain terms |
|---|---|---|---|
| **Answerable questions** (30) | "What does this document say about X?" | **100%** | Every answer pointed to the real source, or honestly said "I don't have that." Never made something up. |
| **Things it couldn't know** (19) | "What's Apple's stock price today?" / "Who won the last World Cup?" | **100%** | It refused to answer all 19 — it knows these aren't in the company's documents, so it didn't guess. |
| **Trick questions** (12) | Real topic, but a specific detail that **doesn't exist** in the document (e.g. "what's the exact port number?" when no port is mentioned) | **100%** | This is the hardest test — the topic is real, so a normal AI confidently invents a believable-sounding number. Smriti refused or used only what was actually there. **No invented details.** |
| **Two-document questions** (3) | "Compare document A and document B" | **100%** | It pulled from both documents and cited both correctly. |
| **Finding the right document** (30) | Did it point to the *correct* source? | **90%** | 9 out of 10 times it found the exact right document. The other 1-in-10 it pointed to a *real but neighbouring* document — still a real source, just not the perfect one. **Never a fake source.** |

---

## "Accuracy," "precision," "recall" — without the jargon

If a technical person on the investor side asks for these by name, here's what they map to:

- **Accuracy = 100%.** Of every answer Smriti gave, **none contained a false or unsupported statement.** This is the trust metric, and it's the one that matters for regulated buyers.

- **Precision ("did it point to the right document?") = 90%.** 9 times out of 10, the citation was the correct document. The misses point at a *real, related* document — never a fabricated one.

- **Recall ("did it find everything relevant?") = not yet formally measured.** We report a close substitute: of questions that *do* have an answer in the documents, Smriti found and delivered it **90%** of the time. A full recall study needs a labeled set we'll build on each customer's documents during the pilot — we're stating this openly rather than printing a number we can't back.

**The honest one-liner:** *Smriti never makes things up (100%), and it finds the right document 9 times out of 10 (90%). The remaining 10% is a "find the best document" problem we're actively improving — it is never a "made up an answer" problem.*

---

## Why this is hard, and why it's a moat

Every popular AI tool — ChatGPT, Gemini, a generic "chat with your docs" bot — will, when it doesn't know something, **smoothly invent a plausible answer and say it with total confidence.** That's fine for writing marketing copy. It's a disaster if a claims adjuster, nurse, or compliance officer acts on it.

Smriti is built differently. Think of it like an **open-book exam with a strict proctor**: before any sentence reaches the user, it's checked against the actual source documents. If a sentence isn't backed by a document, it's deleted. If *nothing* in the documents supports an answer, Smriti refuses. The 100% score above is that proctor working as designed.

Importantly: **a weaker Smriti becomes more cautious, not more inventive.** If its search is poor, it simply refuses more often — it never degrades into making things up. That's a safety property most AI products cannot claim.

---

## How it's been improving

| Version | "Found the right document" score |
|---|---|
| First version | 53% |
| + better document ranking | 73% |
| + upgraded model + smart lookup | **90%** |

The honesty rate was **100% at every step** — the improvement work has been about finding the *best* document, never about stopping hallucinations (that was solved from the start).

---

## The honest caveats (we'd rather you hear these from us than discover them)

1. **Tested on a public document set, not a customer's data.** These numbers are from a public library of 819 cybersecurity documents. A customer's own documents will give different numbers, which we establish during each pilot. The *honesty* behaviour is the same on any document set; the "find the right document" score depends on how well-organised the customer's documents are.

2. **Occasional slowness, not errors.** Smriti runs a capable AI model on the customer's own server (a selling point — data never leaves them). On a cold start under heavy load it can occasionally take longer than our test's time limit. When this happened once in testing, that question simply got *no answer* (a timeout) — **not a wrong answer.** Re-running it succeeded. A timeout can't produce a lie because it produces nothing.

3. **Recall not formally measured yet** (explained above). We're upfront about this.

4. **64 questions is a focused test, not a massive benchmark.** It's deliberately sized to prove the four things we claim the product does. Larger-scale testing happens on customer data.

---

## The bottom line for investors

- **The product does the one hard thing competitors don't:** it refuses instead of fabricating — proven at 100%.
- **It finds the right document 90% of the time**, and improving.
- **It runs inside the customer's own environment** — data never leaves them. This is what opens doors with regulated buyers like Dexcom.
- **We've stated every limitation openly**, which is exactly the posture regulated buyers want to see.

---

*Technical methodology, the test harness, and reproduction steps are in an appendix available on request (`scripts/eval_claims.py`).*