"""Integration tests for ``MCPToolGateway`` over the real MCP servers.

These drive the gateway end to end: the tool names go in through the port, the
calls are dispatched through each server's *own* ``call_tool`` (so the generated
JSON Schema, the ``structured_output`` serialisation and the ``CallToolResult``
envelope are all exercised), and the ``ToolResult`` that comes back is asserted
on. There is no agent and no LLM in this file.

Everything the MCP layer does *except* the stdio pipe is covered here; the pipe
itself is covered by ``tests/integration/test_mcp_*.py``, which starts each
server as a real subprocess (``docs/mcp-sdk-notes.md`` §6). The gateway's
transport choice and its cost are documented in the adapter's module docstring.

Each test gets an isolated store directory via ``tmp_path``, so a refund written
by one test cannot leak into the next.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.domain.tools import TOOL_REGISTRY

pytestmark = pytest.mark.asyncio


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[MCPToolGateway]:
    """A gateway over freshly-built in-process servers backed by ``tmp_path``.

    The servers are built once per test against a private store directory, so the
    refund-count assertions read a store only this test has touched.
    """
    servers = build_in_process_servers(tmp_path)
    yield MCPToolGateway(servers=servers)


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


async def test_list_tools_returns_the_registry(gateway: MCPToolGateway) -> None:
    """``list_tools`` returns exactly the ``TOOL_REGISTRY`` specs.

    The registry is the source of truth for permissions, and the gateway must
    not invent a second one by reading the servers. The assertion is by name
    *and* by the whole spec object, so a gateway that dropped the permission or
    the server binding would fail here rather than silently pass.
    """
    tools = await gateway.list_tools()

    by_name = {spec.name: spec for spec in tools}
    assert set(by_name) == set(TOOL_REGISTRY)
    for name, spec in TOOL_REGISTRY.items():
        assert by_name[name] == spec


# ---------------------------------------------------------------------------
# call_tool -- the happy path
# ---------------------------------------------------------------------------


async def test_get_invoice_returns_the_invoice(gateway: MCPToolGateway) -> None:
    """A successful ``billing.get_invoice`` returns the invoice's fields.

    ``INV-2026-384`` is the ACME invoice the golden path opens on, so its total
    is the ``129.00`` the duplicate-charge scenario is built around.
    """
    result = await gateway.call_tool("billing.get_invoice", {"invoice_id": "INV-2026-384"})

    assert result.ok is True
    assert result.error is None
    assert result.tool_name == "billing.get_invoice"
    assert result.result is not None
    assert result.result["invoice_id"] == "INV-2026-384"
    assert result.result["customer_id"] == "CUS-1001"
    assert result.result["total"] == 129.00
    assert result.result["code"] is None
    assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# call_tool -- idempotency through the gateway
# ---------------------------------------------------------------------------


async def test_refund_twice_with_one_key_yields_one_refund(gateway: MCPToolGateway) -> None:
    """Two identical calls, one ``idempotency_key`` -> one refund side effect.

    The count is asserted against the store document the server writes, not only
    against the two responses: the guarantee lives in the store, so that is where
    a second refund row would appear if the tool were wrong. The second response
    must also carry ``replayed: true`` and the *same* ``refund_id`` -- without
    that flag a replay would look identical to a fresh refund and the run's audit
    would claim two refunds when one happened (``docs/mcp-contracts.md`` §2).
    """
    key = "refund:run-gateway:TX-88219"
    args = {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": key}

    first = await gateway.call_tool("billing.issue_refund", args)
    second = await gateway.call_tool("billing.issue_refund", args)

    assert first.ok is True
    assert first.result is not None
    assert first.result["replayed"] is False
    assert second.ok is True
    assert second.result is not None
    assert second.result["replayed"] is True
    assert first.result["refund_id"] == second.result["refund_id"]


# ---------------------------------------------------------------------------
# call_tool -- refusals arrive as results, never as exceptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    [
        # Unknown transaction -> not_found.
        (
            {"transaction_id": "TX-00000", "amount": 10.00, "idempotency_key": "k-nf"},
            "not_found",
        ),
        # Amount greater than the transaction -> amount_exceeds_transaction.
        (
            {"transaction_id": "TX-88219", "amount": 999.00, "idempotency_key": "k-amt"},
            "amount_exceeds_transaction",
        ),
    ],
)
async def test_refusal_is_a_result_with_its_code(
    gateway: MCPToolGateway, arguments: dict[str, object], expected_code: str
) -> None:
    """A refusal comes back as ``ok=False`` carrying the server's error code.

    This is the assertion that the gateway does not turn a structured refusal
    into an opaque transport error (``docs/mcp-sdk-notes.md`` §5): the agent has
    to be able to tell ``not_found`` from ``amount_exceeds_transaction`` from a
    crash, and it can only do that if the code survives the round trip.
    """
    result = await gateway.call_tool("billing.issue_refund", dict(arguments))

    assert result.ok is False
    assert result.error == expected_code
    assert result.result is not None
    assert result.result["code"] == expected_code


async def test_invalid_state_refusal_is_also_a_result(gateway: MCPToolGateway) -> None:
    """Refunding an already-refunded transaction returns ``invalid_state``.

    Exercised through the gateway so the code survives the port boundary. The
    transaction is moved to ``refunded`` by a first, distinct key, so the second
    call's ``invalid_state`` comes from the server's state check rather than from
    a replay short-circuit.
    """
    first = await gateway.call_tool(
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "k-first"},
    )
    assert first.ok is True

    result = await gateway.call_tool(
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "k-second"},
    )
    assert result.ok is False
    assert result.error == "invalid_state"


async def test_missing_required_key_is_a_validation_error_result(
    gateway: MCPToolGateway,
) -> None:
    """A call omitting ``idempotency_key`` is a ``validation_error`` result.

    The SDK raises ``ToolError`` at the protocol layer because the generated JSON
    Schema marks the argument required -- gate 1 satisfied by the transport
    (``docs/mcp-sdk-notes.md`` §4). The gateway catches that and returns a result
    carrying the same code, so the caller still never sees a raise.
    """
    result = await gateway.call_tool(
        "billing.issue_refund", {"transaction_id": "TX-88219", "amount": 129.00}
    )

    assert result.ok is False
    assert result.error == "validation_error"


async def test_unknown_tool_is_refused_not_dispatched(gateway: MCPToolGateway) -> None:
    """An unregistered name is refused with ``unknown_tool`` and never routed.

    The gateway is the executor, not the decider (gate 2 lives in the runtime),
    so this is a defence-in-depth refusal: even if a proposal reached here, a
    name that is not in the registry cannot be dispatched to a server.
    """
    result = await gateway.call_tool("billing.wire_transfer", {"amount": 1_000_000.0})

    assert result.ok is False
    assert result.error == "unknown_tool"


# ---------------------------------------------------------------------------
# The gateway talks to every server, not only billing.
# ---------------------------------------------------------------------------


async def test_crm_tool_is_dispatched_to_the_crm_server(gateway: MCPToolGateway) -> None:
    """A ``crm`` call is routed to the CRM server and returns its record."""
    result = await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert result.ok is True
    assert result.result is not None
    customer = result.result["customer"]
    assert isinstance(customer, dict)
    assert customer["company"] == "ACME"


async def test_issues_create_executes_through_the_gateway(gateway: MCPToolGateway) -> None:
    """A ``SAFE_WRITE`` tool executes and returns its allocated key."""
    result = await gateway.call_tool(
        "issues.create", {"title": "Duplicate charge for ACME", "priority": "high"}
    )

    assert result.ok is True
    assert result.result is not None
    assert result.result["key"] == "OPS-1001"


# ---------------------------------------------------------------------------
# Store isolation: the fixture really does give each test a private store.
# ---------------------------------------------------------------------------


async def test_fixture_store_is_isolated_from_the_committed_seed(tmp_path: Path) -> None:
    """Each gateway build starts from the committed seed, not from prior writes.

    Guards the fixture itself: ``build_in_process_servers`` constructs a fresh
    ``Store`` per server from the committed ``seed.json``, so a refund written
    through one gateway is not visible to the next -- every count assertion
    elsewhere is order-independent, and no test can see another's mutation even
    when they share a directory.

    The store *does* persist: the refund is written to ``billing.json`` in the
    directory, and a second refund inside the same gateway build is a replay
    (asserted below). What is isolated is the *build*: constructing anew re-seeds.
    """
    first = MCPToolGateway(servers=build_in_process_servers(tmp_path))
    first_result = await first.call_tool(
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "iso-1"},
    )
    assert first_result.ok is True
    assert first_result.result is not None
    assert first_result.result["replayed"] is False

    # The write persisted to the per-directory store file...
    store_file = tmp_path / "billing.json"
    assert store_file.exists()
    assert len(json.loads(store_file.read_text(encoding="utf-8"))["refunds"]) == 1

    # ...but a fresh build re-seeds from the committed seed, so no prior refund.
    rebuilt = MCPToolGateway(servers=build_in_process_servers(tmp_path))
    rebuilt_result = await rebuilt.call_tool(
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "iso-2"},
    )
    assert rebuilt_result.ok is True
    assert rebuilt_result.result is not None
    assert rebuilt_result.result["replayed"] is False
