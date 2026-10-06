"""Unit tests for the tool permission model.

The guard this file implements is ``docs/tool-permissions.md`` §2.1: the registry
is compared against a hard-coded literal, so *any* change to a permission --
including an accidental one -- is a red test that a human must justify in the
diff. That is why the expected mapping is written out longhand below rather than
derived from the registry it checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.domain.tools import (
    TOOL_ARGUMENT_SCHEMAS,
    TOOL_REGISTRY,
    InvalidArgumentsError,
    Permission,
    ToolSpec,
    get_tool_spec,
    validate_tool_call,
)

# Written out in full, deliberately not derived from TOOL_REGISTRY. If a
# permission is downgraded, the diff shows the old and new value side by side in
# a test named "permissions". See docs/adr/0003.
EXPECTED_PERMISSIONS: dict[str, Permission] = {
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


def test_registry_permissions_are_static() -> None:
    """The registry's permissions equal the hard-coded literal exactly."""
    assert {name: spec.permission for name, spec in TOOL_REGISTRY.items()} == EXPECTED_PERMISSIONS


def test_registry_names_match_their_keys() -> None:
    """Every spec's ``name`` equals the dict key it is filed under.

    A mismatch would make ``get_tool_spec`` return a spec whose name disagrees
    with the call that fetched it, and the permission column would be snapshotted
    under the wrong name.
    """
    for key, spec in TOOL_REGISTRY.items():
        assert spec.name == key


def test_exactly_one_high_risk_tool() -> None:
    """Phase 1 has exactly one high-risk tool, and it is the refund."""
    high_risk = {n for n, s in TOOL_REGISTRY.items() if s.permission is Permission.HIGH_RISK_WRITE}
    assert high_risk == {"billing.issue_refund"}


def test_high_risk_tool_requires_approval_and_a_key() -> None:
    refund = TOOL_REGISTRY["billing.issue_refund"]
    assert refund.requires_approval is True
    assert refund.requires_idempotency_key is True


def test_registry_is_frozen_specs() -> None:
    """A ``ToolSpec`` is a frozen model, so a permission cannot be reassigned on
    a live instance at runtime."""
    with pytest.raises(ValidationError):
        TOOL_REGISTRY["billing.issue_refund"].permission = Permission.READ


def test_every_registered_tool_has_an_argument_schema() -> None:
    """Gate 1 needs a schema for every registered tool; the two mappings must not
    drift apart."""
    assert set(TOOL_ARGUMENT_SCHEMAS) == set(TOOL_REGISTRY)


def test_get_tool_spec_returns_the_registered_spec() -> None:
    assert get_tool_spec("billing.issue_refund") is TOOL_REGISTRY["billing.issue_refund"]


def test_get_tool_spec_returns_none_for_an_unknown_tool() -> None:
    assert get_tool_spec("does.not.exist") is None


# ---------------------------------------------------------------------------
# Gate 1 -- schema validation
# ---------------------------------------------------------------------------


def test_unknown_tool_has_no_spec_to_validate() -> None:
    """Gate 1 returns ``None`` for an unregistered tool so gate 2 reports it."""
    spec, parsed = validate_tool_call("does.not.exist", {})
    assert spec is None
    assert parsed == {}


def test_valid_arguments_are_parsed_and_defaulted() -> None:
    spec, parsed = validate_tool_call(
        "billing.issue_refund", {"transaction_id": "TX-1", "amount": "1.00"}
    )
    assert spec is TOOL_REGISTRY["billing.issue_refund"]
    assert parsed["transaction_id"] == "TX-1"
    assert parsed["currency"] == "USD"  # a default is filled in, not dropped


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("crm.get_customer", {"wrong_field": "x"}),
        ("crm.get_customer", {}),
        ("billing.issue_refund", {"transaction_id": "TX-1"}),  # missing amount
        ("billing.issue_refund", {"transaction_id": "", "amount": "1.00"}),  # empty id
        ("knowledge.search", {"query": ""}),  # empty query
        ("issues.create", {"title": "t"}),  # missing body
        ("billing.list_transactions", {"invoice_id": ""}),  # empty id
        ("billing.list_transactions", {}),  # missing invoice_id
        ("billing.list_transactions", {"account_id": "A"}),  # the pre-M6c field name
        ("billing.list_transactions", {"invoice_id": "I", "limit": 5}),  # no such parameter
    ],
)
def test_invalid_arguments_are_rejected(name: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgumentsError):
        validate_tool_call(name, arguments)


