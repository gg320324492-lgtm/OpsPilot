# Agent Run State Machine

The run status is **one enum column**, never a set of booleans. This document is
the normative specification: the enum, the legal transitions, what each state
means, and the failure semantics.

Implementation: `src/opspilot/domain/runs.py`.
Enforcement: the transition table is data, and `AgentRun.transition_to()` is the
only way to change status — it raises `IllegalTransition` otherwise. There is no
setter.

---

## 1. States

```python
class RunStatus(str, Enum):
    RECEIVED         = "received"
    CLASSIFYING      = "classifying"
    RETRIEVING       = "retrieving"
    PLANNING         = "planning"
    EXECUTING        = "executing"
    WAITING_APPROVAL = "waiting_approval"
    RESPONDING       = "responding"
    COMPLETED        = "completed"
    FAILED           = "failed"
```

| State | Meaning | Is it terminal? | Is the worker holding the row? |
|---|---|---|---|
| `RECEIVED` | Run row exists; nothing done yet. The worker's claim predicate accepts this. | no | — |
| `CLASSIFYING` | A model call is determining the ticket category. | no | yes |
| `RETRIEVING` | Embedding + top-k search over `knowledge_chunks`. | no | yes |
| `PLANNING` | A model call is proposing the next action (`ProposedAction`). | no | yes |
| `EXECUTING` | A tool call is passing through the five gates, or has been dispatched. | no | yes |
| `WAITING_APPROVAL` | Parked on a persisted `ApprovalRequest`. **The worker has released the row.** | no | **no** |
| `RESPONDING` | A model call is composing the customer reply from the trace. | no | yes |
| `COMPLETED` | Terminal. The workflow reached a customer-visible outcome (refund executed, or escalation produced). | **yes** | — |
| `FAILED` | Terminal. `failure_reason` is set. | **yes** | — |

The distinction that costs the most to get wrong is `WAITING_APPROVAL`: it is the
only non-terminal state in which the worker is *not* holding the row. Every other
non-terminal state is a claim-and-work state.

## 2. Allowed transitions

```python
ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RECEIVED:         frozenset({CLASSIFYING, FAILED}),
    CLASSIFYING:      frozenset({RETRIEVING, FAILED}),
    RETRIEVING:       frozenset({PLANNING, FAILED}),
    PLANNING:         frozenset({EXECUTING, RESPONDING, FAILED}),
    EXECUTING:        frozenset({EXECUTING, WAITING_APPROVAL, RESPONDING, FAILED}),
    WAITING_APPROVAL: frozenset({EXECUTING, RESPONDING, FAILED}),
    RESPONDING:       frozenset({COMPLETED, FAILED}),
    COMPLETED:        frozenset(),
    FAILED:           frozenset(),
}
```

Read as a diagram, with the loops that matter:

```
                         ┌──────────┐
                         │ RECEIVED │
                         └────┬─────┘
                              ▼
                        ┌───────────┐
                        │CLASSIFYING│
                        └─────┬─────┘
                              ▼
                        ┌───────────┐
                        │RETRIEVING │
                        └─────┬─────┘
                              ▼
                        ┌───────────┐
                   ┌───>│ PLANNING  │
                   │    └─────┬─────┘
                   │          │
                   │          ├──────────────────┐
                   │          ▼                  │  no tool needed
                   │    ┌───────────┐            │
                   │    │ EXECUTING │            │
                   │    └─────┬─────┘            │
                   │          │                  │
                   │   ┌──────┴───────┐          │
                   │   │              │          │
                   │   ▼              ▼          │
                   │ READ/SAFE_WRITE  needs      │
                   │   │           approval      │
                   │   │              │          │
                   │   │              ▼          │
                   │   │      ┌─────────────────┐│
                   │   │      │ WAITING_APPROVAL││
                   │   │      └────────┬────────┘│
                   │   │               │         │
                   │   │        approved│rejected │
                   │   │               │  │      │
                   │   │               ▼  │      │
                   │   └───────> EXECUTING│      │
                   │               │      │      │
                   │               │      │      │
                   │               └──────┼──────┘
                   │                      │
                   └──────────────────────┤   (another planning round)
                                          ▼
                                    ┌───────────┐
                                    │ RESPONDING│
                                    └─────┬─────┘
                                          ▼
                                    ┌───────────┐
                                    │ COMPLETED │
                                    └───────────┘

  Any non-terminal state ─────────────> FAILED
```

