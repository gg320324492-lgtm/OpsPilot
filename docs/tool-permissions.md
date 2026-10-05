# Tool Permission Model

This document specifies how OpsPilot decides whether a tool may execute. It is
the load-bearing document for the project's central claim: **the model cannot
cause a side effect.**

Implementation: `src/opspilot/domain/permissions.py`, `domain/tools.py`.
Enforcement point: `src/opspilot/agents/runtime.py`, `_gate_and_execute()`.

---

## 1. The three levels

```python
class Permission(str, Enum):
    READ             = "read"
    SAFE_WRITE       = "safe_write"
    HIGH_RISK_WRITE  = "high_risk_write"
```

| Permission | May execute automatically? | Audited? | Requires approval? |
|---|---|---|---|
| `READ` | yes | yes | no |
| `SAFE_WRITE` | yes | yes, **mandatory** | no |
| `HIGH_RISK_WRITE` | **never** | yes | **yes — a persisted, human-decided `ApprovalRequest`** |

Three levels, not five. A finer taxonomy (e.g. `READ_SENSITIVE`,
`EXTERNAL_WRITE`, `MONEY_MOVEMENT`) was considered and rejected for Phase 1:
every additional level is a decision someone has to make correctly at
registration time, and Phase 1 has exactly one high-risk tool. When a second one
appears in Phase 2, the classification question — *what makes this risky?* — will
have two real data points to answer it. Inventing four levels from one example
produces a taxonomy that has to be redone.

## 2. Where permissions come from

Permissions are **static, in code, at the tool's registration site.** Not in
config, not in a database row, not in a prompt, not inferred from the model's
description of what it intends to do.

```python
# src/opspilot/domain/tools.py

TOOL_REGISTRY: Final[dict[str, ToolSpec]] = {
    "knowledge.search": ToolSpec(
        name="knowledge.search",
        permission=Permission.READ,
        server="internal",
        ...
    ),
    "crm.get_customer": ToolSpec(..., permission=Permission.READ, server="crm"),
    "crm.get_account": ToolSpec(..., permission=Permission.READ, server="crm"),
    "crm.get_subscription": ToolSpec(..., permission=Permission.READ, server="crm"),

    "billing.get_invoice": ToolSpec(..., permission=Permission.READ, server="billing"),
    "billing.list_transactions": ToolSpec(..., permission=Permission.READ, server="billing"),
    "billing.issue_refund": ToolSpec(
        name="billing.issue_refund",
        permission=Permission.HIGH_RISK_WRITE,
        server="billing",
        requires_idempotency_key=True,
        ...
    ),

    "issues.search": ToolSpec(..., permission=Permission.READ, server="issues"),
    "issues.create": ToolSpec(..., permission=Permission.SAFE_WRITE, server="issues"),
}
```

### 2.1 Why static rather than configurable

A permission stored in a mutable store is a permission that a sufficiently
creative bug can widen. The threat model is not "an attacker edits the database"
— if an attacker can write to the database, the game is over for other reasons.
The threat model is **the agent's own influence surface**: prompt text, retrieved
documents, model output, and tool arguments. None of those can reach a Python
`Final` dict.

Stating it as a rule that CI can check:

> No code path may construct a `ToolSpec` whose `permission` derives from a
> model output, a retrieved document, an HTTP request body, or a database value.
> `tests/security/test_permission_immutability.py` asserts the registry equals a
> hard-coded expected mapping.

The registry is compared against a literal in a test, so *any* change to a
permission — including an accidental one — is a red test that a human must
justify in the diff.

## 3. The execution gate

Every tool call passes through all five stages, in order. There is no early-exit
shortcut for "trusted" callers, and `ToolGateway` is not exposed to `agents/`
except through this function.

```python
async def _gate_and_execute(call: ToolCall, run: AgentRun) -> ToolCall:
    # GATE 1 — schema validation
    #   The arguments were already parsed into a Pydantic model when the
    #   proposal was accepted. Invalid arguments never reach here; if they do,
    #   this is a programming error and raises.
    spec = validate_tool_call(call)

    # GATE 2 — registry lookup
    #   `spec` is None -> the model proposed a tool that does not exist.
    #   Recorded as a rejected ToolCall. Never dispatched.
    if spec is None:
        return reject(call, reason="unknown_tool")

    # GATE 3 — permission lookup
    permission = spec.permission          # static, from code

    # GATE 4 — policy engine
    #   Deterministic business rules: amount ceilings, idempotency pre-check,
    #   state preconditions (e.g. "cannot refund an already-refunded
    #   transaction" is enforced *here* as a fast fail and *again* at the MCP
    #   server as the authoritative check).
    decision = evaluate_policy(spec, call, run)
    if decision.denied:
        return reject(call, reason=decision.reason)

    # GATE 5 — approval gate
    if permission is Permission.HIGH_RISK_WRITE:
        if not has_approved_approval(call):        # <- a DB read
            park_run_awaiting_approval(call, run)  # <- raises RunParked
        # control only reaches here on the resume pass, and only because a
        # row exists with status='approved' pointing at THIS tool_call.id

    # EXECUTE
    result = await gateway.call_tool(spec.name, call.arguments)

    # AUDIT — always, for every permission level including READ
    record_audit(run, call, result)
    return call
```

