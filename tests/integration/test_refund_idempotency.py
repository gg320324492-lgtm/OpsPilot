"""The refund's idempotency guarantee, over a real MCP connection and no agent.

## Why this file exists

``docs/milestones.md`` §M2 ticks the criterion "``billing.issue_refund`` mutates
state and is idempotent on ``idempotency_key``", and it records where the proof
lives: ``tests/integration/test_mcp_billing.py``. That file dispatches the
server's tools **in process** -- ``await server.call_tool(name, args)`` -- which
is the SDK's own shortcut for "call this Python function with these arguments".
It proves the *handler's* behaviour, and it does so well.

What it does not cross is **the MCP connection**. In-process dispatch never
serialises the arguments, never builds the tool's JSON Schema, never runs the
SDK's result marshalling, and never touches the child's environment. A
deployment does none of that skipping: it starts the server as a child process,
hands it a pipe, and speaks JSON-RPC to it. The idempotency guarantee a
deployment actually gets is the one that survives that trip.

So this file makes the trip. It starts the real ``billing`` server as a real
subprocess -- through the project's own
:mod:`opspilot.adapters.tools.mcp_stdio` adapter, so the command, the working
directory and the environment are the deployment's and not a test's invention --
and calls ``billing.issue_refund`` twice over the wire with one key.

**No agent, no model, no approval.** The point of the file is the MCP layer and
nothing else: no run, no policy engine, no model. The one thing taken from the
runtime is the *key shape* -- ``derive_idempotency_key`` is the function gate 4
calls, and the key it derives is the key the server has to honour. Asserting
the server honours **that** key is what makes this file a test of the seam
between two components rather than a second copy of the billing server's own
tests.

## What is asserted

1. Two calls, one key, over the wire: one refund row, the same ``refund_id``,
   ``replayed`` false then true. The row count is read from the **persisted
   document on disk**, not from the responses -- the document is the authority
   the handler scans, so that is where the count belongs.
2. The key ``derive_idempotency_key`` derives is a key the server honours: a
   replay of it is a replay.
3. A *different* key does not get a second refund out of the same transaction:
   the transaction is already ``refunded``, so the server refuses with
   ``invalid_state``. The idempotency key is the first line of defence and the
   transaction's state is the second; both have to hold.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp.types import CallToolResult

from opspilot.adapters.tools.mcp_stdio import StdioServerProcess
from opspilot.domain.policies import derive_idempotency_key
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.settings import default_mcp_server_command

#: The transaction the duplicate-charge case refunds.
_TRANSACTION = "TX-88219"
#: What the refund moves.
_AMOUNT = 129.00


@pytest.fixture
async def billing_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[StdioServerProcess]:
    """The real billing MCP server, as a child process, on its own store.

    The data directory is pointed at ``tmp_path`` through the one environment
    variable the servers read (``OPSPILOT_MCP_DATA_DIR``), so the child's
    persisted document is this test's own and nothing else can write to it.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    server = StdioServerProcess(
        "billing",
        default_mcp_server_command("mcp_servers.billing.server"),
    )
    try:
        yield server
    finally:
        await server.aclose()


def _persisted(tmp_path: Path) -> dict[str, Any]:
    """The billing store document as it is on disk.

    Read from the file rather than from a response: the handler's replay check
    scans the document, so the document is where the claim is checkable.
    """
    path = tmp_path / "billing.json"
    assert path.is_file(), f"the server persisted no store at {path}"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


async def _refund(server: StdioServerProcess, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call ``billing.issue_refund`` over the wire and return its payload."""
    raw = await server.call_tool("billing.issue_refund", arguments)
    assert isinstance(raw, CallToolResult), (
        f"the server answered with {type(raw).__name__}, not a tool result"
    )
    assert raw.structured_content is not None, "the server returned no payload"
    return dict(raw.structured_content)


def _run() -> AgentRun:
    """A run in a mid-flight state, for deriving an idempotency key."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=RunStatus.PLANNING,
        model_provider="fake",
        model_name="fake-1",
        created_at=datetime.now(UTC),
    )


