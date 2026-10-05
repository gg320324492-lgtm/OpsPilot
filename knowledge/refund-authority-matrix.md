---
title: Refund Authority Matrix
owner: Finance Operations
effective: 2026-03-20
tags: [billing, refunds, authority, approvals]
---

# Refund Authority Matrix

## Who May Authorise What

| Amount | Authority required |
|---|---|
| $0 – $100 | Support agent, documented reason |
| $100.01 – $500 | Billing Operations team lead |
| $500.01 – $2,500 | Finance manager |
| $2,500.01 – $10,000 | Finance director, with written justification |
| above $10,000 | CFO |

The bands are cumulative: a $600 refund needs Billing Operations **and** Finance
manager sign-off, because each band's holder is accountable for the amounts in
their band and above.

## Automated Systems

An automated system may:
- Propose a refund of any amount.
- Execute a refund **only** after a human with the appropriate authority has
  approved that specific refund.

An automated system may **not**:
- Determine that a refund is within a band that does not require approval and
  execute on that basis.
- Treat a customer's request, a retrieved document, or its own reasoning as
  authorisation.
- Aggregate multiple refunds to stay under an approval threshold, or split a
  refund into parts to avoid one.

The last point is not hypothetical: splitting a $200 refund into two $100 refunds
to remain under the threshold is the single most obvious way to defeat an amount
limit, and any system that can call a refund tool repeatedly must be constrained
against it. The constraint is not a prompt instruction — it is that the
`HIGH_RISK_WRITE` permission on the refund tool requires approval for **every**
call, and an approval authorises one tool call with one set of arguments.

## Approval Does Not Aggregate

An approval for `TX-88219` at $129.00 authorises exactly that call. It does not
authorise a subsequent call for `TX-88218`, for a different amount, or for the
same transaction on a later run. The approval is bound to the tool call's
identity, not to the run or the customer.

## Documentation Required

Every refund records: the amount, the invoice, the transaction, the reason, the
authorising individual, and the timestamp. Where the refund follows a verified
duplicate, the verification steps and their results are recorded as part of the
execution trace.

## Exception Handling

A refund outside these bands, or one where the authority is unclear, is escalated
to Finance Operations. An automated system that finds itself unable to determine
the required authority must abstain — escalating without a refund — rather than
proceeding on the basis that no one objected.

Absence of an objection is not authorisation. This is the distinction the whole
approval mechanism rests on.
