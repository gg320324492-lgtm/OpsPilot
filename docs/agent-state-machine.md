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

### 3.1 A failed tool call is not a failed run

**Decision: a `ToolCall` with `status='failed'` does not change the run's
status.** The run still reaches `COMPLETED` through `RESPONDING`. What changes
is that the failure can no longer be silent.

This needed deciding rather than assuming, so the argument is recorded.

**The defect that forced it.** Observed live against the Compose stack, on runs
`c7cd0d91`, `f37fb2ff` and `cf8d3b1f`. Each drove the golden path, had its
`billing.issue_refund` refused with `invalid_state` (the transaction was already
refunded, so the refusal was the idempotency contract working correctly), and
each reported:

```
status = completed   failure_reason = null   citations = 5
  crm.get_customer          executed
  billing.get_invoice       executed
  billing.list_transactions executed
  billing.issue_refund      FAILED   error=invalid_state   result=null
customer_reply: "We confirmed the duplicate charge of $129.00 … and have
                 refunded the extra transaction (TX-88219)."
```

The refusal was right. The defect was everything around it: the reply asserted
money that had not moved, and the worker's log for the entire run contained
exactly one line — the boot line.

**Why the run stays `COMPLETED`.** §3's rule is "did OpsPilot finish the job",
and the question that actually distinguishes the cases is *not* whether a tool
returned an error. It is whether the tool returned an **answer**. A tool that
answers — even "no", even `invalid_state`, even `not_found` — has told the run
something true, and the agent is equipped to reason about it. Routing that to
`FAILED` would mean the system could not tell a customer "that invoice does not
exist", which is precisely the job it was asked to do.

**The read/write split, which is the real content of the decision.** "Tool
returned an error" is two different things, and the permission level is what
tells them apart:

| Failure | Run status | Reply | Why |
|---|---|---|---|
| `READ` → `not_found` | `COMPLETED` | not escalated | A fact about the world. The agent is *supposed* to see it. |
| `READ` → `invalid_state` | `COMPLETED` | not escalated | Same: an answer, just an unexpected one. |
| Write of any level → refused | `COMPLETED` | **escalated** | An action that was supposed to happen and did not. The customer may have been told it would. |
| Any tool → `mcp_unavailable` | **`FAILED`** | no reply composed | The *absence* of an answer. Already routed here; see §3. |

The read/write line is drawn from `TOOL_REGISTRY`'s `permission`, not from a
hard-coded tool list and not from the error code, so a second `SAFE_WRITE` tool
gets the rule for free and a reclassified tool cannot silently flip behaviour
(`tool-permissions.md` §2).

**Why not a new state.** There is no `escalated` among the nine states, and
adding one would be worse than the silence. `COMPLETED`-with-a-failed-call is
not a lifecycle the run failed to reach; it is a `COMPLETED` run whose
*outcome* includes an action that did not happen — and `ctx.escalated` already
carries that bit, because it is what the API already returns beside the reply
body. The nine states describe the machine; the escalation describes the
result. A tenth state would have required every consumer to learn when to treat
it as terminal.

**What "not silent" now means, concretely.** Four things, each pinned by
`tests/integration/test_failed_tool_call_visibility.py`:

1. **The reply prompt states the failure in words.** It previously said only
   `Actions taken: billing.issue_refund=failed`, which a model reads as a refund
   that happened. It now says the named actions "did not complete and had no
   effect", tells the model to treat each as something that did *not* happen,
   and forbids claiming a completion that did not occur.
2. **A failed write escalates the run's reply**, so `customer_reply.escalated`
   is `true` — the live defect reduced to a single false field.
3. **The failure is logged at `WARNING`** with run id, tool name, error code
   and permission, and written as a `tool_failed` audit event rather than an
   `ok: false` payload on `tool_executed`.
4. **The API reports `failed_tool_calls`** (`api-contract.md` §3), so the one
   endpoint an operator reads cannot show a completed run with a reply over a
   silently-failed write.

**What this does not fix.** The prompt tells the model not to claim an effect
that did not happen; it cannot make a provider obey. On the default `fake`
provider the reply is a recorded fixture replayed verbatim
(`evals/datasets/fixtures/duplicate_charge.json`), so it still asserts the refund
regardless of what the prompt says — verified live after this change. The
correctness of a live reply therefore depends on the model *composing* from a
truthful trace. A structural guarantee (the runtime refusing to emit a reply
asserting an unperformed effect) is not available without parsing model output,
and is recorded here as unaddressed rather than claimed as fixed.

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

A run may execute at most `MAX_STEPS` plan/execute rounds (`24` by default —
`MAX_STEPS` in `settings.py`, which is the only definition; the worker passes it
onto every `RunContext` it builds). Exceeding it transitions to `FAILED` with
`max_steps_exceeded`. This exists because the failure mode of an agent loop is
not a crash, it is a loop — and an unbounded loop against a tool that mutates
state is the expensive kind. Phase 1 has no retry logic, so the budget is
generous on purpose; Phase 2's retry work will revisit it.
