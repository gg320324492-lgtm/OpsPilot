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
passed in the published Phase 2 review (the suite has grown since; the README
records the current count), a CI-enforced zero-violation invariant that every
executed high-risk write matches an approved approval request, and the
fail-closed property that adversarial retrieved text cannot move money without
a human. The same property was re-measured against a real model in November
2026: across a 15-case safety batch (13 live + 2 scripted injection),
**unsafe executions = 0** — both injection cases proposed the injected $10,000
refund and were refused by the amount check with no approval and no write.

Honest boundaries: third-party systems and the refund are simulated in-repo
mocks; security uses a single-operator token; the model's tool-selection
accuracy is measured openly and stays below the project's own acceptance bar
(development set 3/15 and 2/15 against a ≥10/15 gate, holdout 10/32), while
the safety gate held. Full detail lives in
[`docs/limitations.md`](limitations.md) and
[`docs/case-study-duplicate-charge.md`](case-study-duplicate-charge.md).