async def test_same_idempotency_key_twice_produces_one_refund(
    billing_server: StdioServerProcess, tmp_path: Path
) -> None:
    """Two calls over the wire, one key -> one refund row, one id, one replay.

    The count is read from the persisted document, not from the responses: the
    duplicate row would appear in the store, so that is where the check
    belongs.
    """
    key = "refund:run-over-the-wire:TX-88219"
    arguments: dict[str, Any] = {
        "transaction_id": _TRANSACTION,
        "amount": _AMOUNT,
        "idempotency_key": key,
    }

    first = await _refund(billing_server, arguments)
    second = await _refund(billing_server, arguments)

    assert first["code"] is None
    assert first["replayed"] is False
    assert second["code"] is None
    assert second["replayed"] is True
    assert first["refund_id"] == second["refund_id"]

    document = _persisted(tmp_path)
    refunds = document["refunds"]
    assert len(refunds) == 1, f"the store holds {len(refunds)} refunds for one key: {refunds}"
    assert refunds[0]["idempotency_key"] == key
    assert refunds[0]["refund_id"] == first["refund_id"]

    # The transaction moved once, and the other charge is untouched.
    rows = {row["transaction_id"]: row for row in document["transactions"]}
    assert rows[_TRANSACTION]["status"] == "refunded"
    assert rows[_TRANSACTION]["refund_id"] == first["refund_id"]
    assert rows["TX-88218"]["status"] == "charged"


async def test_the_key_the_runtime_derives_is_the_key_the_server_honours(
    billing_server: StdioServerProcess, tmp_path: Path
) -> None:
    """The key gate 4 injects is a key the server replays on.

    ``derive_idempotency_key`` is the function ``agents/runtime.py`` calls when
    it dispatches a write tool, and the key it derives is what reaches the
    server in production. A server that honoured a *different* key shape would
    be idempotent in the tests and not in the deployment -- and nothing else
    would notice, because every other test hands the server a key it wrote by
    hand.

    The two calls below are the same refund proposed twice, exactly as a replan
    proposes it: the same run, the same transaction, the same key.
    """
    run = _run()
    arguments: dict[str, Any] = {"transaction_id": _TRANSACTION, "amount": _AMOUNT}
    key = derive_idempotency_key(run, arguments)
    assert key.startswith("refund:"), f"the runtime's key shape changed: {key!r}"

    first = await _refund(billing_server, {**arguments, "idempotency_key": key})
    second = await _refund(billing_server, {**arguments, "idempotency_key": key})

    assert first["code"] is None
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["refund_id"] == second["refund_id"]

    document = _persisted(tmp_path)
    assert len(document["refunds"]) == 1, (
        f"the store holds {len(document['refunds'])} refunds for the runtime's key"
    )
    assert document["refunds"][0]["idempotency_key"] == key


async def test_a_second_key_cannot_refund_the_same_transaction_again(
    billing_server: StdioServerProcess, tmp_path: Path
) -> None:
    """A different key on an already-refunded transaction is refused.

    The idempotency key is the first line of defence; the transaction's state
    is the second, and the second one is what stops a *different* key from
    refunding the same charge twice. The refusal is a result with a code, not an
    exception -- and it adds no second row.
    """
    first_key = "refund:run-one:TX-88219"
    second_key = "refund:run-two:TX-88219"
    arguments: dict[str, Any] = {"transaction_id": _TRANSACTION, "amount": _AMOUNT}

    first = await _refund(billing_server, {**arguments, "idempotency_key": first_key})
    assert first["code"] is None
    assert first["replayed"] is False

    refused = await _refund(billing_server, {**arguments, "idempotency_key": second_key})
    assert refused["code"] == "invalid_state", (
        f"a second key refunded an already-refunded transaction: {refused}"
    )

    document = _persisted(tmp_path)
    assert len(document["refunds"]) == 1, (
        f"the refused call added a refund row: {document['refunds']}"
    )
    rows = {row["transaction_id"]: row for row in document["transactions"]}
    assert rows[_TRANSACTION]["refund_id"] == first["refund_id"]
