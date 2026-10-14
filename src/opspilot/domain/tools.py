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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


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
    """Arguments for the CRM lookups keyed on a customer/account id."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)


class CustomerResolveArgs(BaseModel):
    """Arguments for ``crm.get_customer``, which takes an email **or** an id.

    Not ``CustomerLookupArgs``, and that is the point. `docs/mcp-contracts.md` §1
    documents two accepted forms:

        { "customer_email": "billing@acme.example" }
        // or
        { "customer_id": "CUS-1001" }

        Exactly one of `customer_email` / `customer_id` must be given.

    ``mcp_servers/crm/server.py::get_customer`` implements exactly that -- both
    parameters, optional, with the "neither / both-disagreeing" cases answered
    ``validation_error``. Gate 1 did not: all three CRM lookups shared
    ``CustomerLookupArgs``, which declared only ``customer_id`` under
    ``extra="forbid"``. So a model that proposed the contract's own first
    example was refused at gate 1 before the tool ran -- which is what the live
    recording of 2026-10-15 shows happening
    (``docs/evidence/live-run-2026-10-15.json``).

    The "exactly one" rule is enforced here rather than left to the server so
    that a call carrying both identifiers is refused at the gate, not resolved
    by whichever the tool happens to prefer. Guessing which one was meant "is how
    a wrong customer gets refunded" (the contract's own words), and gate 1 is
    where that decision belongs.

    The drift guard is
    ``tests/integration/test_mcp_contract_arguments.py``: it reads the
    parameters out of the contract document and asserts this schema accepts
    every one. It was written because the guard that already existed
    (``tests/unit/test_permissions.py::test_every_gate_1_schema_is_accepted_by_the_real_tool``)
    only pushes gate 1's fields *toward* the server; a parameter the contract
    documents but the schema omits is invisible to it.
    """

    model_config = ConfigDict(extra="forbid")

    customer_email: str | None = Field(default=None, min_length=1)
    customer_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> CustomerResolveArgs:
        given = [name for name in ("customer_email", "customer_id") if getattr(self, name)]
        if len(given) != 1:
            # TRY003 wants the message on an exception class, and a field
            # validator has none: Pydantic wraps this into a ValidationError
            # with no class of ours to hang it on. The reasoning lives in the
            # class docstring above, and this string is what the model sees.
            raise ValueError(  # noqa: TRY003 -- no exception class exists to hold it
                "exactly one of customer_email or customer_id is required"
            )
        return self


class InvoiceLookupArgs(BaseModel):
    """Arguments for ``billing.get_invoice``."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(min_length=1)


class TransactionListArgs(BaseModel):
    """Arguments for ``billing.list_transactions``.

    ``invoice_id`` is the only argument, and it is the only one the tool takes:
    ``mcp_servers/billing/server.py`` declares
    ``list_transactions(invoice_id: str)`` and ``docs/mcp-contracts.md`` §S2
    specifies ``{ "invoice_id": "INV-2026-384" }``. This model previously declared
    ``account_id`` plus a ``limit`` page size, and **no argument set satisfied
    both sides** -- gate 1 accepted ``account_id`` and the server rejected it as
    ``validation_error``; the server accepted ``invoice_id`` and gate 1 rejected
    it under ``extra="forbid"``. The duplicate-detection step of the golden path
    therefore could not execute at all. It survived four milestones because
    ``tests/unit/test_permissions.py`` pinned the ``account_id`` form, i.e. a
    test agreed with the bug.

    The contract is the authority. ``limit`` is dropped because the server has no
    such parameter: sending one is accepted (the MCP layer ignores unknown
    fields) but silently ignored, so a declared ``limit`` would promise a bound
    that does not exist. The drift guard
    ``tests/unit/test_permissions.py::test_every_gate_1_schema_is_accepted_by_the_real_tool``
    pushes each registered model through the real gateway so the two sides cannot
    diverge again without a red test.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(min_length=1)


class IssueSearchArgs(BaseModel):
    """Arguments for ``issues.search``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class IssueCreateArgs(BaseModel):
    """Arguments for ``issues.create``.

    The fields are the contract's, which are also the server's:
    ``docs/mcp-contracts.md`` §5 shows ``{ "title", "description", "priority",
    "labels" }`` and ``mcp_servers/issues/server.py::create`` declares exactly
    those four. This model previously declared ``title`` and ``body`` -- a
    ``body`` the tool has no parameter for, and no ``description``/``priority``/
    ``labels``. **No argument set satisfied both sides**: the contract's example
    was refused at gate 1 under ``extra="forbid"``, and a payload gate 1
    accepted named a field the server would have ignored.

    That is the same defect ``TransactionListArgs`` records having survived four
    milestones, in a second tool. It is why
    ``tests/integration/test_mcp_contract_arguments.py`` exists: the older guard
    only pushed gate 1's fields toward the server, so a contract parameter the
    schema omits went unnoticed in both directions.

    ``description``, ``priority`` and ``labels`` are optional because the server
    declares them so -- only ``title`` is required, and the server allocates the
    ``OPS-{n}`` key itself.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)


# Gate 1's schema surface: the Pydantic model each tool's arguments are parsed
# into. Keyed by the same names as ``TOOL_REGISTRY`` and asserted against it by
# ``tests/unit/test_permissions.py``, so a new registry entry without a schema
# (or the reverse) is a red test rather than a call that reaches the gateway
# unparsed.
TOOL_ARGUMENT_SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "knowledge.search": KnowledgeSearchArgs,
    "crm.get_customer": CustomerResolveArgs,
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

    ``exclude_none=True`` is what the dump uses, and it is load-bearing rather
    than cosmetic. A field defaulted to ``None`` means *the model did not supply
    it*, and the dictionary built here is handed to the MCP server verbatim
    (``agents/runtime.py``'s EXECUTE step) -- so an included ``None`` is a
    parameter the tool receives. Three schemas have such fields and none of them
    wants the null:

    * ``crm.get_customer`` -- sending both ``customer_email: null`` and
      ``customer_id`` reads to the server as *both* identifiers given, which the
      contract refuses ("exactly one"). The null would turn the contract's own
      accepted form into a ``validation_error``.
    * ``billing.issue_refund`` -- ``transaction_amount`` is gate 1's fast-fail
      field, derived from the retrieved invoice; the MCP server has no such
      parameter and rejects unknown ones under ``extra="forbid"``.
    * ``issues.create`` -- ``description`` and ``priority`` are optional on both
      sides, and a null is a value where the server expects an absent key.

    Found by ``tests/integration/test_mcp_contract_arguments.py`` when
    ``crm.get_customer`` grew its optional identifier: the test asserted the
    call reached the tool, and what reached it was
    ``{"customer_email": None, "customer_id": "C1"}``.

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
    return spec, parsed.model_dump(exclude_none=True)
