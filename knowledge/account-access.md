---
title: Account Access and Authentication
owner: Security
effective: 2026-02-20
tags: [security, access, authentication]
---

# Account Access and Authentication

## Password Reset

A password reset is sent to the account's primary email. Reset links expire after
60 minutes. A customer who receives no email should check spam and confirm the
address on file before Support attempts any manual reset.

Support **cannot** reset a password manually or read a reset link. Where a
customer has lost access to their email, identity verification is required per
the identity verification procedure, and the ticket is routed to Security rather
than resolved by Support.

## Multi-Factor Authentication

MFA may be enforced at the organisation level by an account administrator. Where
enforced, a user who cannot complete MFA cannot be granted access by Support.
Administrators can reset an individual user's MFA enrolment from the admin
console.

An administrator who has lost their own MFA enrolment requires identity
verification plus approval from a second administrator on the same account.

## Locked Accounts

Accounts lock after 10 consecutive failed sign-in attempts and unlock
automatically after 30 minutes. Support may not unlock an account early. Repeated
locking within a short period is treated as a possible credential-stuffing
attempt and is escalated to Security.

## Suspended Accounts and Access

A suspended account (see [account-suspension.md](account-suspension.md)) retains
read access to its data export and to the admin console's billing view. Write
access is disabled. A customer reporting "I cannot save anything" on a suspended
account is experiencing the intended effect of the suspension, not a technical
fault.

## Session and Token Handling

API tokens are shown once at creation and cannot be retrieved afterwards. A
customer who has lost a token must rotate it; Support cannot recover it.

## Authorised Users

Support acts on requests from the account's registered billing contact, or from
an address on the account's authorised-user list. A request from an unlisted
address that would change billing or access is escalated to the billing contact
for confirmation, even where the request is otherwise routine. The confirmation
is the control, not the plausibility of the request.