### 3.1 Why `has_approved_approval` reads the database

It would be cheaper to keep an in-memory flag on the run object. It is done as a
database read on purpose, because the approval may have been granted by a
completely different process (the API, in response to a human clicking a button
in the dashboard) while the worker was not running. An in-memory flag would make
approval work only while the worker stayed alive, which is exactly the
crash-during-approval case Phase 1 must survive.

The check is bound to `tool_call.id`, not to `run.id`. This matters: approving a
refund for `TX-88219` must not authorise a *later* proposal to refund a different
transaction. One approval authorises one specific call with the specific
arguments the human was shown.

### 3.2 What the approver sees

The approval record snapshots the arguments as they will be executed. If the
arguments could be mutated after approval, the human would be approving
something other than what runs. So `ToolCall.arguments` is immutable once the
`ApprovalRequest` is created — enforced by a test that attempts to alter them
between approval and execution and requires `ApprovalArgumentsChanged`.

## 4. Idempotency

`billing.issue_refund` requires an `idempotency_key` (schema-enforced: a
`billing.issue_refund` call without one fails gate 1). The key is derived
deterministically from the run and the intended effect:

```python
idempotency_key = f"refund:{run.id}:{transaction_id}"
```

Not a random UUID — a random key would make every call unique and defeat the
purpose. Not derived from a timestamp — then a retry five minutes later would be
a new refund.

The key is enforced in two places, deliberately:

1. **At gate 4** (policy): if a `ToolCall` with this key already has status
   `executed` for this run, the gate short-circuits and returns the recorded
   result. This is fast and keeps the trace coherent.
2. **At the MCP server**: `billing.issue_refund` keeps a `(key -> refund_id)`
   map in its own store and returns the existing `refund_id` rather than
   inserting a second refund. This is authoritative. Gate 4 is an optimisation;
   the MCP server is the guarantee, because it is the only place that can
   actually prevent a duplicate row.

The test that matters is `tests/integration/test_refund_idempotency.py`: it
calls the MCP server directly, twice, with the same key, and asserts one refund
exists with two audit entries. This test does not go through the agent at all,
so it holds even if the agent loop is rewritten.

## 5. What the model can and cannot do

Given this design, here is the honest ledger of model influence:

| The model can | The model cannot |
|---|---|
| Choose which registered tool to propose | Invent a tool name that passes gate 2 |
| Supply arguments for that tool | Supply arguments that skip gate 1 validation |
| Propose a `HIGH_RISK_WRITE` tool | Cause it to execute without an approval row |
| Read retrieved text containing instructions | Make those instructions change gate 3's permission |
| Produce a `reason` string shown to the approver | Make the approver's decision automatic |
| Refuse to propose anything, and escalate | Reach `COMPLETED` while a required gate was skipped |

The right-hand column is what "the model proposes, deterministic code decides"
means concretely. The tests in `tests/security/` each attempt one row of it.

## 6. The CI gate

`tests/security/test_invariants.py` runs these as queries against the assembled
test database after the full integration suite:

```python
def test_no_unapproved_high_risk_execution(db):
    n = db.scalar(select(func.count()).select_from(ToolCall).where(
        ToolCall.permission == Permission.HIGH_RISK_WRITE,
        ToolCall.status == ToolStatus.EXECUTED,
        ~exists().where(
            (ApprovalRequest.tool_call_id == ToolCall.id) &
            (ApprovalRequest.status == ApprovalStatus.APPROVED)
        )
    ))
    assert n == 0
```

Four invariants, each `== 0`:

| Invariant | Violation |
|---|---|
| Unapproved `HIGH_RISK_WRITE` executions | 0 |
| Executions of unregistered tools | 0 |
| Executions with schema-invalid arguments | 0 |
| Duplicate refund side effects for one idempotency key | 0 |

These are not targets to trend toward. A single violation is a red CI run.
