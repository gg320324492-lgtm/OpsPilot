"""Tool permissions, tool specifications, call status, and the static registry.

Responsibility: define what a tool *is* to OpsPilot -- its name, which MCP
server it lives on, whether it needs an idempotency key, and, most importantly,
its permission level -- and hold the registry that is the single source of truth
for those facts.

Layer: ``domain``. Imports only the standard library and Pydantic.

The security argument lives here. Permissions are assigned in **static code at
the registration site** -- never in config, a database row, a prompt, or model
output. The threat model is the agent's own influence surface (prompt text,
retrieved documents, model output, tool arguments); none of those can reach a
Python ``Final`` dict. ``tests/security/test_permission_immutability.py``
asserts the registry equals a hard-coded expected mapping, so any change to a
permission is a red test a human must justify. See ``docs/tool-permissions.md``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Permission(StrEnum):
    """How a tool may execute. Three levels, not five -- see §1 of the spec."""

    READ = "read"
    SAFE_WRITE = "safe_write"
    HIGH_RISK_WRITE = "high_risk_write"


class ToolCallStatus(StrEnum):
    """The lifecycle of a single tool call (the ``tool_calls.status`` column)."""

    PROPOSED = "proposed"
    REJECTED = "rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTED = "executed"
    FAILED = "failed"


class ToolSpec(BaseModel):
    """The static description of a registered tool.

    ``permission`` is the load-bearing field and is set by hand at the
    registration site. ``requires_idempotency_key`` is schema-enforced at gate 1
    for tools such as ``billing.issue_refund``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    permission: Permission
    server: str
    description: str = ""
    requires_idempotency_key: bool = False
    requires_approval: bool = False


# The Phase 1 registry. Transcribed from docs/tool-permissions.md §2 so the
# permissions are visible in one place; these are the tools the MCP servers in
# docs/architecture.md §8 expose. Reviewing a diff to this dict is how a
# permission change is caught.
TOOL_REGISTRY: Final[dict[str, ToolSpec]] = {
    "knowledge.search": ToolSpec(
        name="knowledge.search",
        permission=Permission.READ,
        server="internal",
        description="Semantic search over indexed policy documents.",
    ),
    "crm.get_customer": ToolSpec(name="crm.get_customer", permission=Permission.READ, server="crm"),
    "crm.get_account": ToolSpec(name="crm.get_account", permission=Permission.READ, server="crm"),
    "crm.get_subscription": ToolSpec(
        name="crm.get_subscription", permission=Permission.READ, server="crm"
    ),
    "billing.get_invoice": ToolSpec(
        name="billing.get_invoice", permission=Permission.READ, server="billing"
    ),
    "billing.list_transactions": ToolSpec(
        name="billing.list_transactions", permission=Permission.READ, server="billing"
    ),
    "billing.issue_refund": ToolSpec(
        name="billing.issue_refund",
        permission=Permission.HIGH_RISK_WRITE,
        server="billing",
        description="Refund a transaction. Never automatic; requires human approval.",
        requires_idempotency_key=True,
        requires_approval=True,
    ),
    "issues.search": ToolSpec(name="issues.search", permission=Permission.READ, server="issues"),
    "issues.create": ToolSpec(
        name="issues.create",
        permission=Permission.SAFE_WRITE,
        server="issues",
        description="Open an internal issue. Automatic but always audited.",
    ),
}


class _PaginationArgs(BaseModel):
    """Arguments for the search/list tools that take a bounded page size."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class KnowledgeSearchArgs(BaseModel):
    """Arguments for ``knowledge.search``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RefundArgs(BaseModel):
    """Arguments for ``billing.issue_refund``.

    ``model_config`` forbids unknown fields so an unparseable call is rejected at
    gate 1 rather than silently ignored. The arguments that are *not* here --
    notably ``idempotency_key`` -- are deliberately absent: the key is derived by
    the policy engine from the run and the transaction
    (``docs/tool-permissions.md`` §4), never supplied by the model, so accepting
    one would let a proposal pick a key that defeats the duplicate check.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1)
    # A number, matching the MCP server's ``issue_refund(amount: float, ...)``
    # signature (``mcp_servers/billing/server.py``). The gate sends exactly what
    # the server declares; the policy engine still compares it as a ``Decimal``
    # (``_as_decimal`` accepts a ``float`` without going through a string), so
    # money rules do not acquire binary-roundoff behaviour.
    amount: float
    currency: str = Field(default="USD", min_length=1, max_length=8)
    reason: str = Field(default="")
    # The amount charged on the transaction, when the caller knows it. Gate 4
    # uses it for the "refund exceeds the charge" fast fail; it is optional
    # because the *authoritative* check is the MCP server, which reads the
    # transaction itself (``docs/tool-permissions.md`` §4). This is a fast fail,
    # not the guarantee.
    transaction_amount: float | None = None


class CustomerLookupArgs(BaseModel):
    """Arguments shared by the CRM lookups keyed on a customer/account id."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)


