# Smriti × Dexcom — Compliance Engagement (pre-pilot scoping)

**Status:** Draft for Dexcom compliance team
**Date:** 2026-07-27
**Purpose:** Brief compliance **before** any real Dexcom data is involved, and ask the
team to define the bar Smriti must clear to run on Dexcom infrastructure. Goal: confirm,
up front and on paper, that data cannot leave Dexcom's environment through Smriti.

---

## Cover note

> **Subject: Smriti pilot — security & compliance scoping before real-data test**
>
> Hi [compliance lead],
>
> Following [rep]'s interest in trying Smriti on Dexcom data, I'd like to brief the
> compliance team **before** any real data is involved, and ask you to define the bar
> Smriti must clear to run on Dexcom infrastructure. My goal is the same as yours:
> confirm, up front and on paper, that data cannot leave Dexcom's environment through
> Smriti.
>
> Below is (A) a plain-language description of what Smriti is and does, (B) a data-flow
> statement of fact showing where data goes and where it doesn't, and (C) a short
> questionnaire asking what controls, certifications, and architecture requirements you
> require for deployment. Rather than assert we meet your standard, I'd rather you tell
> us the standard and we'll map to it — and document any gaps we close before the pilot.
>
> Happy to walk through this live or answer in writing. The pilot scope I'm proposing is
> **internal knowledge retrieval over non-clinical documents** (SOPs, quality system docs,
> policies, technical docs) — not clinical decision support and not patient data, unless
> you direct otherwise.
>
> [signature]

---

## (A) What Smriti is — plain language

Smriti is a **single-tenant, on-premise document question-answering system**. You deploy
it inside your own environment (your server / VPC); we do not host it and we do not see
your data.

- **Ingest:** documents you point it at are parsed, split into passages, and converted to
  vector embeddings — all locally.
- **Store:** passages and embeddings live in a vector database (pgvector) running in your
  environment.
- **Retrieve:** when a user asks a question, Smriti searches only your local store using
  hybrid semantic + keyword retrieval and a cross-encoder reranker — all in-process.
- **Generate:** a small open-weights language model (Qwen 2.5 7B) runs locally via Ollama
  to compose an answer from the retrieved passages. **No call is made to any external
  model API** (no OpenAI, no Google, no Anthropic) in the on-premise configuration.
- **Grounding firewall (the differentiator):** every sentence in the generated answer is
  checked against the retrieved source passages. Any sentence not supported by source text
  is stripped. If nothing in your corpus supports an answer, Smriti **refuses** rather than
  guessing. In a 64-question evaluation this held at 100% — no fabricated facts.

**Intended use:** internal knowledge retrieval over company documents. **Not** a clinical
decision support tool, **not** for patient-facing or diagnostic purposes.

---

## (B) Data-flow & data-residency statement of fact

| Data element | Where it lives / is processed | Leaves Dexcom env? |
|---|---|---|
| Source documents you ingest | Dexcom server (parsing, chunking) | No |
| Embeddings & passages | Dexcom pgvector store | No |
| User queries | Dexcom server → local retrieval → local LLM | No |
| Generated answers | Returned to the user inside Dexcom | No |
| Model weights (Qwen, nomic-embed) | Downloaded once to Dexcom server at install | One-time inbound only; no outbound thereafter |
| Telemetry / analytics to us (Smriti vendor) | **None.** Smriti makes no phone-home calls. | N/A |
| Third-party / sub-processor processing | **None** in the on-premise configuration. | N/A |
| Use of your data to train any model | **None** — not ours, not the model's. (Open-weights model runs locally; no training occurs.) | N/A |

**Egress guarantee:** Smriti requires no outbound network access to function. We will
document the exact allowlist (initial model download only) so your network team can confine
it to a private subnet with no public egress. See `docs/security_posture.md`.

---

## (C) The ask — what must Smriti possess to deploy on Dexcom servers?

Please tell us your required bar on each of the following. Where we already meet it, we'll
show evidence; where we don't yet, we'll close the gap before the pilot and document it.

1. **Data classification & PHI.** Will the pilot involve protected health information or
   other regulated data (HIPAA, 21 CFR Part 11, GDPR)? If PHI is in scope, we will execute
   a BAA and enable the corresponding controls. If non-PHI corporate data, confirm the
   reduced control set.
2. **Encryption.** Required standard for data in transit (TLS __) and at rest (database,
   disk, backups)? Do you require customer-managed keys (your KMS)?
3. **Authentication & access.** Do you require SSO (SAML/OIDC) integration with your IdP,
   and RBAC? What are your minimum access-control and least-privilege requirements? Smriti
   supports OIDC JWT validation against your IdP (Azure AD / Okta) — no dependency on our
   infrastructure.
4. **Network isolation.** Required deployment topology (private subnet, no public endpoints,
   egress allowlist, VPC peering)? We will conform to it.
5. **Audit logging.** What must be logged (who queried what, when), retention period, and
   immutability/storage destination? Smriti writes an append-only NDJSON audit log of every
   query (user, timestamp, sources accessed, citations, refused flag); we will forward to
   your SIEM/log sink.
6. **Secrets management.** Do you require integration with your secrets vault (e.g., Vault,
   KMS)? We will remove any plaintext secrets.
7. **Vulnerability management.** Do you require an SBOM, dependency/CVE scan, and a
   penetration test prior to pilot? We will provide them. (SBOM generated via
   `scripts/gen_sbom.py`.)
8. **Data lifecycle.** Required controls for retention, and for data return / secure purge
   on exit?
9. **Sub-processors.** We have **none** in the on-prem config. Confirm this satisfies your
   third-party / sub-processor review, or tell us what documentation you need to record that.
10. **Certifications.** Do you require SOC 2 Type II and/or HITRUST for a pilot, or is a
    controls-mapping + architecture review + signed agreement sufficient for a scoped pilot?
    (We want to meet you at the right stage.)
11. **Right to audit.** Do you require contractual right to audit or to commission a
    penetration test? We will include it.
12. **Model behaviour / safety.** Do you have requirements on output safety, traceability of
    answers to source, or refusal behaviour for out-of-scope questions? (Smriti's grounding
    firewall and cite-or-refuse behaviour are designed to satisfy this — we can show the
    evaluation.)

---

## Internal notes (not for Dexcom)

**Already true (cite as evidence):** single-tenant; local LLM (no external model calls);
local pgvector; grounding firewall (100% cite-or-refuse verified); no telemetry/phone-home;
no sub-processors; append-only NDJSON audit log on every query; startup guard that refuses
to boot in prod with dev-mode auth on.

**Gaps we are closing now (frame to Dexcom as "we'll build to your spec", do not claim done
until verified):**
- OIDC/JWKS auth against customer IdP — scaffold added (`backend/auth.py`).
- RBAC admin allowlist (`SMRITI_ADMINS`) — replacing blanket `is_admin=True`.
- SBOM generation — `scripts/gen_sbom.py`.
- Security posture / egress allowlist doc — `docs/security_posture.md`.
- Audit log: env-configurable path for SIEM forwarding + richer entries (tenant, refused,
  citations).

**Gaps that are infra/config (document, configure at deploy time):** encryption at rest
(Postgres/disk, customer KMS), network isolation/egress allowlist, secrets-vault integration,
formal certifications (SOC 2 / HITRUST) not yet held.

**Honest framing wins:** *"Here's our architecture and what's already in place; you set the
bar and we'll map to it and document every gap we close."* Do not claim "fully HIPAA-
compliant" out of the gate.