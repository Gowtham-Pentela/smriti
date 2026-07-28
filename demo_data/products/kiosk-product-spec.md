---
title: Kiosk Underwriting Product Specification
category: products
owner: Rajesh Kumar, Head of Engineering
last_updated: 2026-06-20
status: active
---

# Kiosk Underwriting — Product Specification v2.1

## What it is

Kiosk Underwriting is Vibe Fintech's flagship SaaS product. It is an automated credit decisioning platform that runs on Android tablets deployed at retail partner locations (typically large retail chains, two-wheeler dealerships, and consumer electronics stores). The kiosk takes a customer through the loan application flow end-to-end, runs the credit decision in under 90 seconds, and either approves on the spot or routes the application to manual review.

## The customer flow

1. Customer approaches the kiosk and selects "Apply for Loan".
2. The kiosk asks for the loan amount and tenure. The customer enters this on the touchscreen.
3. The kiosk runs an **eligibility pre-check** based on the customer's PAN (the customer enters the PAN, and the kiosk fetches the CIBIL score and the existing Vibe exposure from the API).
4. If the pre-check passes, the customer is asked to complete the **KYC flow** (Aadhaar e-KYC XML or V-CIP, depending on the exposure tier — see KYC Policy v3 §1).
5. After KYC, the customer consents to the bureau pull and a soft enquiry is made against CIBIL, Experian, and CRIF High Mark. The results are combined via the bureau aggregator (CIBIL + Experian + CRIF).
6. The **underwriting model** (see Underwriting Engine v2 spec) takes the bureau data, the KYC data, the requested loan parameters, and Vibe's internal history (if any), and produces a decision in under 2 seconds.
7. The decision is displayed to the customer on the kiosk screen. If approved, the customer signs the loan agreement on the touchscreen (wet-ink equivalent via the Aadhaar e-Sign flow).
8. The disbursal is initiated via NEFT/IMPS to the customer's bank account. Disbursal typically completes within 15 minutes for NEFT and within 5 minutes for IMPS.

## Hardware

- Tablet: Samsung Galaxy Tab A8 (10.5" screen, octa-core, 4 GB RAM, Android 13).
- Card reader: Ingenico iCT250 (for debit card EMV chip + PIN, in case of cash disbursal at the partner location).
- Printer: thermal receipt printer (for the loan agreement and the disbursal receipt).
- Network: 4G LTE primary, Wi-Fi fallback. The kiosk must function for at least 8 hours offline (queueing transactions) if both networks are down.
- Power: 12V DC adapter with a 1-hour UPS backup.

## Software stack

- **Android app**: native Kotlin, MVVM with Hilt, Retrofit for API calls, Room for offline cache.
- **API gateway**: AWS API Gateway in front of ECS Fargate (FastAPI containers, 4 vCPU / 8 GB each, autoscale 4–20).
- **Underwriting engine**: Python 3.12, scikit-learn + XGBoost, served via a dedicated inference service. Model artifacts in S3, loaded on cold start.
- **Database**: RDS Postgres 16 (db.r6g.2xlarge) in `ap-south-1`, with read replicas for analytics.
- **Audit log**: DynamoDB in `ap-south-1` with point-in-time recovery enabled.

## Throughput

- Peak: 3,500 kiosk transactions per hour across the fleet.
- Average decision latency: 1.8 seconds end-to-end (from "submit" to "decision displayed").
- Uptime SLA: 99.5% measured monthly. 99.9% measured annually (excluding planned maintenance).

## Current deployment

- 1,200 kiosks across India as of 2026-06-30.
- 47 retail partners, of which the top 5 (Bajaj, Croma, Reliance Digital, Samsung, Vijay Sales) account for 68% of transaction volume.
- 320,000 decisions rendered in 2026-Q2. 71% auto-approval rate, 24% manual review, 5% decline.

## What is NOT in scope for the kiosk

- Credit cards (only personal loans and consumer durable loans).
- Joint applications (single applicant only).
- Self-employed / business loans (salaried customers only).
- Loans above ₹5,00,000 (manual branch application required).
