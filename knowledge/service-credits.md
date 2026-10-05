---
title: Service Credits vs Refunds
owner: Finance Operations
effective: 2026-03-15
tags: [billing, credits, refunds]
---

# Service Credits vs Refunds

## The Distinction

A **refund** returns money already collected. It is issued for a billing error —
a charge that should not have been made.

A **service credit** reduces a future invoice. It is issued for a service failure
— a commitment that was not met, where the charge itself was correct.

The two are not interchangeable, and the distinction is not cosmetic: a refund
moves money out of the business, a credit changes a figure on an invoice not yet
issued. Support agents and automated systems routinely conflate them because both
are described by customers as "getting money back".

## Which Applies

| Situation | Remedy |
|---|---|
| Duplicate charge | Refund |
| Charge after cancellation | Refund |
| Wrong plan applied | Refund of the difference |
| SLA commitment missed | Service credit |
| Degraded service for a documented period | Service credit |
| Customer unhappy with price | Neither |
| Customer unhappy with a feature | Neither |

## Why This Matters For Automated Handling

An automated agent asked to "give the customer their money back" for a missed SLA
must not call a refund tool. The correct action is a credit, which is applied by
Finance Operations and is not a tool an agent has. The agent's correct behaviour
is to recognise the situation as a credit case and escalate, explaining the
credit.

Proposing a refund for an SLA breach is a policy error even when the amount is
small and even when the customer is insistent. It resolves the ticket and creates
a reconciliation problem, and it teaches the customer that insisting produces a
refund.

## Credit Application

Credits are applied to the next invoice automatically once approved. A credit
exceeding the next invoice's total carries forward. Credits expire after 12
months if unused, at which point they are written back.

Credits do not appear as a line on the *current* invoice, which is why a customer
told "the credit is applied" may see no change immediately. Communicate the
timing.

## Disputes About The Remedy

Where a customer insists on a refund rather than a credit, the ticket is
escalated. It is not resolved by issuing the refund. The policy position is that
the remedy follows the cause, and a customer preference for one form of
compensation over another is not a reason to change which one applies.

Where the customer's contract specifies a refund for an SLA breach — some
Enterprise contracts do — the contract governs and the refund follows the normal
approval path.
