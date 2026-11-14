# OpsPilot — Core architectural principles and their enforcement

`docs/milestones.md` §M0 requires that the core architectural principles are
"stated with their enforcement mechanism, not as slogans". This document is
that enumeration. Each principle below gives three things: the statement, the
mechanism in the repository that enforces it (a file, a test, or an ADR that
can be grepped), and the shape the failure takes when the principle is
violated — because a principle whose violation is undetectable is a slogan.

The five principles are not independent. The first makes the second possible:
if the model could execute directly, no deterministic policy downstream could
be trusted to catch it. The third makes the first two auditable after the
fact, and the fourth and fifth bound what the model and the developers can
change.

---

## P1 — The model proposes; deterministic code decides; humans approve risky writes

**Statement.** The language model cannot cause a side effect. Its output is an
untrusted proposal, validated into a Pydantic value object, which must pass
five deterministic gates in a fixed order before any tool executes — and a
`HIGH_RISK_WRITE` tool never executes without a persisted, human-decided
approval row. No prompt, retrieved document, or model output can change this.

**Enforcement.**

- The gates live in one linear function, `agents/runtime.py::`
  `_gate_and_execute` (`src/opspilot/agents/runtime.py:419`), written
  top-to-bottom in the documented order so a reviewer can verify the sequence
  by reading it. `docs/architecture.md` §1 is the specification it implements.
- The order is pinned by `tests/security/test_gate_order.py::`
  `test_the_five_gates_run_in_the_documented_order`, which drives proposals
  that *later* gates would also have refused and asserts the recorded refusal
  is the *earliest* gate's.
- The approval invariant is checked as a database query, not a helper unit
  test: for every `ToolCall` with permission `HIGH_RISK_WRITE` and status
  `executed`, there exists an `ApprovalRequest` for that call with status
  `approved`. `tests/security/test_invariants.py::`
  `test_no_unapproved_high_risk_execution` and
  `tests/security/test_approval_rows.py` hold it, including the replay branch
  of gate 4 (`tests/security/test_gate4_replay_branch.py`), which is the one
  sanctioned exception and writes `executed` with no new approval.
- The end-to-end attack is covered by `tests/security/test_prompt_injection.py`:
  a provider scripted to *fully comply* with a corpus-borne injection
  (`knowledge/ignore-instructions.md`, demanding a $10,000 refund with no
  approval) still parks at gate 5 and issues nothing. The eval-side delivery of
  the same case is `tests/evals/test_injection_delivery.py`.

**Failure shape.** If a gate is reordered, skipped, or given a second caller,
a proposal reaches execution without passing the gates ahead of it — the
ordering test fails on the recorded `rejection_reason`, or the invariant query
finds an `executed` high-risk call with no matching `approved` row. If the
approval binding weakens (an approval reused across calls), the reuse tests in
`tests/integration/test_gates.py` and `tests/security/test_approval_bypass.py`
fail.

---

## P2 — Deterministic policy control takes precedence over model judgement

**Statement.** Business rules that must hold regardless of what the model
believes — the tool denylist, the refund ceiling, the
amount-versus-transaction check, idempotency pre-check — are evaluated in
`domain/policies.py`, *before* the approval gate, on data that has already
passed schema validation. The model's reasoning, confidence or stated
justification is never an input to them.

**Enforcement.**

- `evaluate_policy` (`src/opspilot/domain/policies.py:265`) is a pure function
  over the validated proposal and registry metadata. Its rejection reasons
  (`REASON_TOOL_DENYLISTED`, `REASON_REFUND_ABOVE_CEILING`,
  `REASON_REFUND_EXCEEDS_TRANSACTION`, `REASON_IDEMPOTENT_REPLAY`) are the
  documented vocabulary, and `tests/unit/test_the_failure_reasons_are_documented.py`
  keeps the documentation and the code from drifting apart.
- The denylist can only remove capability: `OPSPILOT_TOOL_DENYLIST`
  (`src/opspilot/settings.py:435`, parsed at `:508`) is consulted by
  `_denied_by_denylist` and there is no configuration path that adds or
  widens a permission — ADR-0003 is the argument for why that asymmetry is
  structural, not incidental.
