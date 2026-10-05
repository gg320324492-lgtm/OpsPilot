"""Integration tests for the five gates, against the real SQLite repositories.

These are the tests that would catch a hole in the milestone. They use a stub
gateway (so nothing does I/O) and the *real* ``SqlRunStore`` / ``SqlToolCallStore``
/ ``SqlApprovalStore`` over an in-memory SQLite schema, so the database reads
gate 5 depends on -- ``has_approved`` -- are the ones production runs.

The properties asserted, in the order the gates run:

- gate 1 rejects malformed arguments and gate 2 rejects an unknown tool, neither
  reaching the gateway;
- a READ tool and a SAFE_WRITE tool execute automatically and write an audit
  event before returning;
- a HIGH_RISK_WRITE tool with no approval raises ``RunParked``, creates a pending
  approval, and executes *nothing*;
- the same call with an approved row executes exactly once;
- an approval binds to one ``tool_call_id``: approving TX-88219 does not
  authorise TX-11111.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
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
from opspilot.agents.runtime import _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.agents.state import RunContext, ToolCallRecord
from opspilot.domain.errors import RunParked
from opspilot.domain.runs import AgentRun
from opspilot.ports.model_provider import ModelResponse
from opspilot.ports.orchestrator import StepResult
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder


class RecordingGateway:
    """A ``ToolGateway`` that records every call and performs no I/O.

    This is the spy the tests assert on: "the rejected proposal was never
    dispatched" is only meaningful if ``call_tool`` is the thing that would have
    dispatched it, so the test asserts ``gateway.calls == []``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True, "echo": name})


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


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
    """The stores, recorder and gateway one gate test threads through."""

    def __init__(self, factory: sessionmaker[Session], run: AgentRun) -> None:
        self.run = run
        self.run_store = SqlRunStore(factory)
        self.tool_call_store = SqlToolCallStore(factory)
        self.approval_store = SqlApprovalStore(factory)
        self.recorder = TraceRecorder(run_id=run.id, session_factory=factory)
        self.gateway = RecordingGateway()
        self.factory = factory

    def context(self) -> RunContext:
        return RunContext(
            run=self.run,
            ticket_subject="duplicate charge",
            ticket_body="charged twice",
            customer_email="a@example.com",
        )

    async def gate(
        self, proposal: ProposedAction, *, tool_call_id: UUID | None = None
    ) -> ToolCallRecord:
        return await _gate_and_execute(
            self.context(),
            proposal,
            gateway=self.gateway,
            run_store=self.run_store,
            tool_call_store=self.tool_call_store,
            approval_store=self.approval_store,
            recorder=self.recorder,
            tool_call_id=tool_call_id,
        )


async def _harness(factory: sessionmaker[Session]) -> Harness:
    return Harness(factory, await _make_run(factory))


def _audit_types(factory: sessionmaker[Session]) -> list[str]:
    """Every audit event type written, oldest first."""
    with db.session_scope(factory) as session:
        rows = (
            session.execute(select(models.AuditEvent).order_by(models.AuditEvent.created_at))
            .scalars()
            .all()
        )
        return [row.event_type for row in rows]


