---
title: Support SLA
owner: Support Operations
effective: 2026-01-05
tags: [support, sla, service-credits]
---

# Support Service Level Agreement

## Severity Levels

| Severity | Definition | First response | Status updates |
|---|---|---|---|
| P1 | Service unavailable for all users, or data loss | 1 hour | every 2 hours |
| P2 | Major function unavailable, workaround exists | 4 hours | every 8 hours |
| P3 | Minor function degraded | 1 business day | every 3 business days |
| P4 | Question, cosmetic issue, feature request | 2 business days | on change |

Response targets are time-to-first-human-response, not time-to-resolution.
Resolution is best-effort and is not covered by this SLA.

## Service Credits

Where a P1 or P2 commitment is missed, the customer is entitled to a service
credit against the next invoice:

| Missed commitment | Credit |
|---|---|
| P1 first response | 5% of monthly charge |
| P1 unresolved > 4 hours | 10% of monthly charge |
| P2 first response | 3% of monthly charge |
| Any P1/P2 unresolved > 72 hours | 25% of monthly charge |

Credits are capped at 50% of the affected month's charge and must be claimed
within 30 days of the incident.

**A service credit is not a refund.** A credit is applied to a future invoice; a
refund returns money already collected. A customer requesting a refund for an SLA
breach is requesting something this policy does not provide — offer the credit
and explain the difference, and escalate if the customer disputes it.

## Exclusions

The SLA does not apply to:

- Failures caused by customer configuration or third-party integrations.
- Scheduled maintenance announced at least 72 hours in advance.
- Force majeure.
- Accounts on the Standard plan, which has no response commitment.
- Accounts in suspension. A suspended account's tickets are deprioritised, and
  suspension itself is not a service incident.

## Support Hours

Standard and Business plans receive support during business hours in their
region. Enterprise plans receive 24x7 support for P1 incidents with a named
contact.

## Relationship To Billing Incidents

A billing dispute is normally a P3 or P4 regardless of how the customer phrases
it. A customer reporting an incorrect charge is not experiencing a service
outage, and classifying it as P1 to accelerate a refund is a misuse of the
severity scale that also corrupts incident metrics.

Where a billing error is caused by a platform defect affecting many customers, it
is a P2 incident in its own right, tracked separately from the individual tickets
it generates.