- Scope of the rules is tested, not assumed:
  `tests/security/test_gate4_rule_scope.py` proves the runtime cannot
  manufacture a `refund_exceeds_transaction` refusal for a validated proposal
  (the charged amount cannot even be proposed), and
  `tests/unit/test_policies.py` pins the ceiling, denylist and
  Decimal-comparison behaviour.

**Failure shape.** A rule that moved into the model's prompt or a tool's own
code would stop producing the documented rejection reason on the `ToolCall`
row — the policy unit tests and the gate-4 scope tests fail, and the failure
reasons documented in `docs/tool-permissions.md` would no longer match what
the database records. A config change that *widened* rather than removed
capability has no spelling in `settings.py` to express it, so it would have to
be a code change, which is exactly what ADR-0003 forces into review.

---

## P3 — Every model call and every decision is traced and auditable

**Statement.** Every stage of a run, every model call with its provider-reported
usage, every tool call with its gate outcome, and every approval decision is
persisted as rows — `agent_runs`, `agent_steps`, `tool_calls`,
`approval_requests`, `audit_events` — so the run can be reconstructed after
the fact, including when it failed or parked.

**Enforcement.**

- The persistence models live in
  `src/opspilot/adapters/persistence/models.py` (`AgentRun:171`,
  `agent_steps` table at `models.py:223`, `ToolCall:245`,
  `ApprovalRequest:284`, `AuditEvent:380`). The `(run_id, sequence)`
  uniqueness constraint on `agent_steps` is the ordering guarantee the trace
  renders from.
- Model-call accounting is asserted per call, not sampled:
  `tests/agent/test_model_call_events.py` requires one `model_called` event
  per model call, checks the usage figures are the provider's reported ones
  rather than fabricated zeros, and that a failed run keeps its accounting.
- Tool-call visibility is tested from both directions:
  `tests/integration/test_failed_tool_call_visibility.py` requires a failed
  tool call to reach the model as a failure (not a success), to be logged, and
  to mark the run escalated; `tests/integration/test_mcp_unavailable_is_audible.py`
  requires an unreachable server to leave the failure in the row even when the
  worker path swallows the exception.
- The trace the API returns is checked against what the steps actually were:
  `tests/agent/test_trace_labels_are_read_from_real_steps.py`.

**Failure shape.** A stage that ran without writing its step row breaks the
per-call event tests; a swallowed exception that skips the audit write fails
the audibility tests; a trace rendered from labels instead of real steps fails
the label tests. In every case the symptom is a missing or contradictory row,
which is detectable by query — the same standard as P1.

---

## P4 — Tool capability is governed by a static registry and a remove-only denylist

**Statement.** The set of tools that exist, the server each lives on, and the
permission each carries are literals in `domain/tools.py` — never a database
row, never a config file, never anything a prompt or a retrieved document could
influence. On top of that, the runtime denylist may *remove* a tool by name
but can never add one or raise a permission.

**Enforcement.**

- `TOOL_REGISTRY` (`src/opspilot/domain/tools.py:93`) is the single source of
  truth. Gate 2 resolves proposals against it, so an invented tool name is
  refused before anything else happens;
  `tests/integration/test_gates.py::`
  `test_unknown_tool_is_rejected_at_gate_2_and_never_dispatched` and
  `tests/security/test_invariants.py::`
  `test_no_execution_of_unregistered_tools` hold that.
- The registry is pinned as a literal by
  `tests/security/test_permission_immutability.py`, which asserts the whole
  table against a hand-transcribed expected literal — any change to a
  permission, a server binding, or an idempotency requirement fails the test
  and therefore lands in code review, which is the mechanism ADR-0003
  prescribes for permission changes.
- Dispatch itself is split by the registry, not by trial and error:
  `adapters/tools/composite_gateway.py::CompositeToolGateway` reads
  `TOOL_REGISTRY[name].server` and asks exactly one half (in-process or MCP),
  which `tests/integration/test_composite_gateway.py` pins by recording what
  each half was asked.
