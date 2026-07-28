---
title: Data Retention Policy
category: compliance
owner: Priya Menon, Head of Compliance
last_updated: 2026-04-10
status: active
---

# Data Retention Policy

This document defines how long Vibe Fintech retains different categories of customer and operational data, and how the data is destroyed at the end of the retention period.

## Retention periods

| Data category | Retention period | Trigger for retention start | Authority |
|---|---|---|---|
| Customer transaction records (credit, debit, repayments) | 7 years | Date of transaction | PMLA 2002, IT Act 2000 (Section 43A) |
| Customer KYC documents (Aadhaar XML, PAN, V-CIP recording) | 10 years from relationship end | Date of customer relationship termination | PMLA 2002, RBI KYC Master Direction |
| Credit decision records (approve/decline, reasoning, model version) | 7 years | Date of decision | RBI Fair Practices Code |
| Audit logs (system access, data access, admin actions) | 10 years | Date of access | RBI Dec 2024 circular (data localization) |
| AML alerts and STR filings | 10 years | Date of alert | PMLA 2002 |
| Customer service recordings (call center) | 1 year | Date of call | Internal policy |
| Marketing consent records | 7 years from withdrawal | Date of consent withdrawal | DPDP Act 2023 |
| Employee access logs (admin actions) | 10 years | Date of access | RBI Dec 2024 circular |
| Source code (production deployments) | Permanent | Date of deployment | Internal policy |
| Production database backups | 1 year (rolling) | Date of backup | Internal policy |

## Storage tiering

- **Hot tier** (data accessed regularly): AWS RDS Postgres, `ap-south-1`, encrypted at rest with AES-256.
- **Warm tier** (data accessed occasionally, e.g. compliance audits): AWS S3 Standard-IA, `ap-south-1`.
- **Cold tier** (data accessed rarely, e.g. regulatory enquiries older than 3 years): AWS S3 Glacier Deep Archive, `ap-south-1`.

## Right to erasure (DPDP Act 2023)

Customers have the right to request erasure of their personal data under the Digital Personal Data Protection Act, 2023, subject to certain exceptions:

- Data required for ongoing legal proceedings is retained until the proceedings conclude.
- Data required for regulatory compliance (see retention periods above) is retained for the full period.
- Anonymized data is not subject to erasure requests.

When a customer submits an erasure request:

1. The compliance team verifies the customer's identity (V-CIP or in-person).
2. The request is logged in the `data_subject_requests` table.
3. Erasure is completed within 30 days, with an extension of 30 days if the request is complex.
4. The customer is notified in writing when erasure is complete.

## Destruction process

When data reaches the end of its retention period, it is:

- **Database records**: deleted via a scheduled job that runs quarterly. Deletion is logged in the audit log.
- **S3 objects**: deleted via a lifecycle policy. Deletion is logged in S3 server access logs.
- **Backups**: aged out via the 1-year rolling backup policy. Backups are not targeted for individual deletion — they are aged out as a whole.
- **Paper records**: shredded by a certified vendor with a certificate of destruction filed.

## Annual review

The Data Compliance Officer reviews this policy annually and updates the retention periods as required by changes in regulation.
