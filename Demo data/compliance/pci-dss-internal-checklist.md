---
title: PCI-DSS Internal Quarterly Checklist
category: compliance
owner: Engineering + Compliance
last_updated: 2026-07-01
status: active
---

# PCI-DSS Internal Quarterly Checklist

This is the internal compliance checklist for PCI-DSS v4.0 controls that apply to Vibe Fintech. It is reviewed every quarter by the Engineering and Compliance teams jointly.

## Scope

The PCI-DSS scope at Vibe covers:

- The production database containing cardholder data (CHD) — currently empty, as we do not store card numbers; we tokenize via the payment gateway.
- The payment gateway integration (Razorpay for domestic, Adyen for international).
- The S3 bucket that holds transaction receipts.
- The audit log store (DynamoDB, `ap-south-1`).

## Quarterly checks

### Network security

- [ ] Firewall rules reviewed. Confirm no rule allows inbound traffic from `0.0.0.0/0` to a CHD-bearing system.
- [ ] Default credentials rotated. No default admin passwords on any system.
- [ ] All production hosts have the latest security patches applied within 30 days of release.

### Access control

- [ ] List of all personnel with access to CHD or Sensitive Authentication Data (SAD) — confirmed current.
- [ ] Terminated employees removed from all access lists within 24 hours of termination.
- [ ] Multi-factor authentication enforced for all administrative access to the production environment.
- [ ] Quarterly review of IAM role permissions. No role has broader access than required for its function.

### Data protection

- [ ] All cardholder data at rest is encrypted with AES-256.
- [ ] All cardholder data in transit is encrypted with TLS 1.2 or higher.
- [ ] Production database backups are encrypted.
- [ ] No cardholder data is stored in non-production environments (no dev/test DB has real card data).
- [ ] Tokenization is confirmed working — the production database has zero rows in the `cardholder_data` table.

### Monitoring and logging

- [ ] Audit logs are being written for every access to the CHD scope.
- [ ] Audit logs are reviewed weekly by the Engineering on-call.
- [ ] File integrity monitoring (FIM) is active on all production hosts.
- [ ] Alerts are configured for any unauthorized access attempt.

### Vulnerability management

- [ ] Internal vulnerability scan run within the last quarter.
- [ ] External vulnerability scan run by an ASV within the last quarter.
- [ ] All critical and high vulnerabilities patched within 30 days of disclosure.
- [ ] Quarterly penetration test by an external vendor (last run: 2026-05-15).

### Documentation

- [ ] Network diagrams updated.
- [ ] Data flow diagrams updated.
- [ ] This checklist signed off by the Head of Compliance and the Head of Engineering.

## Sign-off

| Quarter | Engineering sign-off | Compliance sign-off | Date |
|---|---|---|---|
| 2026-Q2 | Rajesh K | Priya M | 2026-07-02 |
| 2026-Q1 | Rajesh K | Priya M | 2026-04-03 |
| 2025-Q4 | Rajesh K | Priya M | 2026-01-05 |

## Open issues

- FIM coverage on the new analytics hosts (deployed 2026-06) is not yet active. **Owner: Engineering, Due: 2026-08-15.**
- The penetration test report from 2026-05 has 2 medium-severity findings still under remediation. **Owner: Engineering, Due: 2026-08-30.**