def _tool_calls(factory: sessionmaker[Session], tool_name: str) -> list[models.ToolCall]:
    with db.session_scope(factory) as session:
        return list(
            session.execute(select(models.ToolCall).where(models.ToolCall.tool_name == tool_name))
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# READ and SAFE_WRITE execute automatically, and are audited
# ---------------------------------------------------------------------------


async def test_read_tool_executes_and_writes_an_audit_event(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)
    record = await harness.gate(
        ProposedAction(tool_name="crm.get_customer", arguments={"customer_id": "CUST-1"})
    )

    assert record.status == "executed"
    assert harness.gateway.calls == [("crm.get_customer", {"customer_id": "CUST-1"})]
    assert "tool_executed" in _audit_types(factory)


async def test_safe_write_executes_automatically_and_is_audited_before_returning(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)
    record = await harness.gate(
        ProposedAction(tool_name="issues.create", arguments={"title": "t", "body": "b"})
    )

    assert record.status == "executed"
    assert len(harness.gateway.calls) == 1
    # The audit event is on disk by the time the gate returns -- not merely
    # scheduled. This is the "written before returning" property.
    assert "tool_executed" in _audit_types(factory)


async def test_permission_is_snapshotted_from_the_registry(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)
    await harness.gate(
        ProposedAction(tool_name="issues.create", arguments={"title": "t", "body": "b"})
    )
    rows = _tool_calls(factory, "issues.create")
    assert len(rows) == 1
    assert rows[0].permission == "safe_write"


# ---------------------------------------------------------------------------
# HIGH_RISK_WRITE without approval parks the run and executes nothing
# ---------------------------------------------------------------------------


async def test_high_risk_without_approval_parks_and_executes_nothing(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)

    with pytest.raises(RunParked):
        await harness.gate(
            ProposedAction(
                tool_name="billing.issue_refund",
                arguments={"transaction_id": "TX-88219", "amount": "129.00"},
                reason="duplicate charge confirmed",
            )
        )

    # Nothing was dispatched.
    assert harness.gateway.calls == []

    # A pending approval exists, bound to the tool call, with the deterministic
    # risk explanation and the arguments as shown.
    with db.session_scope(factory) as session:
        approvals = session.execute(select(models.ApprovalRequest)).scalars().all()
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.status == "pending"
    assert approval.decided_by is None
    assert "$129.00" in approval.risk_explanation
    assert approval.reason == "duplicate charge confirmed"  # the model's untrusted text
    assert approval.arguments_snapshot["transaction_id"] == "TX-88219"

    # The run is parked, and the tool call is awaiting approval.
    parked = await harness.run_store.get(harness.run.id)
    assert parked is not None
    assert parked.status.value == "waiting_approval"
    calls = _tool_calls(factory, "billing.issue_refund")
    assert len(calls) == 1
    assert calls[0].status == "awaiting_approval"


# ---------------------------------------------------------------------------
# HIGH_RISK_WRITE with approval executes exactly once
# ---------------------------------------------------------------------------


async def _approve(factory: sessionmaker[Session], tool_call_id: UUID) -> None:
    """Approve the (single) approval bound to ``tool_call_id``."""
    with db.session_scope(factory) as session:
        row = session.execute(
            select(models.ApprovalRequest).where(
                models.ApprovalRequest.tool_call_id == tool_call_id
            )
        ).scalar_one()
        row.status = "approved"
        row.decided_at = datetime.now(UTC)
        row.decided_by = "operator:1"


async def test_high_risk_with_approval_executes_exactly_once(
    factory: sessionmaker[Session],
) -> None:
    # NOTE ON THE STUB: ``RecordingGateway`` accepts *any* arguments and returns a
    # canned success, so it cannot see whether the payload the gate dispatches
    # actually satisfies the real tool's signature. A missing required
    # ``idempotency_key`` on ``billing.issue_refund`` passed here for exactly that
    # reason while failing against the real MCP server. That contract is covered
    # by ``tests/integration/test_gate_dispatch_contract.py``, which drives the
    # real ``MCPToolGateway``; this file keeps the stub so it can assert gate
    # ordering and that a rejected proposal is never dispatched.
    harness = await _harness(factory)
    proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
        reason="duplicate charge confirmed",
    )

    # First pass parks.
    with pytest.raises(RunParked) as parked:
        await harness.gate(proposal)
    tool_call_id = UUID(parked.value.tool_call_id)

    # A human approves that specific call.
    await _approve(factory, tool_call_id)

    # Resume: the worker re-enters the gates for the same tool call.
    record = await harness.gate(proposal, tool_call_id=tool_call_id)

    assert record.status == "executed"
    assert len(harness.gateway.calls) == 1  # exactly once
    assert _tool_calls(factory, "billing.issue_refund")[0].status == "executed"


async def test_resume_without_approval_stays_parked(
    factory: sessionmaker[Session],
) -> None:
    """Re-entering the gates without an approved row parks again -- it does not
    execute because a row exists."""
    harness = await _harness(factory)
    proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
    )
    with pytest.raises(RunParked) as parked:
        await harness.gate(proposal)
    tool_call_id = UUID(parked.value.tool_call_id)

    with pytest.raises(RunParked):
        await harness.gate(proposal, tool_call_id=tool_call_id)
    assert harness.gateway.calls == []


# ---------------------------------------------------------------------------
# Approval binds to a tool_call_id, never to a run
# ---------------------------------------------------------------------------


