---
title: RBI Circular Summary — Dec 2024 (Data Localization for NBFCs)
category: compliance
owner: Compliance Team
last_updated: 2026-01-15
status: summary
original_circular: RBI/2024-25/123 — refer to RBI website for original text
---

# RBI Circular Summary — December 2024

**This is an internal summary.** For the original circular text, refer to the RBI website (rbi.org.in) or the FIU-IND portal. We do not have the full original text indexed in this knowledge base.

## What the circular says (in plain language)

The Reserve Bank of India issued a circular in December 2024 clarifying the data localization requirements for NBFCs operating digital lending businesses. The key points are:

### 1. All customer data must be stored on servers located in India

NBFCs and their digital lending partners must ensure that **all customer data** — including KYC documents, transaction records, credit decisions, and communication logs — is stored on servers physically located in India. Cross-border transfer of customer data is permitted only for the following purposes:

- Required by an Indian court or regulator (e.g. an FIU-IND order).
- Necessary for the processing of a cross-border payment, with adequate safeguards.
- Explicitly consented to by the customer in writing, for a specific and time-bounded purpose.

### 2. Audit logs must be retained for the lifetime of the customer relationship plus 5 years

Every system that processes customer data must maintain a tamper-evident audit log. The audit log must record:

- Who accessed the data (employee ID, name, role).
- When they accessed it (timestamp with millisecond precision).
- What they accessed (specific record IDs).
- What they did with it (view, modify, export, delete).
- The reason for the access (a free-text justification, mandatory).

### 3. The board of the NBFC is responsible for compliance

The board of the NBFC must designate a Data Compliance Officer (DCO) at the board level. The DCO is responsible for:

- Quarterly review of the data localization compliance.
- Reporting any non-compliance to the RBI within 7 days of detection.
- Maintaining the register of all cross-border data transfers (which must be reported to the RBI annually).

### 4. Penalties for non-compliance

Non-compliance with the data localization requirement can result in:

- Monetary penalties up to ₹5 crore per instance.
- Suspension of the NBFC's digital lending licence.
- Criminal prosecution of the DCO in cases of wilful non-compliance.

## What this means for Vibe Fintech

We are already compliant on the data localization front — all our customer data is stored in `ap-south-1` (Mumbai region) on AWS. We are not in the process of any cross-border data transfers.

We need to:

1. Formalize the DCO role. Recommendation: assign to Priya Menon (current Head of Compliance). Board approval required.
2. Audit the audit-logging infrastructure to ensure tamper-evident storage. Engineering task: investigate AWS QLDB or a similar immutable ledger.
3. Document the cross-border data transfer policy in the employee handbook.

## Action items (from the last compliance review)

- [x] Confirm all production data is in `ap-south-1`.
- [ ] Confirm the audit log is tamper-evident. **Owner: Engineering, Due: 2026-Q3.**
- [ ] Formalize the DCO role at the next board meeting. **Owner: Priya Menon, Due: 2026-08.**
- [ ] Update the employee handbook with the cross-border data transfer policy. **Owner: People Ops, Due: 2026-Q4.**
