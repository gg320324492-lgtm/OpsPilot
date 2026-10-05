---
title: Chargebacks and Network Disputes
owner: Finance Operations
effective: 2026-02-05
tags: [billing, chargebacks, disputes]
---

# Chargebacks and Network Disputes

## What A Chargeback Is

A chargeback is a customer's dispute filed with their card issuer, not with us.
It is initiated outside our systems and results in the amount being withdrawn
from our merchant account while the network adjudicates.

A chargeback is not a support outcome, and a support agent or automated system
cannot prevent one by issuing a refund. Where a chargeback has been filed, the
refund route is closed — refunding an amount that is already in dispute can
result in a double return once the network rules against us.

## When A Chargeback Appears

A chargeback appears in the transaction list with status `chargeback` or
`reversed`, alongside the original charge. It is **not** a duplicate of the
original charge for detection purposes: the duplicate criteria require both
transactions to be in `charged` status.

A customer who reports "I was charged and also see a reversal" has usually filed
a chargeback. The correct response is to explain the process and route to Finance
Operations, not to refund.

## Our Position

We do not contest chargebacks for verified billing errors; the error is
acknowledged in the response and the amount is not re-billed.

We do contest chargebacks where the charge was correct, providing evidence of the
service delivered and the authorisation recorded.

## Interaction With Refunds

| State | Action |
|---|---|
| Refund already issued, no chargeback | Nothing further |
| Refund issued, chargeback also filed | Do not refund again; notify Finance Operations |
| Chargeback filed, no refund | Do not refund; route to Finance Operations |
| Chargeback resolved in our favour | The original charge stands; no refund |

The second row is the failure this document exists to prevent: a refund and a
chargeback for the same charge is a double return, and recovering it requires a
separate collections process.

## Representation

Finance Operations handles all representment within the network's response
window, which is typically 7–20 days depending on the network and reason code.
Support's role is to supply evidence — delivery records, authorisation records,
the support ticket history — within 3 business days of a request.

## Records

Every chargeback is recorded against the invoice and the transaction, with the
outcome, so that a subsequent support contact sees the dispute's status rather
than re-investigating a charge that is already in adjudication.
