# Smriti — Security Posture & Egress Allowlist

**Audience:** customer security/compliance teams (e.g. Dexcom) reviewing Smriti
for on-premise deployment.
**Date:** 2026-07-27
**Scope:** the on-premise configuration (local LLM, no external model APIs).

This document states what Smriti's runtime touches on the network, how data and
secrets are handled, and which controls are already in place vs. configured at
deploy time. It is the evidence backing the data-residency claims in
`dexcom-compliance-engagement.md`.

---

## 1. Network egress allowlist

Smriti requires **no outbound network access to function** after installation.

| Destination | When | Direction | Required? |
|---|---|---|---|
| Ollama model registry (model download) | One-time, at install / model pull | Outbound | Only if weights not pre-provisioned |
| Customer IdP JWKS endpoint (`SMRITI_OIDC_JWKS_URL`) | Token validation (cached 1h) | Outbound | Only if OIDC SSO enabled |
| Supabase Auth (`SUPABASE_URL`) | Token validation | Outbound | Only if Supabase auth mode used |
| Local Postgres / pgvector | Every request | Internal | Yes (intra-environment) |
| Local Ollama (`OLLAMA_*_URL`, default `127.0.0.1:11434`) | Every request | Internal | Yes (intra-environment) |
| Smriti vendor / telemetry | Never | — | **None. No phone-home.** |
| External LLM APIs (OpenAI / Google / Anthropic) | Never (on-prem config) | — | **None.** |

**Deployment guidance:** place Smriti in a private subnet with no public egress.
Pre-provision model weights and (if air-gapped) pin the IdP JWKS, and the only
remaining traffic is intra-environment (Postgres, Ollama). Egress can be fully
deny-by-default.

---

## 2. Data residency

All customer data — source documents, chunks, embeddings, queries, generated
answers — is processed and stored **inside the customer environment**:

- Documents are parsed, chunked, and embedded on the Smriti host.
- Embeddings and passages live in the customer's pgvector/Postgres instance.
- Retrieval and generation run in-process (local Ollama); no chunk or query
  leaves the environment in the on-prem configuration.
- No sub-processors. No third-party data processing. No model training on
  customer data (the model is open-weights and runs locally; no training occurs).

---

## 3. Authentication & access control

Three modes, selected by environment configuration:

1. **OIDC / SSO (recommended for production on-prem).** Smriti validates Bearer
   JWTs locally against the customer IdP's JWKS (`SMRITI_OIDC_ISSUER`,
   `SMRITI_OIDC_AUDIENCE`, `SMRITI_OIDC_JWKS_URL`). RS256-only; `none`/HS256 are
   rejected (alg-confusion guard). Works with Azure AD, Okta, Keycloak. No
   dependency on Smriti-hosted or Supabase infrastructure.
2. **Supabase Auth.** JWT validated against Supabase (`SUPABASE_URL`,
   `SUPABASE_ANON_KEY`). Suitable when Supabase is already the IdP.
3. **Dev mode** (`SMRITI_DEV_MODE=true`, local/dev envs only). Trusts the
   `X-Dev-User-Email` header from loopback. **Startup guard refuses to boot if
   dev mode is enabled outside a local/dev environment**, so it cannot leak into
   production by accident.

**RBAC:** single-tenant. `SMRITI_ADMINS` (comma-separated email allowlist) = admins;
any other authenticated user is a read-only viewer. If unset, legacy "first user
is admin" behaviour is preserved for local dev. (Per-document ACLs would require
a roles table — not yet implemented.)

Self-check: `SMRITI_DEV_MODE=true SMRITI_ENV=local python -m backend.auth` exercises
JWT round-trip, alg-confusion rejection, expiry, and signature-tamper detection.

---

## 4. Audit logging

Every `/query` and `/agent` call writes an append-only NDJSON record:

```json
{"timestamp": "...", "tenant_id": "...", "user_email": "...",
 "query": "...", "accessed_files": ["..."], "citations": ["..."], "refused": false}
```

- Path is env-configurable (`SMRITI_AUDIT_LOG_PATH`) so it can point at a persistent
  volume or a SIEM-forwarder drop directory.
- Refusals (no chunks retrieved, or grounding firewall stripped everything) are
  logged with `refused: true`.
- Forward to your SIEM via file collection (Filebeat / Fluent Bit / equivalent).

Known minor edge: a refusal that still emits a spurious inline-citation footer
(model quirk) is logged as `refused: false` because the grounding firewall did
not fully strip it. The answer text in the audit record makes the refusal
visible; the boolean is conservative. (Tracked for refinement.)

---

## 5. Encryption

| Layer | Status | Configured by |
|---|---|---|
| In transit (API → Smriti) | TLS terminated at your reverse proxy / load balancer | Customer infra |
| In transit (Smriti → Postgres) | TLS if Postgres enforces it (`DATABASE_URL` with `sslmode=require`) | Customer infra |
| In transit (Smriti → Ollama) | Loopback; TLS not required | N/A (local) |
| At rest (Postgres / pgvector) | Disk + DB encryption | Customer infra (KMS / LUKS / TDE) |
| At rest (audit log) | Filesystem encryption on the host volume | Customer infra |

Smriti itself does not manage keys; it relies on the host/DB layer for
encryption. Customer-managed keys (KMS) are supported at the infrastructure
layer. **Open item for customer:** confirm disk/DB encryption and KMS settings.

---

## 6. Secrets management

- All configuration is via environment variables (`.env`), not hardcoded.
- No plaintext secrets in the repository.
- **Open item for customer:** integrate env injection from your secrets vault
  (HashiCorp Vault, GCP/AWS KMS, etc.) rather than a static `.env` file on disk.

---

## 7. Vulnerability management

- **SBOM:** `python scripts/gen_sbom.py --out docs/sbom.json` produces a
  CycloneDX-style JSON BOM of the installed Python environment (name, version,
  license, purl). 127 components at last generation; 1 UNKNOWN license
  (setuptools — MIT).
- **Open item for customer:** run dependency CVE scanning (e.g. `pip-audit`,
  `osv-scanner`, `safety`) against the SBOM prior to pilot, and a penetration
  test on the deployed instance.

---

## 8. Data lifecycle

- Ingested documents and embeddings persist in pgvector until cleared via the
  `/clear` endpoint (admin) or direct DB action.
- **On exit / offboarding:** data return and secure purge is performed by
  dropping the Smriti schema and deleting the audit log + Ollama models from the
  customer host. No data is held vendor-side (nothing to retrieve from us).
- **Open item for customer:** agree retention period for the audit log and any
  backup-snapshot retention.

---

## 9. Model behaviour / safety

- **Grounding firewall:** every generated sentence is verified against retrieved
  source passages; unsupported sentences are stripped. If nothing supports an
  answer, Smriti refuses. Verified at 100% cite-or-refuse across a 64-question
  evaluation (`scripts/eval_claims.py`).
- **Refusal-when-absent:** queries whose answer is not in the corpus are refused
  rather than fabricated — the core differentiator vs. a raw LLM API.
- Answers carry inline citations traceable to source filename + location.

---

## 10. Control summary — what's in place vs. what's configured at deploy

**In place now:** single-tenant; local LLM (no external model calls); local
pgvector; OIDC/Supabase/dev auth with startup guard; RBAC admin allowlist;
append-only audit log (env-configurable, SIEM-forwardable); no telemetry;
no sub-processors; SBOM generation; grounding firewall.

**Configured by customer at deploy:** TLS termination, Postgres/at-rest
encryption + KMS, network isolation / egress allowlist, secrets-vault injection,
CVE scan + pentest, audit-log retention, certifications (SOC 2 / HITRUST — not
yet held).