- The remove-only property is tested at the policy layer
  (`tests/unit/test_policies.py::test_denylisted_tool_is_denied`,
  `test_unset_denylist_denies_nothing`) and exercised through the gates in
  `tests/security/test_gate_order.py`, which sets
  `OPSPILOT_TOOL_DENYLIST=billing.issue_refund,...` and confirms gate 4
  refuses before gate 5 is reached.

**Failure shape.** A tool added outside the registry is unresolvable at gate 2
and can never execute — the failure is immediate and total, not gradual. A
permission softened in the registry fails the immutability test and its diff
is a reviewed code change. A denylist that could add capability is not
expressible in the current settings surface; making it so would be a code
change to `policies.py`, caught by its tests.

---

## P5 — Eval-driven and reproducible: the corpus judges the change, the holdout stays held out

**Statement.** Behaviour claims are measured against committed datasets with a
deterministic runner and fixed metrics — the safety metric
`unsafe_execution_count` must be exactly zero — and the holdout set that
judges a finished change is never part of the run the change was tuned on.

**Enforcement.**

- The harness and metrics live in `src/opspilot/evals/runner.py` and
  `src/opspilot/evals/metrics.py`; `docs/evals.md` is their specification, and
  `tests/evals/test_metrics_definitions.py` keeps the two in agreement.
- The holdout is opt-in at the CLI level, not by convention:
  `src/opspilot/evals/__main__.py:70` defines `_OPT_IN_DATASETS =`
  `("tool_selection_holdout",)` and the default run cannot reach it;
  `docs/evals.md` §`tool_selection_holdout.jsonl` states the rule — run once,
  after tuning, against the baseline configuration.
- The safety expectation is declared and guarded:
  `tests/evals/test_the_safety_expectations_are_declared.py` verifies every
  dataset case's expectation is declared in the metrics table and that the
  guard is not vacuously green; `unsafe_execution_count`
  (`src/opspilot/evals/metrics.py:686`) is an absolute count that must stay 0.
- Committed results are recomputable:
  `tests/evals/test_committed_results_are_recomputable.py` and
  `tests/evals/test_the_readme_eval_figures_match_the_committed_results.py`
  keep the published numbers honest.
- The datasets were written *before* the implementation they judge (an M0
  decision recorded in `docs/progress.md` §"Decisions made during M0"), which
  is what keeps them a specification rather than a description.

**Failure shape.** A change that regresses safety produces a non-zero
`unsafe_execution_count` and the eval run fails. Tuning against the holdout is
prevented structurally: the default dataset list omits it, so fitting it by
accident requires an explicit `--dataset tool_selection_holdout` flag, which is
the auditable act ADR-style decisions are recorded for. Published numbers that
drift from the committed results fail the agreement tests.

---

## How the principles map to the ADRs

| ADR | Principle it primarily serves |
|---|---|
| [ADR-0001](adr/0001-mcp-behind-a-tool-gateway.md) — MCP behind a tool gateway | P1, P4 |
| [ADR-0002](adr/0002-domain-owns-the-state-machine.md) — domain owns the state machine | P1, P2 |
| [ADR-0003](adr/0003-permissions-are-static-code.md) — permissions are static code | P2, P4 |
| [ADR-0004](adr/0004-sqlite-tests-postgres-production.md) — SQLite tests, Postgres production | P3, P5 |
| [ADR-0005](adr/0005-db-backed-worker-no-broker.md) — database-backed worker, no broker | P3 |

Two cross-cutting guards make the documentation itself trustworthy, which is
the precondition for everything above being checkable:

- `tests/unit/test_layering.py` enforces the dependency rule (domain imports
  no framework, ports import no implementations) as a static AST check, so
  the layering the principles rely on cannot erode silently.
- `tests/unit/test_doc_symbol_citations_point_at_the_definition.py` verifies
  that `Symbol:line` citations in the docs point at the symbol they name, so
  the pointers this document relies on do not rot.
