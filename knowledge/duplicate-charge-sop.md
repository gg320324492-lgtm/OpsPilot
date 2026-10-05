---
title: Duplicate Charge Handling SOP
owner: Billing Operations
effective: 2026-02-01
tags: [billing, refunds, procedure, sop]
---

# Duplicate Charge Handling — Standard Operating Procedure

## When This Procedure Applies

A customer reports being charged more than once for the same invoice, or
investigation reveals more than one successful charge against a single invoice.

## Detection

A duplicate charge exists when **all** of the following hold for two or more
transactions on the same invoice:

1. Same invoice identifier.
2. Same amount, to the cent.
3. Both transactions in `charged` status (not `voided`, `declined` or
   `refunded`).
4. Both processed within **72 hours** of each other.

The 72-hour window matters. Two charges on one invoice six months apart are not a
duplicate — they are an arrears payment and a re-billing, or a plan change, and
treating them as a duplicate would refund a legitimate charge.

## Verification Steps

Before proposing any refund, confirm every step. Each step corresponds to a tool
call in the automated procedure, and a step that cannot be completed means the
refund must not be proposed.

1. **Identify the customer.** Resolve the reporting email to a customer record.
   An email that matches no customer is escalated, never guessed.
2. **Fetch the invoice.** Confirm the invoice belongs to that customer. A
   customer reporting an invoice that is not theirs is escalated to security.
3. **List the invoice's transactions.** Confirm the duplicate against the
   detection criteria above.
4. **Check for an existing refund.** If a refund already exists for the
   duplicate transaction, do not issue another. Report the existing refund to the
   customer.

Step 4 is not optional. The single most common cause of a duplicate *refund* is
an agent or operator re-processing a case that was already resolved. Every
refund must carry an idempotency key so that a repeated attempt returns the
original refund rather than creating a second one.

## Resolution

### Verified duplicate

1. Refund the **later** of the duplicate transactions in full. The earlier charge
   is the one that stands. Refunding the later one keeps the customer's
   accounting period intact.
2. If the later transaction has already been refunded, refund nothing further
   and cite the existing refund.
3. If more than two charges exist, refund all but the earliest.

### Amount limits

A duplicate-charge refund is a correction of a billing fault, so it is not
subject to the discretionary-refund limit in [refund-policy.md](refund-policy.md)
as a *reason to decline*. The approval requirement still applies: **any refund
above $100 requires human approval**, including a duplicate refund, without
exception.

For Enterprise plan customers, a verified duplicate may be refunded in full
including any associated fees. See
[enterprise-billing.md](enterprise-billing.md) for the fee treatment.

### Not a duplicate

If verification fails, do not refund. Reply explaining what the charges were and
offer to review a specific invoice if the customer has further questions.

## Escalation

Escalate rather than deciding when:

- The duplicate spans two different invoice identifiers.
- The amounts differ by any amount.
- The customer's account is suspended or terminated.
- The reporting email does not match the invoiced customer.
- The transactions have different payment methods.

## Documentation

The incident must be recorded with the invoice, the duplicate transactions, the
verification steps performed and their results, and the refund reference. For an
automated run, the execution trace is the record, provided it captures each
verification step.