def test_unknown_fields_are_forbidden() -> None:
    """``extra='forbid'`` on every argument model means an injected field cannot
    ride along into the call."""
    with pytest.raises(InvalidArgumentsError):
        validate_tool_call(
            "billing.issue_refund",
            {"transaction_id": "TX-1", "amount": "1.00", "approved": "true"},
        )


def test_validate_never_returns_a_spec_without_parsing() -> None:
    """The returned spec and the parsed arguments come out together, so a caller
    cannot get the spec and skip validation."""
    result = validate_tool_call("issues.create", {"title": "t", "body": "b"})
    spec, parsed = result
    assert isinstance(spec, ToolSpec)
    assert parsed == {"title": "t", "body": "b"}


# ---------------------------------------------------------------------------
# The drift guard -- gate 1's schema against the *real* tool
# ---------------------------------------------------------------------------
#
# Acceptance criterion (docs/milestones.md §M6, and the finding that forced this
# file): a tool whose gate-1 schema and whose MCP server disagree on the argument
# names can never be called. ``TransactionListArgs`` declared ``account_id`` and
# a ``limit`` page size; ``mcp_servers/billing/server.py`` and
# ``docs/mcp-contracts.md`` §S2 both declare ``invoice_id``. No argument set
# satisfied both sides -- gate 1 accepted ``account_id`` and the server rejected
# it (``validation_error``); the server accepted ``invoice_id`` and gate 1
# rejected it (``extra="forbid"``). The golden path's duplicate-detection step
# could not execute. It survived four milestones because the pinning test above
# asserted the ``account_id`` form: a test that agreed with the bug.
#
# This guard reads TOOL_ARGUMENT_SCHEMAS and pushes each model's fields through
# the real MCPToolGateway, rather than transcribing a list of tools: a
# transcribed list is a second copy of the registry that a new tool can be added
# to without ever appearing here. It is derived, so adding a tool to the registry
# without matching the server makes this red automatically.
#
# The comparison that matters is *not* "the call succeeded" -- several servers
# return a structured refusal (``not_found``) for an invented id, which is still
# a success at this layer and would let the guard pass vacuously. It is that the
# server did not reject the call at its own schema, i.e. ``ok`` is True and the
# error is not ``validation_error``. A tool that cannot be reached is caught here
# even when its arguments are merely wrong.


def _example_arguments(model: type[Any]) -> dict[str, Any]:
    """Build the minimum valid arguments for a gate-1 argument model.

    Derived from the model's own fields rather than written per tool, so a schema
    that gains or renames a required field is exercised without editing this
    helper. Each field is given a value of its own annotation's type; enum-ish
    string fields that the server also treats as a bounded set are left to the
    server's own validation, which is the point -- this helper only has to get
    past *gate 1*.
    """
    from pydantic import BaseModel as _BaseModel

    assert issubclass(model, _BaseModel)
    arguments: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        if annotation is str:
            arguments[name] = "DRIFT-GUARD-PROBE"
        elif annotation is int:
            arguments[name] = 1
        elif annotation is float:
            arguments[name] = 1.0
        else:  # pragma: no cover - no required non-scalar field today
            arguments[name] = "DRIFT-GUARD-PROBE"
    return arguments


@pytest.fixture
def drift_gateway(tmp_path: Path) -> MCPToolGateway:
    """The *real* gateway over in-process MCP servers on a private store."""
    return MCPToolGateway(servers=build_in_process_servers(tmp_path))


@pytest.fixture
def drift_servers(tmp_path: Path) -> dict[str, Any]:
    """The same in-process MCP servers, as the map the gateway holds."""
    return dict(build_in_process_servers(tmp_path))


def _external_tools() -> dict[str, type[Any]]:
    """The subset of TOOL_ARGUMENT_SCHEMAS that dispatches over MCP.

    ``knowledge.search`` is ``server="internal"`` and is served in-process by the
    knowledge adapter, not by an MCP server (``docs/mcp-contracts.md`` §4), so
    the gateway cannot exercise it. It is excluded here and covered separately by
    ``test_every_registered_tool_has_an_argument_schema``.
    """
    return {
        name: model
        for name, model in TOOL_ARGUMENT_SCHEMAS.items()
        if TOOL_REGISTRY[name].server != "internal"
    }