async def test_approval_for_one_transaction_does_not_authorise_another(
    factory: sessionmaker[Session],
) -> None:
    """The single most important detail in the milestone: approving a refund for
    TX-88219 must not authorise a call for TX-11111."""
    harness = await _harness(factory)

    first = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
        reason="the approved one",
    )
    with pytest.raises(RunParked) as parked:
        await harness.gate(first)
    approved_call_id = UUID(parked.value.tool_call_id)
    await _approve(factory, approved_call_id)

    # A *different* transaction, for the same run, is a *different* tool call.
    second = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-11111", "amount": "5.00"},
        reason="a different proposal",
    )
    with pytest.raises(RunParked):
        await harness.gate(second)

    # Nothing executed: the TX-11111 proposal saw its own (absent) approval.
    assert harness.gateway.calls == []

    # The first call is still awaiting its own resume; the second has its own
    # pending approval.
    with db.session_scope(factory) as session:
        approvals = session.execute(select(models.ApprovalRequest)).scalars().all()
    assert len(approvals) == 2
    assert {a.tool_call_id for a in approvals} == {
        approved_call_id,
        next(a.tool_call_id for a in approvals if a.tool_call_id != approved_call_id),
    }


async def test_approved_call_for_one_transaction_executes_only_that_one(
    factory: sessionmaker[Session],
) -> None:
    """With one call approved, resuming it executes; a fresh proposal for the
    other transaction still parks."""
    harness = await _harness(factory)

    approved_proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
    )
    with pytest.raises(RunParked) as parked:
        await harness.gate(approved_proposal)
    approved_call_id = UUID(parked.value.tool_call_id)
    await _approve(factory, approved_call_id)

    other_proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-11111", "amount": "5.00"},
    )
    with pytest.raises(RunParked):
        await harness.gate(other_proposal)

    record = await harness.gate(approved_proposal, tool_call_id=approved_call_id)
    assert record.status == "executed"
    assert len(harness.gateway.calls) == 1
    assert harness.gateway.calls[0][1]["transaction_id"] == "TX-88219"


# ---------------------------------------------------------------------------
# Gate 1 and gate 2 reject before the gateway
# ---------------------------------------------------------------------------


async def test_unknown_tool_is_rejected_at_gate_2_and_never_dispatched(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)
    record = await harness.gate(
        ProposedAction(tool_name="billing.wire_money", arguments={"amount": "10000"})
    )

    assert record.status == "rejected"
    assert harness.gateway.calls == []
    rows = _tool_calls(factory, "billing.wire_money")
    assert len(rows) == 1
    assert rows[0].status == "rejected"
    assert rows[0].rejection_reason == "gate_2_registry_lookup"
    # Recorded at the least-privileged level: a rejected call must never look
    # like a high-risk execution to the CI invariant.
    assert rows[0].permission == "read"
    assert "tool_rejected" in _audit_types(factory)


async def test_malformed_arguments_are_rejected_at_gate_1(
    factory: sessionmaker[Session],
) -> None:
    harness = await _harness(factory)
    record = await harness.gate(
        ProposedAction(tool_name="crm.get_customer", arguments={"not_a_real_field": 1})
    )

    assert record.status == "rejected"
    assert harness.gateway.calls == []
    rows = _tool_calls(factory, "crm.get_customer")
    assert len(rows) == 1
    assert rows[0].status == "rejected"
    assert rows[0].rejection_reason == "gate_1_schema_validation"


async def test_gate_1_runs_before_gate_2(factory: sessionmaker[Session]) -> None:
    """An unregistered tool with malformed arguments is a gate-2 rejection,
    because gate 1 has no schema to fail against for an unknown tool -- the
    registered-tool case is where gate 1 does the work."""
    harness = await _harness(factory)
    record = await harness.gate(ProposedAction(tool_name="nope", arguments={"bad": 1}))
    assert record.status == "rejected"
    rows = _tool_calls(factory, "nope")
    assert rows[0].rejection_reason == "gate_2_registry_lookup"


# ---------------------------------------------------------------------------
# The CI invariant, checked here on the gate's own output
# ---------------------------------------------------------------------------


async def test_no_high_risk_execution_without_an_approved_row(
    factory: sessionmaker[Session],
) -> None:
    """The §6 invariant, evaluated over a run that parked, got approved, and
    executed: zero unapproved high-risk executions."""
    harness = await _harness(factory)
    proposal = ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
    )
    with pytest.raises(RunParked) as parked:
        await harness.gate(proposal)
    await _approve(factory, UUID(parked.value.tool_call_id))
    await harness.gate(proposal, tool_call_id=UUID(parked.value.tool_call_id))

    with db.session_scope(factory) as session:
        executed = list(
            session.execute(
                select(models.ToolCall).where(
                    models.ToolCall.permission == "high_risk_write",
                    models.ToolCall.status == "executed",
                )
            )
            .scalars()
            .all()
        )
        assert executed, "the approved call should have executed"
        for call in executed:
            approval = session.execute(
                select(models.ApprovalRequest).where(
                    models.ApprovalRequest.tool_call_id == call.id,
                    models.ApprovalRequest.status == "approved",
                )
            ).scalar_one_or_none()
            assert approval is not None, "a high-risk execution without an approved row"


