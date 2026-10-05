---
title: Refund Policy
owner: Finance Operations
effective: 2026-01-15
tags: [billing, refunds, policy]
---

# Refund Policy

## Scope

This policy governs refunds issued against charges on a customer account,
regardless of payment method or plan tier. It applies to refunds initiated by
Support, by Billing Operations, and by automated systems acting on their behalf.

## Refund Limits

Refunds above **$100** require human approval before they are issued. This
threshold is per transaction, not per invoice and not per month: three refunds of
$40 each do not aggregate into a $120 refund for the purposes of this rule, and
one refund of $101 does.

Refunds at or below $100 may be issued by Support directly, provided the reason
is documented on the account.

This limit exists because automated and support-initiated refunds are the primary
route by which a billing error becomes an unrecoverable loss. A refund that
should not have been issued is money that has left the business and cannot be
recalled.

## Eligible Reasons

A refund may be issued when one of the following holds and is documented:

- A duplicate charge was applied to a single invoice.
- A service was charged after a documented cancellation.
- A plan change was applied on the wrong effective date, producing an
  overcharge.
- A charge was applied in error due to a payment-processing fault.

"Customer is unhappy with the price" is not an eligible reason. Dissatisfaction
is handled through retention offers, not refunds.

## Ineligible Reasons

- Charges older than **180 days**. Older charges are handled as a credit against
  a future invoice, if at all.
- Charges on a terminated account with an unpaid balance. The refund is offset
  against the balance first.
- Voluntary overages the customer authorised in the admin console. These are
  invoiced as used.

## Interaction With Duplicate Charges

A duplicate charge is a specific case with its own procedure. Where a duplicate
charge has been verified, the applicable procedure is
[duplicate-charge-sop.md](duplicate-charge-sop.md), which may permit a full
refund of the duplicate even where it exceeds the $100 limit above — the limit
governs *discretionary* refunds, not corrections of a verified billing fault.

When this policy and the duplicate-charge procedure appear to conflict, the
procedure governs for duplicates, because it is the more specific rule. The
approval requirement for amounts above $100 still applies.

## Partial Refunds

A partial refund may be issued when a service was degraded for a documented
period. The refund amount is calculated as:

    (days affected / days in billing period) x period charge

rounded to the nearest cent and capped at the period charge. Partial refunds are
recorded against the invoice, not against the individual transaction.

## Currency and Method

Refunds are issued in the currency of the original charge. Refunds return to the
original payment method; we do not issue refunds as account credit unless the
original method is unavailable or the charge is older than the card network's
refund window (typically 120 days).

## Records

Every refund must have a documented reason and be associated with the invoice it
corrects. Refunds issued without a recorded reason are treated as a policy
violation during audit, even when the amount is within discretionary limits.
