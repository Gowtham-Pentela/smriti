---
title: Integration Partners
category: products
owner: Rajesh Kumar, Head of Engineering
last_updated: 2026-06-30
status: active
---

# Integration Partners — Kiosk Underwriting

Vibe Fintech integrates with the following external systems. Every integration is reviewed by the security team before going to production, and every data flow is logged.

## Bureau aggregators

### CIBIL (TransUnion CIBIL Limited)
- **Purpose:** primary credit bureau for CIBIL score, tradeline data, enquiry history.
- **Data flow:** server-to-server, TLS 1.3, mutual auth.
- **Data residency:** processed and stored in India. CIBIL does not transfer customer data outside India.
- **PII handled:** yes — name, PAN, Aadhaar reference, address, employment.
- **Fallback:** Experian and CRIF High Mark (we pull from all three and pick the highest score for the auto-approval decision).

### Experian India
- **Purpose:** secondary credit bureau, used for Experian score and the cross-bureau fraud check.
- **Data flow:** server-to-server, TLS 1.3.
- **Data residency:** processed and stored in India.
- **PII handled:** yes — same as CIBIL.

### CRIF High Mark
- **Purpose:** tertiary bureau, used for the small-business segment (not in kiosk scope today, but integrated for future).
- **Data flow:** server-to-server, TLS 1.3.
- **Data residency:** processed and stored in India.

## Payment partners

### Razorpay (domestic)
- **Purpose:** disbursal of approved loans (NEFT/IMPS) and collection of repayments.
- **Data flow:** server-to-server, Razorpay webhooks for repayment confirmations.
- **PCI scope:** Razorpay is our PCI-DSS Level 1 service provider. We do not handle raw card data — all card capture is on Razorpay's hosted page.
- **PII handled:** name, account number, IFSC, loan ID.

### Adyen (international)
- **Purpose:** disbursal of loans in INR to NRE accounts (used for NRI customers).
- **Data flow:** server-to-server, TLS 1.3.
- **PCI scope:** Adyen is PCI-DSS Level 1.

## KYC and identity

### UIDAI (Aadhaar)
- **Purpose:** Aadhaar e-KYC (XML) and Aadhaar e-Sign.
- **Data flow:** server-to-server via the UIDAI sandbox in production.
- **PII handled:** Aadhaar number (encrypted at rest), demographic data, biometric (for e-Sign only).
- **Compliance:** AUA/KUA license held. Audit log retained for 10 years per UIDAI regulations.

### Setu (e-Sign and e-KYC orchestration)
- **Purpose:** orchestrates the Aadhaar e-Sign flow, returns the signed PDF.
- **Data flow:** server-to-server.

## Sanctions and PEP screening

### Refinitiv World-Check
- **Purpose:** PEP and sanctions screening during onboarding and quarterly re-screening.
- **Data flow:** server-to-server, returns a screening result (clear / potential match / confirmed match).
- **PII handled:** name, date of birth, country.

## Cloud infrastructure

### AWS (ap-south-1, Mumbai)
- **Purpose:** all production compute, storage, and database.
- **Services used:** ECS Fargate, RDS Postgres, S3, DynamoDB, CloudWatch, KMS, Secrets Manager.
- **Data residency:** 100% in `ap-south-1`. No cross-region replication outside India.
- **Compliance:** SOC 2 Type II report received annually.

## Partner risk classification

Every integration is classified by the security team as **Critical**, **High**, or **Standard**:

- **Critical:** integrations that handle raw PII or that have admin access to production (UIDAI, CIBIL, AWS).
- **High:** integrations that handle tokenized data or have limited admin access (Razorpay, Adyen, Setu).
- **Standard:** integrations that only return public data (Refinitiv for non-PEP lookups).

Critical integrations are reviewed quarterly. High and Standard integrations are reviewed annually.
