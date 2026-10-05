---
title: Payment Methods
owner: Finance Operations
effective: 2026-02-15
tags: [billing, payments]
---

# Payment Methods

## Accepted Methods

- Credit and debit cards (Visa, Mastercard, American Express)
- ACH transfer (US accounts, Business and Enterprise plans)
- Wire transfer (Enterprise only)
- Purchase order with approved credit terms (Enterprise only)

## Card Failures

A declined charge is retried automatically on days 3, 5 and 8 after the initial
attempt. Each retry is a new authorisation attempt against the same card; where it
succeeds, a single charge results, not two.

A retry that succeeds after a decline does **not** produce a duplicate charge,
even where two authorisation records are visible. Authorisation attempts that were
declined appear in the transaction list with status `declined` and are not
duplicates of a successful charge — the duplicate-detection criteria require both
transactions to be in `charged` status.

## ACH and Wire

ACH transfers settle in 3–5 business days. During settlement the transaction
appears as `pending`. A `pending` transaction is not a duplicate of a subsequent
`charged` transaction on the same invoice within the retry window; it is the same
payment before and after settlement.

This is the second common false-positive duplicate report. A customer seeing a
pending ACH and a charged card payment on one invoice has usually had an ACH fail
and a card fallback succeed.

## Updating a Payment Method

A payment method change does not re-bill the current period. The change takes
effect from the next invoice. A customer who updates their card and sees no new
charge has not been missed — the current period was already paid.

## Refunds To Cards

Refunds return to the original card and take 5–10 business days to appear on the
customer's statement. Support should communicate the timeframe when issuing a
refund, because a refund that has been issued but not yet posted generates a
follow-up ticket at a rate that is entirely avoidable.

Where the card is expired or closed, the refund is re-issued by ACH if the
customer has a verified bank account on file, or held as account credit
otherwise.

## Currency

Charges are issued in the contract currency, normally USD. Where a card is
billed in another currency, the customer's bank applies conversion; conversion
differences are not refundable because they are not charges we made.