### 2.1 Why these specific edges exist

- **`EXECUTING → EXECUTING` (self-loop).** The golden path calls several tools in
  sequence (`crm.get_customer`, then `billing.get_invoice`, then
  `billing.list_transactions`). Each is a separate plan→gate→execute round, and
  the run stays in `EXECUTING` for the tool-call segment. Modelling each tool call
  as its own state would multiply states without adding a single distinct
  invariant. The step-level detail lives in `AgentStep`/`ToolCall`, not in
  `RunStatus`.

- **`EXECUTING → WAITING_APPROVAL`.** The only way a `HIGH_RISK_WRITE` reaches
  execution. A parked `ApprovalRequest` is created in the same transaction as
  this transition, so the two cannot disagree.

- **`WAITING_APPROVAL → RESPONDING` (rejection path).** A rejected refund does
  not fail the run. The workflow's correct outcome is an escalation reply. This
  edge is the difference between a system that models an approval as a *gate*
  and one that models it as an *error*.

- **`PLANNING → EXECUTING` directly is *not* allowed.** Planning always routes
  through `EXECUTING` to touch a tool, and `EXECUTING` is where the five gates
  live. There is no edge from `PLANNING` to a tool.

- **`COMPLETED` and `FAILED` have empty successor sets.** `COMPLETED →
  EXECUTING` raises. This is asserted in `tests/unit/test_run_state.py` against
  the enum itself, not against a hand-written list, so adding a state without
  adding its transitions fails the test.

## 3. Failure semantics

`FAILED` is reachable from every non-terminal state. When entering it:

- `failure_reason` is set to a short machine-stable token (`interrupted`,
  `mcp_unavailable`, `schema_invalid`, `tool_error`, `max_steps_exceeded`), and
- an `AuditEvent(event_type='run_failed')` is written with the detail payload.

Failures that are *expected and handled* do not map to `FAILED`:

| Situation | Run status | Why |
|---|---|---|
| Refund rejected by a human | `COMPLETED` (via `RESPONDING`) | The system did its job. A human declined. |
| Policy forbids the refund | `COMPLETED` (via `RESPONDING`) | Same — the correct answer is an escalation reply. |
| Knowledge insufficient to answer | `COMPLETED` (via `RESPONDING`) | Abstention is a supported outcome, not an error. |
| MCP server unreachable after retry (Phase 2) | `FAILED` | The system could not complete the work it was asked to do. |
| Model returned a schema-invalid proposal twice | `FAILED` | Not recoverable within Phase 1's no-retry policy. |
| Worker crashed mid-step | `FAILED` (`interrupted`) at next boot | Honest about what Phase 1 does not do. **Not** `EXECUTING`: that state may hold an approval a human already granted, and failing it would discard their decision (`architecture.md` §5). |

The distinction in one line: **`FAILED` means OpsPilot did not finish the job;
`COMPLETED` means it did, even when the answer was "no".**

## 4. Persistence rules

- Every transition writes an `AgentStep(step_type='state_change')` row carrying
  `(from_status, to_status, at)`.
- The `agent_runs.status` update and the step insert happen in **one
  transaction**. A status change without its step is not observable.
- `started_at` is set on the first transition out of `RECEIVED`;
  `completed_at` is set on entry to `COMPLETED` or `FAILED`.

## 5. Claim predicate

The worker claims rows matching:

```sql
status IN ('received', 'classifying', 'retrieving', 'planning', 'executing', 'responding')
```

`waiting_approval`, `completed` and `failed` are absent by construction — they are
built from the enum rather than typed as literals:

```python
CLAIMABLE = frozenset(RunStatus) - {WAITING_APPROVAL, COMPLETED, FAILED}
```

so a new state defaults to *claimable* only if someone thinks about it. (The
default for a new state is "claimable", which is the safe direction: forgetting
to exclude a parked state causes duplicate work, and the integration test
`test_waiting_approval_is_not_claimed` catches it.)

## 6. Step budget

A run may execute at most `MAX_STEPS = 24` plan/execute rounds. Exceeding it
transitions to `FAILED` with `max_steps_exceeded`. This exists because the
failure mode of an agent loop is not a crash, it is a loop — and an unbounded
loop against a tool that mutates state is the expensive kind. Phase 1 has no
retry logic, so the budget is generous on purpose; Phase 2's retry work will
revisit it.
