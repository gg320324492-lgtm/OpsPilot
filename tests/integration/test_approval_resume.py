"""Gap B, end to end: an approved refund actually executes; a rejected one does not.

This is the test the milestone turns on. It parks a run on a refund through the
pump, decides the approval **the way the API does** (the real
``SqlApprovalStore.decide``, which flips the run back to a claimable state), and
then runs the worker's ``drain_once``. It asserts the refund actually executed --
exactly once -- and that the run reached ``COMPLETED``. The mirror case rejects,
drains, and asserts ``COMPLETED`` with an escalation reply and **no refund**.

The billing gateway is a small idempotent stand-in for the MCP server: it keeps
one refund per ``idempotency_key``, so "the server holds one refund" is a real
assertion and a second execution would be observable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

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
from opspilot.agents.state import RunContext
from opspilot.domain.errors import RunParked
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder
from opspilot.worker.loop import drain_once
from tests.agent.test_runtime_fake import ScriptedProvider, UnusedOrchestrator


class FakeBillingServer:
    """An idempotent ``billing.issue_refund`` behind a gateway-shaped object.

    Returns the same ``refund_id`` for the same ``idempotency_key`` and stores
    one refund per key -- the MCP server's contract
    (``docs/architecture.md`` §8). ``refunds`` is what the test inspects.
    """

    def __init__(self) -> None:
        self.refunds: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        if name != "billing.issue_refund":
            return ToolResult(tool_name=name, ok=True, result={"ok": True})
        key = str(arguments["idempotency_key"])
        if key not in self.refunds:
            self.refunds[key] = {
                "refund_id": f"RFD-{len(self.refunds) + 1:04d}",
                "transaction_id": arguments["transaction_id"],
                "amount": arguments["amount"],
            }
        return ToolResult(tool_name=name, ok=True, result=dict(self.refunds[key]))


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


def _recorder_factory(factory: sessionmaker[Session]) -> Callable[[UUID], TraceRecorder]:
    return lambda run_id: TraceRecorder(run_id=run_id, session_factory=factory)


async def _make_run(factory: sessionmaker[Session]) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Duplicate charge",
            body="We were charged twice.",
            customer_email="billing@acme.example",
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


def _refund_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.tool_calls = [
        {
            "tool_name": "billing.issue_refund",
            "arguments": {"transaction_id": "TX-88219", "amount": 129.0},
            "reason": "duplicate charge confirmed",
        }
    ]
    return provider


async def _park(factory: sessionmaker[Session], run: AgentRun, gateway: FakeBillingServer) -> UUID:
    """Drive the pump until gate 5 parks the run; return the parked tool call id."""
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge",
        ticket_body="We were charged twice.",
        customer_email="billing@acme.example",
    )
    from opspilot.agents.runtime import run_loop

    with pytest.raises(RunParked) as parked:
        await run_loop(
            ctx,
            provider=_refund_provider(),
            gateway=gateway,
            orchestrator=UnusedOrchestrator(),
            run_store=SqlRunStore(factory),
            tool_call_store=SqlToolCallStore(factory),
            approval_store=SqlApprovalStore(factory),
            recorder=TraceRecorder(run_id=run.id, session_factory=factory),
        )
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.WAITING_APPROVAL
    return UUID(parked.value.tool_call_id)


async def _drain(factory: sessionmaker[Session], gateway: FakeBillingServer) -> bool:
    return await drain_once(
        worker_id="w1",
        run_store=SqlRunStore(factory),
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        provider=ScriptedProvider(reply="Your refund of $129.00 has been issued."),
        gateway=gateway,
        orchestrator=UnusedOrchestrator(),
        recorder_factory=_recorder_factory(factory),
    )


async def _approval_for(factory: sessionmaker[Session], tool_call_id: UUID) -> UUID:
    with db.session_scope(factory) as session:
        row = session.execute(
            select(models.ApprovalRequest).where(
                models.ApprovalRequest.tool_call_id == tool_call_id
            )
        ).scalar_one()
        return row.id


# ---------------------------------------------------------------------------
# Approve -> the refund executes exactly once, and the run completes
# ---------------------------------------------------------------------------


async def test_approved_refund_executes_on_drain(factory: sessionmaker[Session]) -> None:
    run = await _make_run(factory)
    gateway = FakeBillingServer()
    tool_call_id = await _park(factory, run, gateway)
    assert gateway.refunds == {}  # nothing executed while parked

    # Approve the way the API does: the real store's conditional decision, which
    # also flips the run to EXECUTING (re-queuing it for the worker).
    approval_id = await _approval_for(factory, tool_call_id)
    from datetime import UTC, datetime

    with db.session_scope(factory) as session:
        decided = await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
    assert decided.status.value == "approved"
    requeued = await SqlRunStore(factory).get(run.id)
    assert requeued is not None
    assert requeued.status is RunStatus.EXECUTING

    # The worker drains the re-queued run.
    processed = await _drain(factory, gateway)
    assert processed is True

    # The refund actually executed.
    assert len(gateway.refunds) == 1
    refund = next(iter(gateway.refunds.values()))
    assert refund["transaction_id"] == "TX-88219"
    assert all(call[0] == "billing.issue_refund" for call in gateway.calls)

    with db.session_scope(factory) as session:
        call = session.get(models.ToolCall, tool_call_id)
    assert call is not None
    assert call.status == "executed"
    assert call.result is not None

    completed = await SqlRunStore(factory).get(run.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED

    # A second drain does nothing further: the run is terminal, so it is not
    # claimed, and no second refund is issued.
    assert await _drain(factory, gateway) is False
    assert len(gateway.refunds) == 1
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# Reject -> COMPLETED with an escalation reply, and no refund
# ---------------------------------------------------------------------------


async def test_rejected_refund_completes_with_escalation_and_no_refund(
    factory: sessionmaker[Session],
) -> None:
    run = await _make_run(factory)
    gateway = FakeBillingServer()
    tool_call_id = await _park(factory, run, gateway)

    approval_id = await _approval_for(factory, tool_call_id)
    from datetime import UTC, datetime

    with db.session_scope(factory) as session:
        decided = await SqlApprovalStore(session).decide(
            approval_id, approved=False, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
    assert decided.status.value == "rejected"
    requeued = await SqlRunStore(factory).get(run.id)
    assert requeued is not None
    # A rejection is not a failure: the run is re-queued to RESPONDING.
    assert requeued.status is RunStatus.RESPONDING

    processed = await _drain(factory, gateway)
    assert processed is True

    # No refund, ever.
    assert gateway.refunds == {}
    assert gateway.calls == []

    with db.session_scope(factory) as session:
        call = session.get(models.ToolCall, tool_call_id)
    assert call is not None
    assert call.status == "awaiting_approval"  # never executed

    # The run reached COMPLETED -- the workflow working -- with an escalated reply.
    completed = await SqlRunStore(factory).get(run.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED
    assert completed.failure_reason is None

    response_steps = [s for s in _steps(factory, run.id) if s.step_type == "response"]
    assert response_steps
    assert (response_steps[-1].output or {})["escalated"] is True


# ---------------------------------------------------------------------------
# Gap A: a repeat proposal for the same (run, transaction) short-circuits
# ---------------------------------------------------------------------------


async def test_repeat_proposal_is_a_remembered_replay_not_a_second_approval(
    factory: sessionmaker[Session],
) -> None:
    """Gate 4's idempotency lookup (Gap A), through the production wiring.

    Once the refund has executed for this run and transaction, a *fresh*
    proposal with the same derived key must short-circuit: it must not park for a
    second approval, and it must not trip the partial unique index. This drives
    ``run_loop`` with no ``executed_lookup`` argument, so the lookup the runtime
    builds itself (``executed_lookup_for`` over the tool-call store) is the one
    under test -- which is exactly what the M3 default did not provide.
    """
    from opspilot.agents.runtime import run_loop
    from opspilot.agents.schemas import ProposedAction

    run = await _make_run(factory)

    # Seed the prior execution directly: an ``executed`` refund row for this run
    # under the key the gate derives, exactly as a completed earlier attempt
    # would have left it.
    key = f"refund:{run.id}:TX-88219"
    with db.session_scope(factory) as session:
        call = SqlToolCallStore(session)
        first = await call.record_proposed(
            run_id=run.id,
            tool_name="billing.issue_refund",
            arguments={"transaction_id": "TX-88219", "amount": 129.0},
            permission="high_risk_write",
        )
        await call.set_status(
            first,
            ToolCallStatus.EXECUTED,
            result={"refund_id": "RFD-0001", "transaction_id": "TX-88219"},
            idempotency_key=key,
        )

    gateway = FakeBillingServer()
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge",
        ticket_body="charged twice",
        customer_email="billing@acme.example",
    )
    proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": 129.0},
    )
    # No ``executed_lookup`` argument: the runtime wires the real one.
    result = await run_loop(
        ctx,
        [proposal],
        provider=ScriptedProvider(),
        gateway=gateway,
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )

    # It did not park (it would have raised RunParked) and did not dispatch.
    assert result.run.status is RunStatus.EXECUTING
    assert gateway.calls == []
    assert gateway.refunds == {}
    # The duplicate is recorded as rejected, carrying the remembered result; the
    # original executed row still solely owns the key.
    duplicate = result.executed_tool_calls[-1]
    assert duplicate.status == "rejected"
    assert duplicate.result is not None
    assert duplicate.result["refund_id"] == "RFD-0001"
    with db.session_scope(factory) as session:
        keyed = (
            session.execute(
                select(models.ToolCall).where(
                    models.ToolCall.idempotency_key.is_not(None),
                    models.ToolCall.status == "executed",
                )
            )
            .scalars()
            .all()
        )
    assert len(keyed) == 1


def _steps(factory: sessionmaker[Session], run_id: UUID) -> list[models.AgentStep]:
    with db.session_scope(factory) as session:
        return list(
            session.execute(
                select(models.AgentStep)
                .where(models.AgentStep.run_id == run_id)
                .order_by(models.AgentStep.sequence)
            )
            .scalars()
            .all()
        )
