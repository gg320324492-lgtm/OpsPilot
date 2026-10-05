---
title: Enterprise Billing Terms
owner: Finance Operations
effective: 2026-03-01
tags: [billing, enterprise, plans]
---

# Enterprise Billing Terms

## Applicability

These terms apply to accounts on the **Enterprise** plan. They modify, and where
they conflict take precedence over, the standard terms in
[refund-policy.md](refund-policy.md) and
[subscription-policy.md](subscription-policy.md).

## Billing Cycle

Enterprise accounts are invoiced **monthly in advance**, on the first day of the
billing period. The invoice covers the coming period, not the past one. An
invoice dated 2026-10-01 covers 2026-10-01 through 2026-10-31.

This differs from the standard plan, which invoices in arrears. The difference
matters when reasoning about a charge that appears before a service period has
elapsed: on Enterprise, that is normal and not an error.

## Duplicate Charges

Enterprise duplicate charges may be refunded **in full**, including any
associated processing fees, once the duplicate has been verified under
[duplicate-charge-sop.md](duplicate-charge-sop.md).

The fee treatment is the Enterprise-specific part. On standard plans, processing
fees are retained on a refund because the processor does not return them. On
Enterprise, fees are absorbed by us and the customer receives the full charge
back.

**The approval requirement is not waived.** Refunds above $100 still require
human approval. Enterprise status affects the *amount* that may be refunded, not
the *authorisation* required to refund it. These are separate questions and
conflating them is how an unreviewed refund gets issued.

## Seat Changes

Enterprise seat count may be changed at any time. Mid-period additions are
charged pro-rata for the remainder of the period. Mid-period removals take effect
at the start of the next period; they are not credited mid-period.

A seat removal followed by a re-addition within the same period produces two
charges on one invoice at different amounts. This is **not** a duplicate charge —
the amounts differ, so the detection criteria in
[duplicate-charge-sop.md](duplicate-charge-sop.md) are not met. It is a normal
pro-ration artefact and should be explained to the customer rather than refunded.

## Overages

Enterprise contracts include a monthly overage allowance. Charges beyond the
allowance are invoiced monthly in arrears and marked as overage line items.
Overage charges are not refundable where the usage was authorised in the admin
console.

## Credit Terms

Enterprise accounts are billed on **net 30** unless the contract specifies
otherwise. An invoice unpaid at 30 days is overdue and enters the dunning
sequence described in [account-suspension.md](account-suspension.md).

## Service Credits

Where an Enterprise SLA commitment is missed, the remedy is a service credit
against the next invoice — not a refund. See [support-sla.md](support-sla.md) for
the credit schedule. A service credit and a refund are distinct remedies and are
not interchangeable: a credit reduces a future invoice, a refund returns money.
