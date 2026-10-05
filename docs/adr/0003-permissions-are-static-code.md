# ADR-0003 — Tool permissions are static code, not configuration

**Status:** Accepted (Phase 1)

## Context

Each tool needs a risk level (`READ`, `SAFE_WRITE`, `HIGH_RISK_WRITE`) that
decides whether it may execute automatically. There are three obvious places to
put it:

1. A column in a `tool_definitions` table, editable from an admin UI.
2. A YAML/JSON config file loaded at startup.
3. A Python literal in the tool registry, in source.

Option 1 is what an enterprise product would eventually build: operators can
reclassify a tool without a deploy. Option 2 is a middle ground that is friendlier
than source to non-engineers. Option 3 is the least flexible.

The threat model decides this. It is **not** "an attacker with database access" —
at that point the game is over for unrelated reasons. It is **the agent's own
influence surface**: prompt text, retrieved documents, model output, and tool
arguments. Those four channels are where an injection lives.

The question becomes: can anything flowing through those channels reach a
permission value? In option 3 the answer is structurally no — a `Final` dict
literal cannot be written to by a string that came from a retrieved Markdown
file. In options 1 and 2 the answer is "no, as long as no code path mistakenly
wires an untrusted value into the permission lookup", which is a much weaker
guarantee and is the exact shape of the bug class this project exists to
demonstrate it prevents.

There is a second consideration: the classification decision ("is this risky?")
is a judgement that deserves a code review. A one-line YAML diff that reclassifies
`billing.issue_refund` to `SAFE_WRITE` is a change that could pass a hurried review.

## Decision

Permissions are Python literals in `src/opspilot/domain/tools.py`:

```python
class Permission(str, Enum):
    READ = "read"
    SAFE_WRITE = "safe_write"
    HIGH_RISK_WRITE = "high_risk_write"

TOOL_REGISTRY: Final[dict[str, ToolSpec]] = {
    "billing.issue_refund": ToolSpec(
        name="billing.issue_refund",
        permission=Permission.HIGH_RISK_WRITE,
        requires_idempotency_key=True,
        ...
    ),
    ...
}
```

And the registry is asserted against a hard-coded literal in
`tests/security/test_permission_immutability.py`:

```python
EXPECTED_PERMISSIONS = {
    "knowledge.search": Permission.READ,
    "crm.get_customer": Permission.READ,
    "crm.get_account": Permission.READ,
    "crm.get_subscription": Permission.READ,
    "billing.get_invoice": Permission.READ,
    "billing.list_transactions": Permission.READ,
    "billing.issue_refund": Permission.HIGH_RISK_WRITE,
    "issues.search": Permission.READ,
    "issues.create": Permission.SAFE_WRITE,
}

def test_registry_matches_expected():
    assert {n: s.permission for n, s in TOOL_REGISTRY.items()} == EXPECTED_PERMISSIONS
```

Downgrading a permission is therefore always two coordinated edits — the
registry and the literal — and the diff shows the old and new value side by side
in a file whose name says `security`. A reviewer cannot miss it.

## Consequences

**Good.** The influence surface is closed by construction rather than by
convention. Every permission change is visible in code review. There is no second
source of truth to diverge from `ToolSpec`.

**Cost.** No runtime reclassification. If an operator needs to disable
`billing.issue_refund` during an incident, Phase 1 requires a code change and a
restart.

That cost is real and is addressed — not by weakening this decision, but by a
separate, merge-safe control: `OPSPILOT_TOOL_DENYLIST`, an environment variable
holding tool names to refuse. A denylist can only *remove* capability, never
grant it, so an unset or malformed value defaults to "nothing is denied" and
there is no way for it to widen permissions. The asymmetry is the point:
config may take away, only code may give.

**Snapshotting consequence.** `ToolCall.permission` records the permission in
force at call time, so a future reclassification does not rewrite history. The
registry is the truth for new calls; the column is the record of past ones.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| `tool_definitions` table | Creates a mutable path to a security-critical value, reachable by any code that can write a row. Solves a Phase 3 problem at a Phase 1 cost. |
| YAML config file | Same shape with fewer safeguards: no type checking, no enum, and a diff that a reviewer skims. |
| Permission derived from the MCP server's own declared metadata | Makes the remote server authoritative over our policy. A compromised or misbehaving server could declare `billing.issue_refund` as read-only. |
| Permission inferred from the model's description of the action | The model would classify its own risk. This is precisely the failure mode the project is built to prevent. |
| A UI-managed policy with an approval workflow for changes | Correct at a scale this is not yet at. Deferred to Phase 3 with the rest of the change-management story. |
