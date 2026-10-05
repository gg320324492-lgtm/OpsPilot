"""The dispatch contract: the gate must send the arguments the *real* tool needs.

Why this file exists
--------------------
Every other gate test drives ``_gate_and_execute`` with a stub gateway that
accepts **any** arguments and returns a canned success. Such a stub cannot tell
whether the payload the gate dispatches satisfies the real tool's signature --
it agrees with whatever the gate sends. That is exactly how a blocking defect
survived a green suite: gate 4 derived the refund's ``idempotency_key`` and
recorded it on the tool call, but the EXECUTE step forwarded only the model's
parsed arguments, which ``RefundArgs`` deliberately does not allow to carry a
key. The stub accepted the keyless payload; the real MCP server requires the key
and refused every dispatch, so an approved refund could never execute and the
project's central promise -- an idempotent refund -- never engaged on the real
path.

These tests close that gap by driving the gate against the **real**
``MCPToolGateway`` over in-process billing servers backed by a private
``tmp_path`` store, with the real SQLite repositories. They assert on the refund
side effect in the store document, not merely on a returned status.

The cover, in the order the gates run:

- a proposal carrying an ``idempotency_key`` is rejected at gate 1 (the model
  cannot choose a key -- ``docs/tool-permissions.md`` §4);
- an approved refund actually executes through the real gateway, with the derived
  key recorded on the tool call;
- a second approved call under the same key returns the *same* ``refund_id`` and
  writes no second refund row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.agents.runtime import GATE_SCHEMA, ExecutedLookup, _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.agents.state import RunContext, ToolCallRecord
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.policies import derive_idempotency_key
from opspilot.domain.runs import AgentRun
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder

# The transaction the ACME duplicate-charge scenario refunds. It is ``charged``
# in the committed seed, so the server can move it to ``refunded``.
_TX = "TX-88219"
_AMOUNT = 129.00


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


@pytest.fixture
def gateway(tmp_path: Path) -> MCPToolGateway:
    """The *real* gateway over in-process servers backed by a private store.

    This is the point of the file: the payload the gate builds is handed to the
    server's own generated JSON Schema, so a missing required argument is a real
    ``validation_error`` rather than something a stub would wave through.
    """
    return MCPToolGateway(servers=build_in_process_servers(tmp_path))


async def _make_run(factory: sessionmaker[Session]) -> AgentRun:
    """Create a ticket and its run, then return the run."""
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


class Harness:
    """The real stores, recorder and gateway one dispatch test threads through."""

    def __init__(
        self, factory: sessionmaker[Session], run: AgentRun, gateway: MCPToolGateway
    ) -> None:
        self.run = run
        self.run_store = SqlRunStore(factory)
        self.tool_call_store = SqlToolCallStore(factory)
        self.approval_store = SqlApprovalStore(factory)
        self.recorder = TraceRecorder(run_id=run.id, session_factory=factory)
        self.gateway = gateway
        self.factory = factory

    def context(self) -> RunContext:
        return RunContext(
            run=self.run,
            ticket_subject="duplicate charge",
            ticket_body="charged twice",
            customer_email="a@example.com",
        )

    async def gate(
        self,
        proposal: ProposedAction,
        *,
        tool_call_id: UUID | None = None,
        executed_lookup: ExecutedLookup | None = None,
    ) -> ToolCallRecord:
        return await _gate_and_execute(
            self.context(),
            proposal,
            gateway=self.gateway,
            run_store=self.run_store,
            tool_call_store=self.tool_call_store,
            approval_store=self.approval_store,
            recorder=self.recorder,
            executed_lookup=executed_lookup,
            tool_call_id=tool_call_id,
        )


async def _harness(factory: sessionmaker[Session], gateway: MCPToolGateway) -> Harness:
    return Harness(factory, await _make_run(factory), gateway)


def _refund_proposal() -> ProposedAction:
    """The refund proposal the human approves."""
    return ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": _TX, "amount": _AMOUNT},
        reason="duplicate charge confirmed",
    )


async def _seed_approved_call(harness: Harness) -> UUID:
    """Create an approved tool call directly, then re-enter the gate on its id.

    The task's approved path: insert the ``ToolCall`` row (``proposed``, no key
    stamped) and an ``ApprovalRequest`` already in the ``approved`` state bound to
    that id, then re-enter the gates with ``tool_call_id`` so gate 5's
    ``has_approved`` reads true. Building the rows directly keeps the tool call
    the gate executes free of the idempotency key until the EXECUTE step decides
    what to stamp -- which is what lets a replay be recorded without manufacturing
    a second executed row that bears the key (invariant 4).
    """
    arguments: dict[str, object] = {"transaction_id": _TX, "amount": _AMOUNT}
    call_id = await harness.tool_call_store.record_proposed(
        run_id=harness.run.id,
        tool_name="billing.issue_refund",
        arguments=arguments,
        permission="high_risk_write",
    )
    approval = ApprovalRequest(
        id=uuid4(),
        run_id=harness.run.id,
        tool_call_id=call_id,
        status=ApprovalStatus.APPROVED,
        reason="duplicate charge confirmed",
        risk_explanation="This moves $129.00 out of the account.",
        arguments_snapshot=dict(arguments),
        created_at=datetime.now(UTC),
        decided_at=datetime.now(UTC),
        decided_by="operator:1",
    )
    await harness.approval_store.create(approval)
    return call_id


def _refund_rows(directory: Path) -> list[dict[str, Any]]:
    """The billing store's refund collection, read from what the server wrote."""
    data = json.loads((directory / "billing.json").read_text(encoding="utf-8"))
    refunds = data["refunds"]
    assert isinstance(refunds, list)
    return refunds


