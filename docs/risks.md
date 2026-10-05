# Technical Risks and Requirement Conflicts

Written before implementation, so that the choices in M1–M9 can be checked
against them. Each risk has a mitigation and an honest statement of what remains
unmitigated after Phase 1.

---

## R1 — LangGraph's graph becomes the architecture

**Risk.** LangGraph wants to own control flow. If the state machine is expressed
as graph edges, then the allowed-transition table lives in library constructs,
the transition rules become untestable without the library, and a graph edit can
silently widen what is reachable. The project ends up as "a LangGraph app" and
Phase 2's changes are constrained by the library's model rather than the domain's.

**Mitigation.** `domain/runs.py` owns `RunStatus`, `ALLOWED_TRANSITIONS` and the
enforcement; it imports nothing but `enum` and Pydantic. LangGraph sits behind
`ports/orchestrator.py` in `adapters/orchestration/langgraph_adapter.py`, and the
adapter is built in M4 — *after* the runtime works on a linear adapter. The unit
test for legal and illegal transitions never imports LangGraph.

**Residual.** The adapter still translates domain states into graph nodes, so a
future contributor could add logic there. Guarded by a test asserting the
adapter does not import from `domain.policies` or `domain.permissions` — the
adapter may schedule work, it may not decide whether work is allowed.

---

## R2 — The fake provider makes the tests pass for the wrong reason

**Risk.** With `FakeModelProvider` driving every test, the suite proves the
plumbing works with a cooperative model. It proves nothing about whether a real
model will propose the right tools. Worst case, the tests encode the fake's
script and become a mirror.

**Mitigation.** Two things. First, the fake provider is a *replay* of recorded
real interactions, not invented scripts: `evals/datasets/fixtures/` holds
recorded provider responses for the golden path, captured once from a real run
and committed. Second, `tool_selection` and `classification` datasets are run
against a real provider in the manual `eval-live` job, and the README quotes that
run. The fake is for determinism in CI, not as evidence of quality.

**Residual.** CI cannot detect a real-model regression. That is by design (cost,
non-determinism) and is stated in [evals.md §5](evals.md).

---

## R3 — Approval becomes a rubber stamp

**Risk.** The human approval gate is a security control only if the human
actually reads it. A dashboard that shows a bare "Approve / Reject" with a
transaction id trains the operator to click Approve. The control then exists in
the schema and not in practice.

**Mitigation.** The approval payload is designed for the decision, not for the
record: company name, the model's reason, the deterministic `risk_explanation`
with the dollar amount, and the exact arguments. The UI shows the *deterministic*
risk line in a visually distinct element from the model's `reason`, so the
operator can tell which text is trustworthy. The eval's `safety` set includes
cases where the correct human answer is Reject, and the demo scenario in the
README shows a rejection path, not only an approval.

**Residual.** Phase 1 has one operator and no separation of duties — the same
token can create the ticket and approve the refund. This is a real weakness and
is listed in [limitations.md](limitations.md); it is Phase 3 work (roles,
approval spheres, break-glass).

---

## R4 — Idempotency that does not actually hold

**Risk.** The most expensive bug in this project is a duplicate refund from a
retried tool call. An idempotency key generated per *call* rather than per
*intended effect* is worthless; so is a key checked in application memory only.

**Mitigation.** Three independent layers, described in
[tool-permissions.md §4](tool-permissions.md): the key is derived
deterministically from `(run_id, transaction_id)`; a partial unique index on
`tool_calls.idempotency_key` makes a second executed call with the same key a
database error; and the MCP server keeps its own `UNIQUE` constraint on
`refunds.idempotency_key` and returns `replayed: true`. Each layer alone is
sufficient for the common case; the test calls the MCP server directly, twice,
bypassing the agent.

**Residual.** None identified for the single-run case. Cross-run idempotency
(two runs both refunding the same transaction) is handled by the MCP server's
`invalid_state` check on an already-refunded transaction, but the *concurrent*
version of that race is only covered because the store serialises writes; with a
real payment API this would need a conditional write, and that is noted for
Phase 2.

---

## R5 — The worker's restart behaviour hides failures

**Risk.** Marking mid-flight runs as `FAILED(interrupted)` on boot is simple and
honest, but if the worker restarts often, most runs fail and the failure looks
like an environment problem rather than a design choice.

**Mitigation.** The behaviour is documented in the state-machine doc and in the
README's limitations, and `failure_reason='interrupted'` is a distinct token from
real errors, so the dashboard can separate them. `WAITING_APPROVAL` runs are
never touched, so the golden path's parked state survives a restart — which is
the case that actually matters for the demo.

**Residual.** A run that crashes three steps into a five-step investigation loses
that work. Auto-resume needs a step journal and per-tool idempotency at a depth
Phase 1 does not build. Stated, not hidden.

---

## R6 — pgvector in tests, cosine-in-Python in SQLite

**Risk.** The test configuration substitutes an in-memory cosine implementation
for pgvector (ADR-0004). If the two disagree — normalisation, distance metric,
NaN handling — the tests pass and production is wrong. This is the classic
"tested on a different thing than we ship" failure.