def test_the_recording_gateway_is_a_real_spy() -> None:
    """A guard that passes because it inspected nothing is worse than a failing
    one, because the pass is the part people read. This asserts the spy actually
    records, so ``gateway.calls == []`` above means "nothing was dispatched"."""
    import asyncio

    gateway = RecordingGateway()
    asyncio.run(gateway.call_tool("x", {"a": 1}))
    assert gateway.calls == [("x", {"a": 1})]


# ---------------------------------------------------------------------------
# The run loop: MAX_STEPS, RunParked propagation
# ---------------------------------------------------------------------------


class _UnusedOrchestrator:
    """A placeholder orchestrator -- the loop takes one but does not use it.

    Conforms to ``Orchestrator`` so ``run_loop`` type-checks; a call would raise,
    which is the point -- the loop must not consult it.
    """

    async def step(self, run: AgentRun) -> StepResult:
        raise NotImplementedError


class _UnusedProvider:
    """A placeholder provider -- proposals are passed to ``run_loop`` directly.

    Conforms to ``ModelProvider``; every method raises, because the loop must not
    ask the model anything.
    """

    async def generate_structured(self, **kwargs: object) -> ModelResponse[Any]:
        raise NotImplementedError

    async def choose_tool(self, **kwargs: object) -> dict[str, Any]:
        raise NotImplementedError

    async def generate_text(self, **kwargs: object) -> str:
        raise NotImplementedError


async def test_run_loop_executes_a_read_proposal(factory: sessionmaker[Session]) -> None:
    from opspilot.agents.runtime import run_loop

    harness = await _harness(factory)
    result = await run_loop(
        harness.context(),
        [ProposedAction(tool_name="crm.get_customer", arguments={"customer_id": "C1"})],
        provider=_UnusedProvider(),
        gateway=harness.gateway,
        orchestrator=_UnusedOrchestrator(),
        run_store=harness.run_store,
        tool_call_store=harness.tool_call_store,
        approval_store=harness.approval_store,
        recorder=harness.recorder,
    )
    assert result.steps_taken == 1
    assert harness.gateway.calls == [("crm.get_customer", {"customer_id": "C1"})]
    reloaded = await harness.run_store.get(harness.run.id)
    assert reloaded is not None
    assert reloaded.status.value == "executing"


async def test_run_loop_lets_run_parked_propagate(factory: sessionmaker[Session]) -> None:
    """The loop must not catch ``RunParked``: a parked run is the workflow
    working, and swallowing it here is the one mistake that turns "did its job"
    into "failed"."""
    from opspilot.agents.runtime import run_loop

    harness = await _harness(factory)
    with pytest.raises(RunParked):
        await run_loop(
            harness.context(),
            [
                ProposedAction(
                    tool_name="billing.issue_refund",
                    arguments={"transaction_id": "TX-88219", "amount": "129.00"},
                )
            ],
            provider=_UnusedProvider(),
            gateway=harness.gateway,
            orchestrator=_UnusedOrchestrator(),
            run_store=harness.run_store,
            tool_call_store=harness.tool_call_store,
            approval_store=harness.approval_store,
            recorder=harness.recorder,
        )
    assert harness.gateway.calls == []
    parked = await harness.run_store.get(harness.run.id)
    assert parked is not None
    assert parked.status.value == "waiting_approval"
    assert parked.failure_reason is None  # parking is not a failure


async def test_run_loop_fails_the_run_when_the_budget_is_exhausted(
    factory: sessionmaker[Session],
) -> None:
    from opspilot.agents.runtime import run_loop
    from opspilot.domain.errors import MaxStepsExceeded

    harness = await _harness(factory)
    context = harness.context()
    context.steps_taken = context.max_steps  # already at the budget

    with pytest.raises(MaxStepsExceeded):
        await run_loop(
            context,
            [],
            provider=_UnusedProvider(),
            gateway=harness.gateway,
            orchestrator=_UnusedOrchestrator(),
            run_store=harness.run_store,
            tool_call_store=harness.tool_call_store,
            approval_store=harness.approval_store,
            recorder=harness.recorder,
        )

    failed = await harness.run_store.get(harness.run.id)
    assert failed is not None
    assert failed.status.value == "failed"
    assert failed.failure_reason == "max_steps_exceeded"
    assert "run_failed" in _audit_types(factory)