def _tool_call(factory: sessionmaker[Session], tool_call_id: UUID) -> models.ToolCall:
    with db.session_scope(factory) as session:
        row = session.get(models.ToolCall, tool_call_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# Gate 1 -- the model cannot choose its own idempotency key
# ---------------------------------------------------------------------------


async def test_proposal_supplying_an_idempotency_key_is_rejected_at_gate_1(
    factory: sessionmaker[Session], gateway: MCPToolGateway
) -> None:
    """A proposal that carries an ``idempotency_key`` is refused before dispatch.

    ``RefundArgs`` forbids the field (``extra="forbid"``), so the model -- or a
    prompt injection trying to pick its own key -- is rejected at gate 1 with no
    dispatch and no derived key recorded. This is the control that keeps the key
    a function of the run and the intended effect rather than something the
    model can vary to defeat the duplicate check
    (``docs/tool-permissions.md`` §4).
    """
    harness = await _harness(factory, gateway)

    record = await harness.gate(
        ProposedAction(
            tool_name="billing.issue_refund",
            arguments={
                "transaction_id": _TX,
                "amount": _AMOUNT,
                "idempotency_key": "model-chosen-key",
            },
        )
    )

    assert record.status == "rejected"
    with db.session_scope(factory) as session:
        row = session.execute(
            select(models.ToolCall).where(models.ToolCall.tool_name == "billing.issue_refund")
        ).scalar_one()
    assert row.rejection_reason == GATE_SCHEMA
    assert row.idempotency_key is None


# ---------------------------------------------------------------------------
# The real dispatch -- the defect this file was written for
# ---------------------------------------------------------------------------


async def test_approved_refund_executes_through_the_real_gateway(
    factory: sessionmaker[Session], gateway: MCPToolGateway, tmp_path: Path
) -> None:
    """An approved refund actually executes, with the derived key dispatched.

    This is the regression test for the defect: with only ``call.arguments``
    forwarded, the server raised a ``validation_error`` for the missing
    ``idempotency_key`` and ``ok`` was ``False``. Now the EXECUTE step adds the
    key gate 4 derived, the server accepts the call, the transaction moves to
    ``refunded`` and a refund row exists in the store. The ``idempotency_key``
    recorded on the tool call is the deterministic one, not anything the
    proposal supplied.
    """
    harness = await _harness(factory, gateway)
    tool_call_id = await _seed_approved_call(harness)

    record = await harness.gate(_refund_proposal(), tool_call_id=tool_call_id)

    assert record.status == "executed"
    assert record.result is not None
    assert record.result["code"] is None
    assert record.result["refund_id"] is not None
    assert record.result["replayed"] is False

    # The key gate 4 derived is what was recorded -- and what the server echoed.
    expected_key = derive_idempotency_key(harness.run, {"transaction_id": _TX})
    assert expected_key == f"refund:{harness.run.id}:{_TX}"
    call = _tool_call(factory, tool_call_id)
    assert call.idempotency_key == expected_key
    assert record.result["idempotency_key"] == expected_key

    # The side effect is real: exactly one refund row, for this transaction.
    refunds = _refund_rows(tmp_path)
    assert len(refunds) == 1
    assert refunds[0]["refund_id"] == record.result["refund_id"]
    assert refunds[0]["transaction_id"] == _TX


async def test_second_approved_call_on_one_key_replays_without_a_second_refund(
    factory: sessionmaker[Session], gateway: MCPToolGateway, tmp_path: Path
) -> None:
    """A second call under one key returns the first refund and writes no new row.

    This is the crash-retry the idempotency design exists for
    (``docs/risks.md``): the refund moved at the server but the ``ToolCall`` row
    was never committed as ``executed``. Re-entering the gate for the *same*
    approved call must therefore reach the real server, which -- because the
    dispatched key is the derived one -- recognises the key, returns the *same*
    ``refund_id`` with ``replayed: true``, and inserts no second refund row. One
    keyed ``executed`` row is recorded, one refund row exists, and the derived key
    is what is both dispatched and recorded.

    Before the dispatch fix this could not happen at all: the keyless payload
    failed the server's required-argument check, so a retry raised the same
    ``validation_error`` instead of replaying.
    """
    harness = await _harness(factory, gateway)
    expected_key = derive_idempotency_key(harness.run, {"transaction_id": _TX})

    # The prior attempt: the money moved at the server, but no ``executed`` tool
    # call row was committed. This is the state the gate retries from.
    pre = await gateway.call_tool(
        "billing.issue_refund",
        {"transaction_id": _TX, "amount": _AMOUNT, "idempotency_key": expected_key},
    )
    assert pre.ok is True
    assert pre.result is not None
    first_refund_id = pre.result["refund_id"]
    assert len(_refund_rows(tmp_path)) == 1

    # The retry: a fresh approved call whose row records no key yet. The gate
    # derives the same key, dispatches it, and the server replays.
    tool_call_id = await _seed_approved_call(harness)
    retry = await harness.gate(_refund_proposal(), tool_call_id=tool_call_id)

    assert retry.status == "executed"
    assert retry.result is not None
    assert retry.result["refund_id"] == first_refund_id
    assert retry.result["replayed"] is True
    assert retry.result["idempotency_key"] == expected_key

    # No second refund row: the whole point of the guarantee.
    assert len(_refund_rows(tmp_path)) == 1

    # The single executed row carries the derived key -- invariant 4 holds.
    call = _tool_call(factory, tool_call_id)
    assert call.idempotency_key == expected_key
    assert call.status == "executed"
