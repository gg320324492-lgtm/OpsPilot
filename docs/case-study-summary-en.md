# Case-study summary

OpsPilot is an open-source AI operations agent — an independent personal
project, designed and built end to end by the author — that automates
duplicate-charge investigations while keeping refunds under exclusive human
control.

The problem is repetitive but sensitive: verify the invoice, compare
transactions, apply refund policy — before any money moves. OpsPilot does the
legwork: it ingests the ticket, retrieves policy with pgvector-backed RAG
(retrieved text is untrusted data), and queries CRM, Billing and Issues
through three real MCP servers. The model can only propose; between proposal
and external write stand five deterministic gates — schema validation,
registry lookup, permission lookup, policy engine, human approval — none of
which it can influence.

In the documented reference run, the agent detected two $129.00 charges two
seconds apart against one paid invoice, proposed a refund, and stopped at the
approval gate. The operator reviewed a deterministic explanation with
snapshot-at-park arguments, approved, and the worker executed the refund
exactly once under an idempotency key; a repeat approval was refused with 409.

Measurable results, all from the repository's own records: 1,365 Python tests
passed in the published Phase 2 review, a CI-enforced zero-violation invariant
that every executed high-risk write matches an approved approval request, and
the fail-closed property that adversarial retrieved text cannot move money
without a human.

Honest boundaries: third-party systems and the refund are simulated in-repo
mocks; security uses a single-operator token; real-model proposal quality is
measured openly and remains active work. Full detail lives in
[`docs/limitations.md`](limitations.md) and
[`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md).
