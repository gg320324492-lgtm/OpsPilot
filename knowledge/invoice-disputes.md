---
title: Invoice Dispute Handling
owner: Billing Operations
effective: 2026-03-10
tags: [billing, disputes, procedure]
---

# Invoice Dispute Handling

## Scope

A dispute is a customer assertion that a charge is not owed. It is distinct from
a duplicate-charge report: a duplicate is a verified billing fault with a
mechanical remedy, while a dispute is a disagreement about whether a charge
should exist at all.

## Dispute Categories

| Category | Example | Route |
|---|---|---|
| Duplicate charge | Charged twice for one invoice | [duplicate-charge-sop.md](duplicate-charge-sop.md) |
| Amount disagreement | "This should have been $99, not $129" | Verify against contract; correct if wrong, explain if right |
| Service not received | "We were billed for October but had no service" | Check SLA and incident records |
| Unauthorised charge | "We never ordered this" | Security; possible compromise |
| Cancellation ignored | "We cancelled in September" | Check cancellation record and effective date |

## Verification Order

Always verify in this order, because each step can invalidate the need for the
next:

1. Does the invoice exist and belong to the reporting customer?
2. Does the invoice amount match the contract or plan for the period?
3. Is there a duplicate charge on this invoice (mechanical check)?
4. Is there a cancellation, downgrade or plan change recorded that affects the
   period?
5. Is there a service incident affecting the period?

A dispute that resolves at step 3 is a duplicate and follows the SOP. A dispute
that resolves at step 4 is normally a misunderstanding of the effective date, and
is explained rather than refunded.

## Unauthorised Charges

A customer reporting a charge they did not authorise is a **security** incident,
not a billing one, until proven otherwise. Do not refund. Do not discuss the
account's payment methods in detail. Route to Security immediately, and note that
a refund issued before the security review could destroy evidence of how the
charge was made.

This is the one dispute category where the correct action is deliberately the
opposite of what the customer is asking for.

## Resolution Records

Every dispute resolution records the category, the verification steps and their
results, and the outcome. Where the outcome is a refund, the refund follows the
normal approval rules — a dispute finding does not by itself authorise a refund
above the approval threshold.

## Timeframes

Disputes must be acknowledged within one business day and resolved within ten
business days or escalated. Where a dispute concerns a card chargeback, the
network's own timetable governs and Support's role is to provide evidence, not to
decide.
