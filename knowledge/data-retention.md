---
title: Data Retention Schedule
owner: Compliance
effective: 2026-01-01
tags: [compliance, data, retention]
---

# Data Retention Schedule

## Retention Periods

| Data class | Retention | Basis |
|---|---|---|
| Invoice and transaction records | 7 years | Tax and audit obligations |
| Support tickets | 3 years | Dispute resolution |
| Agent execution traces | 1 year | Operational and audit |
| Authentication logs | 90 days | Security investigation |
| Application logs (non-security) | 30 days | Operational |
| Customer data (active account) | Indefinite | Contract performance |
| Customer data (terminated account) | 90 days after termination, then purged | Contract and privacy commitments |

## Deletion and Audit Conflict

Invoice and transaction records are retained for 7 years and are therefore
retained past the end of most customer relationships. Where a customer requests
deletion of their data, financial records are excluded from the deletion and are
retained under the legal-obligation basis. This is disclosed in the privacy
notice and is not negotiable on request.

## Traces

An agent execution trace is retained for one year. Traces may contain customer
data — ticket text, customer records returned by tools, the model's prompt and
response — and are therefore subject to the same access controls as the data they
reference.

Traces are used for operational debugging, evaluation, and audit of automated
decisions. A trace of an automated refund decision is the record that the
decision was authorised, and is retained for the full year regardless of whether
the associated ticket is closed earlier.

## Backups

Backups follow the retention of the data they contain. A deletion propagates to
backups within one backup cycle (7 days); backups are not individually edited,
because editing a backup makes it unverifiable as a backup.

## Purge Verification

A purge is verified by a count query before and after. Where a purge affects more
than 1,000 records, the count is recorded in the compliance log.

## Exceptions

Legal hold suspends all deletion for the affected dataset. Where a dataset is
under legal hold, the retention schedule does not apply until the hold is lifted,
and the hold takes precedence over a customer deletion request.