**Mitigation.** The substitution happens behind `ports/vector_store.py`, so it is
one class, and it is covered by a **differential test**: the same fixed chunk set
and query vector are loaded into both the SQLite store and a real pgvector
instance (in the Postgres CI job), and the top-5 results are asserted equal,
including the similarity scores to 4 decimal places. A divergence is a red test,
not a production surprise.

**Residual.** The differential test runs only in CI's Postgres job, not locally.

---

## R7 — Scope creep through "small additions"

**Risk.** Each of these looks cheap: a second agent, a Slack notification, a
GraphQL endpoint, a reranker, a Redis cache, a real Stripe sandbox. Individually
defensible; collectively they are how a Phase 1 that was supposed to be reviewable
in an afternoon becomes a codebase nobody has read end to end.

**Mitigation.** `docs/architecture.md §11` and §13 list the excluded items and the
test for inclusion ("does the golden workflow require it"). The milestone plan
puts the golden workflow (M6) before the dashboard (M7), the evals (M8) and CI
(M9), so the tempting additions have a clear queue ahead of them. A feature not
required by the golden workflow gets questioned before it is added, and the
question is recorded as an ADR rather than answered in the moment.

**Residual.** This is a discipline problem, not a technical one, and no guard
enforces it. The cost of indulging it is a delayed Phase 1, which is the user's
call to make.

---

## R8 — The demo is not reproducible on a fresh clone

**Risk.** The README's GIF shows a workflow the reader cannot reproduce, because
it depends on a seeded database, an API key, or a machine-specific state.

**Mitigation.** Seed data is committed JSON. `docker compose up` plus
`opspilot-seed` produces the exact database the demo ran against. The
deterministic path (`--provider fake` with recorded responses) reproduces the
*same timeline* with no API key at all and is what the GIF is generated from — so
the artifact a reviewer sees is regenerable. The live-model path is documented
and optional.

**Residual.** The live run's exact wording will differ (model non-determinism).
The README shows the recorded path for the screenshot and says which is which.

---

## Requirement conflicts identified in the specification

### C1 — `issues.create` is `SAFE_WRITE`, but the golden path calls it optional

The specification lists "optionally create an issue" in the golden workflow, and
marks `issues.create` as `SAFE_WRITE` (auto-execute, audited). But an issue
creation is *externally visible*: it notifies a team. Auto-executing an external
notification sits uneasily beside a project whose thesis is "risky writes need
approval".

**Resolution.** Keep `SAFE_WRITE` for Phase 1, because the definition of risk
here is *irreversibility of a financial or customer-facing effect*, and an
internal tracker issue is neither. But record the tension: `risk_explanation` for
`issues.create` explicitly says "creates a visible internal issue". If Phase 2
adds issue escalation to on-call, that becomes a `HIGH_RISK_WRITE` and the
three-level model gets its first real second data point.

### C2 — "Refund executed once" vs. "worker may crash mid-execution"

The specification requires exactly-once refund execution and also permits
`FAILED(interrupted)` on worker restart. If the worker crashes *after* the refund
was executed but *before* the `ToolCall` row was committed as `executed`, the run
is marked interrupted while the money moved.

**Resolution.** The MCP server's `refunds.idempotency_key` unique constraint is
the authority, and the run's idempotency key is deterministic from
`(run_id, transaction_id)`. So a resumed retry — whenever Phase 2 adds it — hits
the server, gets `replayed: true` and the existing `refund_id`, and the trace is
corrected rather than duplicated. In Phase 1, the interrupted run is simply
failed and a human re-runs it; the second run produces a *different* `run_id` and
therefore a *different* idempotency key, so the MCP server's `invalid_state`
check on the already-refunded transaction is what prevents the double refund.
This is a real seam: the safety property holds, but through the transaction-state
check rather than through the key. Documented, and the integration test
`test_rerun_after_interrupt_does_not_double_refund` covers it.

### C3 — "Two real providers" vs. "the schedule"

The specification prefers both OpenAI and Anthropic adapters and also warns
against scope growth.

**Resolution.** The `ModelProvider` protocol is defined in M4 before either
adapter, and the fake provider is the first implementation. Both real adapters
share a single provider-agnostic prompt assembly and structured-output path, so
the second adapter is configuration plus a request-shape translation, not a
second design. If the schedule slips, the Anthropic adapter ships and the OpenAI
adapter is a thin addition to an already-proven interface — the risk is bounded
because the interface exists first.

### C4 — "No LLM-as-a-judge" vs. measuring reply quality

Nothing in the deterministic metric set measures whether the customer reply is
good: whether it is faithful to the trace, appropriately apologetic, free of
invented policy claims. The specification forbids LLM-as-a-judge in Phase 1.

**Resolution.** Accept the gap and declare it. The reply's *faithfulness* is
partially checkable structurally — the reply's `citations` field must be a subset
of the run's retrieved citations, which is a deterministic check and is metric
4's sibling. What is not checkable is tone. `limitations.md` says so, and the
README does not claim reply quality is measured.
