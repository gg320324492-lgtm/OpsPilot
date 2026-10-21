---
title: Account Suspension and Dunning
owner: Revenue Operations
effective: 2026-01-20
tags: [billing, collections, suspension]
---

# Account Suspension and Dunning

## Trigger

An account enters the dunning sequence when an invoice remains unpaid past its
due date. Payment terms are set per account and are one of `net15`, `net30` or
`net45`, counted from the invoice's issue date. `enterprise` contracts are net
30 unless the contract specifies otherwise, per
[enterprise-billing.md](enterprise-billing.md).


This system's customer and subscription records carry exactly two statuses:
`active` and `past_due`. An account on a dunning sequence is `past_due` here,
and that is the only step of the sequence the system represents. The later
stages — suspension, termination — are the states a fuller billing system would
carry, and they are described in this document because the dunning sequence is
the policy Support works to. They do not exist here.

## Dunning Sequence

| Day past due | Action |
|---|---|
| 1 | Automated reminder email |
| 7 | Second reminder, billing contact copied |
| 14 | Account status set to `past_due`; admin console shows a warning banner |
| 21 | Third reminder; service degradation notice |
| 30 | **Suspension**: write access disabled, data retained |
| 60 | Data export offered; termination scheduled for the end of the paid term |
| 90 | Termination, subject to the retention commitments in the contract |

Suspension disables write access and the API for new work. It does not delete
data, and it does not stop billing — the subscription remains active and invoices
continue to accrue, because the account still exists and the data is still held.

## Interaction With Refunds

**An account in suspension or termination does not receive refunds.** A refund
against a suspended account is applied as a credit against the outstanding
balance instead.

There is a defensible alternative — return the money and pursue the balance
separately — and this policy deliberately does not take it. Returning money to an
account that owes money converts a recoverable receivable into an unlikely
recovery. Where the outstanding balance is itself the disputed item, the dispute
procedure applies instead of this one; a refund is not the mechanism for
resolving a disagreement about whether a charge is owed.

A support agent or automated system encountering a refund scenario on a
suspended account must **escalate** rather than choose between these remedies.
The choice depends on whether the balance is disputed, which is not determinable
from the billing data alone.

## Reactivation

Payment in full, or a payment plan agreed with Revenue Operations, reactivates the
account within one business day. Partial payment does not reactivate; it pauses
the dunning clock.

## Exceptions

Suspension is **not** applied when:

- The account is under an active dispute, provided the dispute was raised before
  day 21.
- The account is on a contract with custom credit terms.
- The invoice is unpaid because of a documented billing error on our side. Where
  the error is verified, the invoice is corrected rather than pursued, and the
  dunning clock is reset.

The third exception is the one that interacts most often with support tickets: a
customer disputing a charge is frequently also overdue on it, and the correct
first step is to determine whether the dispute is a billing error before any
collections action proceeds.

