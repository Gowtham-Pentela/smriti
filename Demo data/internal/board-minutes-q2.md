---
title: Q2 Board Minutes
category: internal
owner: Gowtham Pentela, CEO
last_updated: 2026-07-05
status: confidential — board members only
---

# Q2 2026 Board Meeting Minutes

**Date:** 2026-06-28
**Location:** Vibe Fintech HQ, Indiranagar, Bangalore
**Attendees:** All 5 board members (Gowtham Pentela, Sanjay Mehta — Sequoia, Deepa Subramaniam — Accel, Vikram Singh — independent, Ritu Agarwal — independent), Priya Menon (Head of Compliance) for agenda item 4, Rajesh Kumar (Head of Engineering) for agenda item 5.

## 1. Q2 financial review

- Revenue: ₹3.4 crore for the quarter. Up 41% from Q1.
- Gross margin: 64% (consistent with Q1).
- Burn: ₹2.1 crore per month, down from ₹2.4 crore per month in Q1 (Rajesh's AWS cost optimization kicked in).
- Cash in the bank: ₹22 crore as of 2026-06-30. Runway: 11 months at current burn.

**Board action:** None. Numbers are on plan.

## 2. Product update

- Total decisions rendered: 320,000 in Q2 (up 38% from Q1).
- Auto-approval rate: 71% (up from 67% in Q1). The new Stage 2 model is the main driver.
- Decline rate: 5% (up from 3.5% in Q1). The model is being slightly more conservative — this is intentional (Priya pushed for it after the Q1 AML review).
- Average decision latency: 1.8 seconds (within SLA).
- NPS from retail partners: 47 (up from 38 in Q1).

The Bajaj deal closed on 2026-05-15. They brought 240 kiosks onto the v2.1 engine in the first week. The integration was smoother than expected — Rajesh's team deserves credit.

**Board action:** Approve the Croma Tier 2 deal (1,400 kiosks). Approved unanimously.

## 3. Series A update

- The data room is open. The lead investor's counsel has completed the financial due diligence. The legal due diligence is in progress.
- The term sheet is expected by end of July. The proposed terms are: $8M at a $40M post-money valuation, 1.5x liquidation preference, no anti-dilution.
- **Board discussion on the term sheet:**
  - Sanjay (Sequoia) thinks the valuation is fair. Sequoia is in the lead-investor consortium but they are not the lead on this round (they are pre-empting their pro-rata).
  - Deepa (Accel) agrees on the valuation but is pushing for a more investor-friendly anti-dilution clause. Gowtham pushed back; the current language is standard.
  - Vikram (independent) raised the question of whether the 11-month runway means we should be closing earlier. Gowtham explained that closing later gives more leverage on the valuation.
  - Ritu (independent) asked about the use of funds. Gowtham walked through the plan: 60% engineering hiring, 25% sales and partner success, 15% compliance and risk.

**Board action:** Approve the term sheet in principle. The final sign-off will be at the next board meeting once the term sheet is in hand. Approved unanimously.

## 4. Compliance and risk (Priya presents)

- The RBI December 2024 circular on data localization is now in scope. We are compliant on the data residency front (all data in `ap-south-1`). The audit log tamper-evidence requirement is still being evaluated (Rohit's team is working on it).
- **Board decision:** Formalize the DCO (Data Compliance Officer) role. **Priya Menon is appointed as the DCO**, with the title and responsibilities added to her employment contract. Approved unanimously.
- Two STRs were filed in Q2. Both were follow-ups from Q1 patterns (rapid cross-border transfers). No material findings from the FIU-IND.
- The KYC re-verification backlog (890 customers) is being addressed. Hiring two more analysts. Target: backlog cleared by 2026-Q4.

## 5. Engineering and security (Rajesh presents)

- Platform rewrite is 60% done. The new service mesh is in production for the underwriting engine. The audit log migration is the next milestone. Target completion: 2026-Q3.
- PCI-DSS Q2 scan completed. All critical and high findings remediated. One medium finding on the analytics hosts is due 2026-08-30.
- **Board decision:** Approve the hire of a Security and SRE lead. **Rohit Bhatia's offer has been accepted, start date 2026-02-15.** Approved unanimously.
- The model retraining pipeline is on schedule. The next retrain is 2026-08-15. Karthik's team is also exploring a transformer-based model for Stage 2.

## 6. Hiring plan

- The board approved a **Q3 hiring plan of 12 net-new engineering hires**, weighted toward platform and ML. Total annualized cost: ₹4.2 crore fully loaded. The plan is funded out of the existing cash, not the Series A.
- Job descriptions are with People Ops. Target close: 2026-Q4.
- The board also approved a **2 net-new compliance analyst hires** to clear the KYC backlog.

## 7. Next board meeting

- **Date:** 2026-09-25 (subject to Series A term sheet timing — may move earlier).
- **Agenda items:** (1) Series A term sheet sign-off, (2) Q3 hiring plan progress, (3) Platform rewrite completion, (4) DCO formalization, (5) DPDP Act 2023 impact assessment.

## 8. Confidential items

- The board discussed the potential acquisition interest from a strategic buyer. The discussion is preliminary and the board agreed to keep it confidential for now. No further action.
- Vikram raised the question of whether we should explore a strategic partnership with a large bank. The board agreed this is worth exploring post-Series A.

---

*Minutes prepared by: Anjali Sharma, Head of Product.*
*Approved by: All 5 board members on 2026-07-05.*