async def test_every_gate_1_schema_is_accepted_by_the_real_tool(
    drift_gateway: MCPToolGateway,
) -> None:
    """Each gate-1 schema's own fields satisfy the real registered tool.

    Acceptance criterion (docs/milestones.md §M6, "the exact documented sequence"
    must be executable): the duplicate-detection step of the golden path failed
    because ``TransactionListArgs`` and ``billing.list_transactions`` disagreed.
    This test is the guard against that class of defect returning.

    For every tool in ``TOOL_ARGUMENT_SCHEMAS`` (minus the internal
    ``knowledge.search``), the model's required fields are dispatched through the
    real gateway, mirroring what ``agents/runtime.py`` actually sends at EXECUTE,
    and the server must not reject the call at its own schema.

    **What counts as success.** Not ``result.ok``: a server asked about an
    invented id answers ``not_found``, which is a *correct* refusal and proves the
    call reached the tool body. The failure this guards is specifically
    ``validation_error`` -- the server rejecting the payload at the MCP schema
    layer, before any OpsPilot code ran. That is exactly what ``account_id``
    produced, and asserting only on ``ok`` would have let it through.

    **The dispatch mirrors the runtime.** ``billing.issue_refund`` requires an
    ``idempotency_key`` that gate 1 deliberately does *not* accept from the
    model; gate 4 derives it and the EXECUTE step adds it
    (``docs/tool-permissions.md`` §4). So for a spec with
    ``requires_idempotency_key``, this test appends the same derived key rather
    than passing a model-supplied one -- a key the schema rejects at gate 1 would
    make the guard vacuous for the one tool that moves money.
    """
    failures: list[str] = []
    tools = _external_tools()
    assert tools, "no external tools found -- the guard would pass vacuously"

    for name, model in sorted(tools.items()):
        arguments = _example_arguments(model)
        spec, parsed = validate_tool_call(name, arguments)
        assert spec is not None, f"{name} is not in the registry"

        dispatch: dict[str, Any] = dict(parsed)
        if spec.requires_idempotency_key:
            # What gate 4 derives and the EXECUTE step adds. The shape is
            # asserted exactly in test_gate_dispatch_contract.py; here it only
            # has to be a string, so the guard can reach the server's schema.
            dispatch["idempotency_key"] = f"refund:drift-guard:{name}"

        result = await drift_gateway.call_tool(name, dispatch)
        if result.error == "validation_error":
            failures.append(
                f"{name}: the real tool rejected the payload at its own MCP schema "
                f"-- gate 1 accepts these arguments but the server cannot be called "
                f"with them: dispatched={dispatch!r} error={result.error!r}"
            )

    assert not failures, (
        "these gate-1 schemas cannot call the real registered tool -- gate 1 and "
        "the MCP server have drifted apart, so no argument set satisfies both:\n  "
        + "\n  ".join(failures)
    )


async def test_gate_1_schema_and_server_agree_on_required_arguments(
    drift_servers: dict[str, Any],
) -> None:
    """The server's required-argument set is satisfiable by the gate-1 model.

    The complementary direction to the guard above. A schema can be "accepted"
    by the server on the happy path (a guessable required name) while still being
    unable to *supply* a required field the server demands -- which is what a
    renamed or dropped parameter looks like. This reads each real server's own
    generated JSON Schema and asserts every field it lists as required is a field
    the gate-1 model can produce.

    ``billing.issue_refund`` is excluded: its ``idempotency_key`` is required by
    the server but deliberately *absent* from ``RefundArgs`` -- it is derived by
    gate 4 and added at the EXECUTE step (``docs/tool-permissions.md`` §4), so
    requiring it in the schema would let the model choose its own key.
    """
    failures: list[str] = []

    for name, model in sorted(_external_tools().items()):
        if name == "billing.issue_refund":
            continue
        spec = TOOL_REGISTRY[name]
        server = drift_servers.get(spec.server)
        assert server is not None, f"{name} names server {spec.server!r} but none was built"
        listed = await server.list_tools()
        tool = next(t for t in listed if t.name == name)
        required = set(tool.input_schema.get("required", []))
        model_fields = set(model.model_fields)
        missing = required - model_fields
        if missing:
            failures.append(
                f"{name}: the real server requires {sorted(missing)}, which the "
                f"gate-1 model {model.__name__} does not declare"
            )

    assert not failures, (
        "the real servers require arguments the gate-1 models cannot supply -- "
        "the two sides have drifted apart:\n  " + "\n  ".join(failures)
    )
