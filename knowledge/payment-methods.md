---
title: Payment Methods
owner: Finance Operations
effective: 2026-02-15
tags: [billing, payments]
---

# Payment Methods
## Accepted Methods

- Credit and debit cards (Visa, Mastercard, American Express)
- ACH transfer (US accounts, `growth` and `enterprise` plans)
- Wire transfer (`enterprise` only)
- Purchase order with approved credit terms (`enterprise` only)

## Card Failures

A declined charge is retried automatically on days 3, 5 and 8 after the initial
attempt. Each retry is a new authorisation attempt against the same card; where it
succeeds, a single charge results, not two.

A retry that succeeds after a decline does **not** produce a duplicate charge,
even where two authorisation records are visible. A declined authorisation is
not a transaction: it does not reach the transaction list at all, so there is
nothing to compare — the duplicate-detection criteria require both transactions
to be in `charged` status, and a decline is not a transaction here.


This system has exactly two transaction statuses: `charged` and `refunded`. The states discussed in this document — a decline, a payment pending settlement, a void, a chargeback, a reversal — are the states a fuller billing system would carry, and they are described here because Support staff meet them in the wild. They do not exist here: a transaction this system returns is `charged` or `refunded`, and nothing else.

## ACH and Wire

ACH transfers settle in 3–5 business days. During settlement the payment is
still in flight and is not yet a transaction: a transaction exists once the
money settles, and its status is then `charged`. An in-flight payment is not a
duplicate of a subsequent `charged` transaction on the same invoice within the
retry window; it is the same payment before and after settlement.

This is the second common false-positive duplicate report. A customer seeing an
ACH in flight and a charged card payment on one invoice has usually had an ACH
fail and a card fallback succeed.

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