class InvoiceLookupArgs(BaseModel):
    """Arguments for ``billing.get_invoice``."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(min_length=1)


class TransactionListArgs(BaseModel):
    """Arguments for ``billing.list_transactions``."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)


class IssueSearchArgs(BaseModel):
    """Arguments for ``issues.search``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class IssueCreateArgs(BaseModel):
    """Arguments for ``issues.create``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


# Gate 1's schema surface: the Pydantic model each tool's arguments are parsed
# into. Keyed by the same names as ``TOOL_REGISTRY`` and asserted against it by
# ``tests/unit/test_permissions.py``, so a new registry entry without a schema
# (or the reverse) is a red test rather than a call that reaches the gateway
# unparsed.
TOOL_ARGUMENT_SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "knowledge.search": KnowledgeSearchArgs,
    "crm.get_customer": CustomerLookupArgs,
    "crm.get_account": CustomerLookupArgs,
    "crm.get_subscription": CustomerLookupArgs,
    "billing.get_invoice": InvoiceLookupArgs,
    "billing.list_transactions": TransactionListArgs,
    "billing.issue_refund": RefundArgs,
    "issues.search": IssueSearchArgs,
    "issues.create": IssueCreateArgs,
}


class UnknownToolError(ValueError):
    """Gate 1/2: the proposed tool name is not in the static registry.

    A ``ValueError`` subclass so a pump that only knows how to fail closed still
    catches it; the runtime catches it by this name at gate 2 and records the
    call as ``rejected`` (``reason='unknown_tool'``) rather than letting it
    propagate as a crash.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"tool {name!r} is not registered")


class InvalidArgumentsError(ValueError):
    """Gate 1: the arguments do not parse into the tool's Pydantic model.

    Carries the underlying ``ValidationError`` so a test can assert *which*
    field was wrong, and a message a trace can show.
    """

    def __init__(self, name: str, error: ValidationError) -> None:
        self.name = name
        self.error = error
        super().__init__(f"invalid arguments for {name!r}: {error.error_count()} problem(s)")


def get_tool_spec(name: str) -> ToolSpec | None:
    """Return the registered spec for ``name``, or ``None`` if unregistered.

    Gate 2: a model proposing a tool that is not here is rejected and never
    dispatched. The lookup is a plain read of the ``Final`` registry, so the
    answer cannot be influenced by a prompt, a document or a model output.
    """
    return TOOL_REGISTRY.get(name)


def validate_tool_call(
    name: str, arguments: dict[str, Any]
) -> tuple[ToolSpec | None, dict[str, object]]:
    """Gate 1: validate arguments against the tool's schema.

    Returns ``(spec, parsed_arguments)``. ``spec`` is ``None`` when the tool is
    not registered -- gate 1 cannot validate a schema it does not have, and
    letting gate 2 report the unknown name keeps the two gates' duties distinct.
    ``parsed_arguments`` is the model-dump of the validated arguments (defaults
    filled, unknown fields absent), which is what gets persisted as
    ``ToolCall.arguments`` and snapshotted for the approver -- so the row records
    exactly what will execute.

    Raises:
        InvalidArgumentsError: If the tool is registered and the arguments do not
            parse into its argument model. This is a *result*, caught by the
            runtime and recorded as a rejected tool call, not a crash: the model
            supplied bad arguments, which is expected traffic, not a programming
            error (``docs/tool-permissions.md`` §3, gate 1).
    """
    spec = get_tool_spec(name)
    if spec is None:
        return None, {}
    schema = TOOL_ARGUMENT_SCHEMAS[name]
    try:
        parsed = schema.model_validate(arguments)
    except ValidationError as exc:
        raise InvalidArgumentsError(name, exc) from exc
    return spec, parsed.model_dump()
