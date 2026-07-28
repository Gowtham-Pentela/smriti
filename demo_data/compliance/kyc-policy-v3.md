---
title: KYC Policy v3
category: compliance
owner: Priya Menon, Head of Compliance
last_updated: 2026-06-12
effective_from: 2026-07-01
status: active
---

# KYC Policy v3 — Customer Onboarding & Identity Verification

This is the current KYC policy for all customer-facing onboarding flows at Vibe Fintech. It supersedes v2 (2025-08) and aligns with RBI Master Direction DNBR.PD.007/03.10.119/2016-17 (updated 2024).

## 1. Identity verification tiers

We operate three KYC tiers, based on the customer's annual credit exposure with Vibe:

### Tier 1 — OVD (Officially Valid Document) only
- Applies to: customers with annual credit exposure up to ₹20,000.
- Documents accepted: Aadhaar (e-KYC XML or offline XML), PAN card, Passport, Voter ID.
- Aadhaar e-KYC is the default; offline XML is the fallback for areas with poor UIDAI connectivity.
- No physical visit required.

### Tier 2 — V-CIP (Video-based Customer Identification Process)
- Applies to: customers with annual credit exposure between ₹20,001 and ₹2,00,000.
- In addition to Tier 1 OVD, the customer must complete a V-CIP session with a Vibe onboarding agent.
- V-CIP recording is stored for 5 years and linked to the customer ID in our core system.
- A live selfie match against the OVD photo is mandatory.

### Tier 3 — In-person verification
- Applies to: customers with annual credit exposure above ₹2,00,000.
- A Vibe agent must complete a physical visit and collect wet-ink signature on the KYC form.
- Aadhaar e-KYC XML or offline XML is still required in addition to the physical visit.

## 2. Credit score thresholds (kiosk underwriting)

The kiosk underwriting engine uses the following CIBIL score thresholds:

| CIBIL Score | Decision |
|---|---|
| 750 and above | Auto-approve, no manual review. |
| 700 – 749 | Auto-approve, soft manual spot-check on 5% of cases. |
| 650 – 699 | Soft hold, manual review by an underwriter within 24 hours. |
| 500 – 649 | Hard hold, manual review by a senior underwriter, additional income proof required. |
| Below 500 | Auto-decline. Customer is referred to the grievance officer and may re-apply after 90 days. |

The minimum CIBIL score for **auto-approval** is **650**. Scores below 650 always require manual review by an underwriter.

## 3. Politically Exposed Persons (PEPs)

Any customer flagged as a PEP by our screening vendor (Refinitiv World-Check) is routed to the compliance queue. PEP status does not auto-decline, but no credit decision is made until the compliance officer signs off in writing.

## 4. Re-KYC

- Individual customers: re-KYC every 10 years, or earlier if there is a material change in declared income or address.
- Non-individual customers (partnerships, LLPs, companies): re-KYC every 5 years.
- The re-KYC reminder is sent via SMS and email 90, 60, and 30 days before the due date.

## 5. Document retention

- OVD copies (Aadhaar XML, PAN image, V-CIP recording) are retained for 10 years from the date of customer relationship termination, per the PMLA 2002 (as amended in 2015) and RBI KYC Master Direction.
- KYC records are stored in encrypted form at rest, and access is gated by the `kyc-reader` IAM role only.

## 6. Failure modes and escalation

If Aadhaar e-KYC fails three times in a row for the same customer, the customer is automatically offered the V-CIP path. If V-CIP is also declined, the customer is referred to the grievance officer at grievance@vibefintech.in.

If a customer's PAN is flagged as "inoperative" by the Income Tax Department, all credit operations on the customer ID are frozen immediately and an alert is sent to the compliance officer.
