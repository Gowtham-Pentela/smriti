---
title: Weekly All-Hands — 15 July 2025
category: internal
owner: Anjali Sharma, Head of Product
last_updated: 2026-07-15
status: active
---

# All-Hands Notes — 15 July 2026

**Attendees:** 72 of 78 employees (Anjali absent, travelling to a Croma partner site).

## What Gowtham said

- The Series A is on track. The data room is open to the lead investor's counsel. Expect a term sheet by end of July.
- The Q2 numbers are good. We're at 71% auto-approval rate, which is 4 points above plan. The decline rate is also up 1.5 points, which means the model is being slightly more conservative — that's a feature, not a bug.
- The board has approved the Series A hiring plan (12 net-new engineers, weighted toward platform and ML). The job descriptions are with the People team. The hiring budget is ₹4.2 crore annualized, fully loaded.

## Product updates (Anjali's pre-read, presented by Rajesh)

- **Bajaj integration is live.** All 240 Bajaj kiosks are now running on the new Vibe v2.1 underwriting engine. First-week stats: 4,200 decisions, 73% auto-approval, 0 critical incidents.
- **Croma Tier 2 deal signed** on 2026-07-08. Onboarding starts next week. 1,400 kiosks. Target go-live: 2026-09-15.
- **Samsung negotiation paused** pending their internal review. Anjali is hopeful but not confident for Q3 close.
- **Kiosk hardware refresh** — we're evaluating the new Samsung Galaxy Tab A9 as a replacement for the current A8. Decision by end of August.

## Engineering updates (Rajesh)

- **Platform rewrite** is 60% done. The new service mesh is in production for the underwriting engine. The audit log migration is next.
- **Audit log** — Rohit's team is evaluating AWS QLDB vs DynamoDB for the tamper-evident requirement (see RBI circular summary). Decision by next week.
- **PCI-DSS Q3 scan** scheduled for 2026-08-15. All findings from the Q2 scan are remediated except one medium-severity issue on the analytics hosts, which is due 2026-08-30.
- **Model retraining** is on schedule. Next retrain: 2026-08-15. Karthik's team is also working on a more aggressive feature set for Stage 2.

## Compliance updates (Priya)

- The RBI December 2024 circular on data localization is now in our quarterly review. The board is expected to formalize the DCO role at the next board meeting (2026-08-22).
- Two STRs were filed in Q2 (both involving customers with rapid cross-border transfer patterns). The customers were not notified (as required by PMLA Section 12).
- The KYC re-verification queue is up to 890 customers. The compliance team is at 60% of target throughput. Hiring two more analysts.

## Data Science updates (Karthik)

- The Stage 2 model is showing better calibration than the Stage 1 model — as expected. The PD-vs-actual-default plot is well-aligned across all PD buckets.
- The feature drift detector flagged a small drift on the CIBIL score distribution last month. Investigation concluded it was a seasonal pattern, not a real drift. The detector works.
- Next experiment: a transformer-based model for the bureau feature interactions. Estimated 6-week effort. Karthik is scoping it this week.

## Q&A

**Q (Rohit):** Are we doing anything about the new DPDP Act 2023 rules that came out last month?

**A (Gowtham):** Yes. Priya is putting together a brief for the board on the impact. Expect a memo by next week.

**Q (Sandeep):** Is the platform rewrite going to impact the on-call rotation?

**A (Rajesh):** Yes, but not in a bad way. The new architecture has fewer moving parts, so the on-call load should drop. We're going to a 2-week rotation instead of 1-week for everyone except security.

**Q (Anjali, via Slack):** Can we get an update on the kiosk hardware refresh next week?

**A (Rajesh):** Yes, I'll send a note by Wednesday.

## Action items

- [ ] Rajesh: send kiosk hardware refresh update. **Due: 2026-07-22.**
- [ ] Priya: brief the board on the DPDP Act 2023 impact. **Due: 2026-07-22.**
- [ ] Karthik: scope the transformer-based bureau model. **Due: 2026-07-22.**
- [ ] Anjali: confirm the Croma onboarding timeline with Croma's PM. **Due: 2026-07-22.**
- [ ] People Ops: post the 12 new engineering job descriptions. **Due: 2026-07-25.**
