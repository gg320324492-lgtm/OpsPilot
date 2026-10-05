---
title: Escalation Policy
owner: Support Operations
effective: 2026-03-05
tags: [support, escalation, procedure]
---

# Escalation Policy

## When To Escalate

Escalate a ticket rather than resolving it when any of the following holds:

- The correct action is not determinable from the available policy and data.
- The available knowledge does not address the customer's question.
- The requested action conflicts with policy and the customer is likely to
  disagree.
- The situation spans multiple domains in a way a single procedure does not
  cover.
- The account is suspended, terminated, or under dispute.
- The requested action touches money above the applicable approval threshold
  **and** no approver is available.
- The customer has asked to escalate.

## What Escalation Is Not

Escalation is not a way to avoid an approval. "This refund needs approval, so I
escalated it" is a process violation: sending a refund to a human as an
*escalation* rather than as an *approval request* loses the structured record of
which tool call was authorised, and the refund may then be issued through a
channel with no trace.

Where a high-risk action is required and an approver is available, the correct
path is the approval gate. Escalation is for cases the policy does not resolve,
not for cases the policy resolves but requires authorisation to execute.

## Escalation Tiers

| Tier | Handles | Response target |
|---|---|---|
| Tier 1 | Standard tickets, documented issues | 8 business hours |
| Tier 2 | Tier 1 unresolved, cross-domain issues | 4 business hours |
| Billing Operations | Refund disputes, invoice corrections | 1 business day |
| Engineering | Confirmed defects | per severity |
| Security | Suspected account compromise, unauthorised access | 1 hour |

Routing to the wrong tier is a delay, not a failure, provided the ticket carries
enough context for the receiving tier to re-route it. An escalation with no
context is the failure.

## Insufficient Knowledge

The most common correct escalation in an automated support context: the question
is legitimate, the policy corpus does not answer it, and the honest response is
"I could not determine this; a human will follow up."

An automated system must not:
- Compose an answer from weak or partially-matching evidence and present it as
  policy.
- Invent a policy position to resolve a ticket.
- Issue an action on the strength of a document it retrieved but which does not
  actually address the question.

Abstention is a supported outcome and is preferred over a confident wrong answer.
A support reply that states a policy we do not have is worse than a reply that
promises a follow-up, because the first creates a commitment we then have to
honour or retract.

## Escalation Record

Every escalation records: the reason, the tier it was routed to, and the
determination that produced the reason. For an automated run, the execution trace
is the record — which is why the trace must capture the retrieval results even
when the outcome is "no document answered this".
