---
title: Engineering Org Chart
category: internal
owner: People Ops
last_updated: 2026-07-01
status: active
---

# Engineering Org Chart (as of 2026-07-01)

Total headcount: 47 (Engineering), 78 (Company).

## Leadership

- **Gowtham Pentela** — CEO. Sets product direction, signs off on Series A allocation. (gowtham@vibefintech.in)
- **Priya Menon** — Head of Compliance & Risk. Owns KYC, AML, RBI reporting. (priya@vibefintech.in)
- **Rajesh Kumar** — Head of Engineering. Owns the platform, the underwriting engine, the integrations. (rajesh@vibefintech.in)
- **Anjali Sharma** — Head of Product. Owns the kiosk roadmap, the partner relationships, the pricing. (anjali@vibefintech.in)
- **Karthik Iyer** — Head of Data Science. Owns the underwriting model, the bureau data, the monitoring. (karthik@vibefintech.in)

## Engineering teams

### Platform team (8 engineers)

Owns: the FastAPI backend, the ECS Fargate deployment, the RDS Postgres database, the audit log DynamoDB, the CI/CD pipeline.

- Lead: **Sandeep Naik** (sandeep@vibefintech.in)
- On-call rotation: weekly, currently held by **Vivek Reddy**.

### Underwriting team (6 engineers + 3 data scientists)

Owns: the underwriting model service, the model retraining pipeline, the feature store, the model monitoring dashboards.

- Engineering lead: **Meera Joshi** (meera@vibefintech.in)
- Data Science lead: **Arjun Pillai** (arjun@vibefintech.in) — joined 2026-04 from Cred.
- On-call rotation: weekly, currently held by **Meera Joshi**.

### Integrations team (5 engineers)

Owns: the bureau integrations (CIBIL, Experian, CRIF), the payment gateway integrations (Razorpay, Adyen), the KYC/identity integrations (UIDAI, Setu).

- Lead: **Faisal Khan** (faisal@vibefintech.in)
- On-call rotation: weekly, currently held by **Tara Menon**.

### Android team (6 engineers)

Owns: the kiosk Android app, the offline cache, the hardware integration (card reader, printer).

- Lead: **Naveen Shetty** (naveen@vibefintech.in)
- On-call rotation: weekly, currently held by **Aditya Rao**.

### Data infrastructure team (4 engineers)

Owns: the data warehouse (Snowflake on AWS Mumbai), the Airflow pipelines, the internal BI dashboards.

- Lead: **Sneha Iyer** (sneha@vibefintech.in)

### Security and SRE (4 engineers)

Owns: the AWS infrastructure, the secrets management, the monitoring and alerting, the incident response.

- Lead: **Rohit Bhatia** (rohit@vibefintech.in) — joined 2026-02 from AWS.
- On-call rotation: weekly, currently held by **Rohit Bhatia**.

## Compliance and risk

- **Priya Menon** (Head of Compliance) + 4 compliance analysts.
- The compliance team is the primary stakeholder for every change to the underwriting model, the KYC flow, or the AML rules engine.

## Who owns what — quick reference

| Area | Primary owner | Secondary owner |
|---|---|---|
| Underwriting model | Karthik Iyer (DS) | Rajesh Kumar (Eng) |
| KYC flow | Priya Menon | Rajesh Kumar |
| AML rules engine | Priya Menon | Karthik Iyer |
| Bureau integrations | Faisal Khan | Meera Joshi |
| Disbursal flow | Faisal Khan | Naveen Shetty |
| Audit log | Rohit Bhatia | Priya Menon |
| Kiosk hardware | Naveen Shetty | Anjali Sharma |
| Pricing | Anjali Sharma | Gowtham Pentela |
| Partner relationships | Anjali Sharma | Gowtham Pentela |
| AWS infrastructure | Rohit Bhatia | Sandeep Naik |

## Recent changes

- 2026-06-15: Arjun Pillai joined as Data Science lead. The previous DS lead, Karthik Iyer, was promoted to Head of Data Science.
- 2026-05-01: Sneha Iyer's team moved from Platform to a new "Data Infrastructure" team. The move was made to free up Sandeep Naik to focus on the platform rewrite.
- 2026-02-15: Rohit Bhatia joined as Security and SRE lead. The previous lead, Aditya Rao, moved to the Android team.
