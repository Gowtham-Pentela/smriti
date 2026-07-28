---
title: AML Procedures
category: compliance
owner: Priya Menon, Head of Compliance
last_updated: 2026-05-28
status: active
---

# Anti-Money Laundering (AML) Procedures

Vibe Fintech is a reporting entity under the Prevention of Money Laundering Act (PMLA), 2002. This document describes how we identify, escalate, and report suspicious transactions.

## 1. Monitoring scope

We monitor every customer transaction for AML risk signals. The AML rules engine runs nightly on the previous day's transactions and emits alerts that are triaged by the compliance team.

The engine considers, in order of weight:

1. **Cash transaction thresholds** — any single cash transaction above ₹10 lakh, or aggregate cash transactions above ₹50 lakh in a calendar month, for a single customer.
2. **Velocity** — a customer initiating more than 30 transactions in a 24-hour window, or more than 10 distinct counter-parties in a 24-hour window.
3. **Cross-border indicators** — any transaction involving a counter-party in a FATF grey-list or high-risk jurisdiction (per the latest FATF list published at fatf-gafi.org).
4. **Structuring** — multiple cash transactions just below the ₹10 lakh threshold, within a 72-hour window.
5. **PEP (Politically Exposed Person) involvement** — any transaction where the customer or counter-party is flagged as a PEP in our screening database.

## 2. Alert triage and escalation

Every AML alert is reviewed by a compliance analyst within 24 hours of generation. The analyst has three actions available:

- **Close as false positive** — the alert is logged with a justification and archived. No further action.
- **Escalate to STR** — the alert is forwarded to the Senior Compliance Officer (currently Priya Menon) for review. The Senior Officer has 7 days to decide whether to file a Suspicious Transaction Report (STR) with the Financial Intelligence Unit - India (FIU-IND).
- **Hold transaction** — the transaction is held in pending state and the customer is contacted within 24 hours for clarification. Held transactions must be resolved within 5 business days.

## 3. STR (Suspicious Transaction Report) filing

- STRs are filed electronically via the FIU-IND portal (https://fiuindia.gov.in) using the prescribed XML format.
- An STR is filed irrespective of the transaction amount if the compliance officer has reasonable grounds to suspect that the transaction is connected to proceeds of crime.
- The fact that an STR has been filed is **never** disclosed to the customer. Disclosure of an STR filing is a criminal offence under PMLA Section 12.
- A copy of every STR is retained internally for 10 years.

## 4. Counter-party due diligence

For B2B partnerships (NBFCs, banks, payment aggregators), we perform Enhanced Due Diligence (EDD) on the partner entity before contract signing. EDD includes:

- Beneficial ownership walk up to the ultimate natural person.
- Review of the partner's AML policy and recent FIU-IND filings.
- Sanctions screening against the UN Security Council Consolidated List and OFAC SDN List.
- A signed undertaking from the partner's compliance officer.

## 5. Training

Every Vibe employee handling customer transactions completes an annual AML training module. The training includes:

- PMLA 2002 (as amended) and the rules thereunder.
- Red flags in customer behaviour.
- The "tipping off" prohibition (Section 12).
- Case studies of STRs filed in the previous year (anonymised).

## 6. Record retention

All AML records — alerts, triage notes, STR filings, training completion — are retained for **10 years** from the date of creation, in encrypted form. The retention period aligns with the KYC document retention period (see KYC Policy v3 §5).
