---
title: Underwriting Engine v2 — Model Spec
category: products
owner: Data Science team
last_updated: 2026-05-04
status: active
---

# Underwriting Engine v2 — Model Specification

This document describes the credit decisioning model that powers the Kiosk Underwriting product. The model is owned by the Data Science team, with sign-off from the Risk Committee.

## Architecture

The engine is a two-stage model:

1. **Stage 1 — Pre-screen (always runs):** a lightweight gradient-boosted decision tree (XGBoost) that takes the bureau data + the requested loan parameters and outputs a probability of default (PD) in the next 12 months. The Stage 1 model is 12 MB and runs in <100 ms.
2. **Stage 2 — Full decision (runs only if Stage 1 PD is between 0.02 and 0.35):** a larger ensemble (XGBoost + LightGBM + a small neural net) that uses the same bureau features plus internal Vibe history (previous loans, repayment behavior) plus a few macroeconomic features. The Stage 2 model is 180 MB and runs in <800 ms.

If Stage 1 PD is below 0.02, the application is auto-approved without Stage 2.
If Stage 1 PD is above 0.35, the application is auto-declined without Stage 2.

The combination of the two stages keeps average latency under 2 seconds and concentrates compute on the borderline cases.

## Inputs

### Bureau features (from CIBIL + Experian + CRIF High Mark)

- CIBIL score (the primary bureau score; the one shown to the customer on the kiosk).
- Number of active tradelines.
- Total outstanding balance across all tradelines.
- Number of hard enquiries in the last 6 months.
- Worst delinquency in the last 24 months (in DPD — days past due).
- Average account age (in months).
- Credit utilization ratio.

### KYC features (from our onboarding flow)

- Age.
- Employment type (salaried / self-employed — only salaried is accepted in the kiosk flow).
- Monthly declared income.
- Employer category (MNC / large Indian private / SME / government / other).

### Loan parameters

- Requested loan amount.
- Requested tenure (in months).
- Product type (personal loan / consumer durable loan).

### Internal Vibe features (only if the customer has a previous Vibe relationship)

- Number of previous Vibe loans.
- Worst delinquency on a previous Vibe loan.
- Time since the most recent Vibe loan.
- Repayment ratio (on-time payments / total payments).

## Outputs

The model outputs:

- **PD (probability of default)** in the next 12 months — a float between 0 and 1.
- **Decision**: `auto_approve`, `manual_review`, or `auto_decline`.
- **Reason codes**: a list of 3–5 human-readable explanations, e.g. "CIBIL score 612 is below the 650 auto-approval threshold".
- **Suggested loan terms** (if auto-approved): the maximum loan amount we will offer, and the interest rate band.

## Decision rules (overlay on top of the model)

The model is augmented with hard-coded business rules. The rules override the model in the following cases:

1. **CIBIL score below 500** → always `auto_decline`, no matter what the model says.
2. **CIBIL score 500–649** → always `manual_review`, no matter what the model says.
3. **Existing Vibe loan with 60+ DPD in the last 12 months** → always `auto_decline`.
4. **Customer is on the RBI defaulter list or the SEBI debarred list** → always `auto_decline` and route to compliance.
5. **Loan amount above ₹5,00,000** → always `manual_review` (out of kiosk scope).
6. **Customer is a PEP** → always `manual_review` and route to compliance.

The **minimum CIBIL score for auto-approval is 650**. Below 650, the decision is always `manual_review` (or `auto_decline` if below 500), regardless of the model's PD output.

## Training data

The Stage 1 model was trained on 2.3 million Vibe decisions rendered between 2023-01 and 2025-12, of which 180,000 had a 12-month outcome (charged off, settled, or current). The Stage 2 model uses the same dataset with the additional Vibe-internal features.

The dataset is split 80/10/10 for train/validation/test, stratified by outcome. The model is retrained every 6 months; the next retrain is scheduled for 2026-08.

## Monitoring

The model is monitored daily for:

- **Calibration drift** — the actual default rate vs the predicted PD, in 5 PD buckets.
- **Feature drift** — the distribution of each input feature, compared to the training distribution.
- **Outcome drift** — the 30/60/90 DPD rates on auto-approved loans, compared to the previous month.

Any drift > 10% triggers a P2 incident and an immediate review by the Data Science team.
