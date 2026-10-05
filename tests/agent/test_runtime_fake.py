"""The provider-driven pump, end to end, on a scripted fake provider.

This replaces the M0 placeholder. It drives ``run_loop`` with no ``proposals``
argument, so the provider-driven path is exercised: the loop classifies,
retrieves, plans, executes through the gates, responds and completes -- and every
state change is persisted with its ``AgentStep``.

The fake provider is defined here rather than imported from
``opspilot.adapters.models.fake`` (a scripted-fixture provider filled in
alongside this milestone): the pump's contract must be testable without that
module, and a local fake keeps the assertions independent of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

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
from opspilot.agents.runtime import run_loop
from opspilot.agents.schemas import (
    AgentResponse,
    ProposedAction,
    TicketCategory,
    TicketClassification,
)
from opspilot.agents.state import RunContext
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.model_provider import ModelResponse, ModelUsage
from opspilot.ports.orchestrator import StepResult
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder

_INVALID_CLASSIFICATION = "classification did not validate"
_UNEXPECTED_SCHEMA = "unexpected schema requested"


class ScriptedProvider:
    """A deterministic provider: a fixed classification, a queue of tool choices,
    and a fixed reply.

    ``choose_tool`` pops from ``tool_calls`` in order, so a test scripts the exact
    sequence of proposals without any model. Once the queue is empty it returns a
    ``done`` proposal, which is how the pump is told to respond.
    """

    def __init__(
        self,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        reply: str = "Your refund has been processed.",
        escalated_reply: str = "A refund was declined; escalating to a human.",
        invalid_classification: bool = False,
    ) -> None:
        self.tool_calls = list(tool_calls or [])
        self.reply = reply
        self.escalated_reply = escalated_reply
        self.invalid_classification = invalid_classification
        self.classify_calls = 0
        self.choose_calls = 0
        self.text_calls = 0

    def _usage(self) -> ModelUsage:
        return ModelUsage(
            provider="fake",
            model="fake-1",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        )

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,
        schema: type[Any],
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> ModelResponse[Any]:
        if schema is TicketClassification:
            self.classify_calls += 1
            if self.invalid_classification:
                raise ValueError(_INVALID_CLASSIFICATION)
            value: Any = TicketClassification(
                category=TicketCategory.DUPLICATE_CHARGE,
                confidence=0.9,
                rationale="charged twice",
            )
        elif schema is AgentResponse:
            # The reply is escalated when the pump marked the run escalated.
            escalated = "declined" in prompt or "escalation" in prompt.lower()
            body = self.escalated_reply if escalated else self.reply
            value = AgentResponse(body=body, escalated=escalated)
        else:  # pragma: no cover - the pump only asks for the two schemas
            raise AssertionError(_UNEXPECTED_SCHEMA)
        return ModelResponse(value=value, usage=self._usage())

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.choose_calls += 1
        if self.tool_calls:
            return self.tool_calls.pop(0)
        return {"tool_name": None, "arguments": {}, "done": True}

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.text_calls += 1
        return self.reply


class RecordingGateway:
    """A gateway that records calls and returns a canned success."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True, "tool": name})


class UnusedOrchestrator:
    """The pump takes an orchestrator but must not consult it."""

    async def step(self, run: AgentRun) -> StepResult:
        raise NotImplementedError


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _make_run(factory: sessionmaker[Session]) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Duplicate charge on INV-2026-384",
            body="We were charged twice for the same invoice.",
            customer_email="billing@acme.example",
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


async def _drive(
    factory: sessionmaker[Session],
    run: AgentRun,
    provider: ScriptedProvider,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
) -> RunContext:
    if tool_calls is not None:
        provider.tool_calls = list(tool_calls)
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge on INV-2026-384",
        ticket_body="We were charged twice for the same invoice.",
        customer_email="billing@acme.example",
    )
    return await run_loop(
        ctx,
        provider=provider,
        gateway=RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )


def _steps(factory: sessionmaker[Session], run_id: object) -> list[models.AgentStep]:
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


async def test_pump_reaches_completed_and_persists_every_state(
    factory: sessionmaker[Session],
) -> None:
    run = await _make_run(factory)
    provider = ScriptedProvider()
    result = await _drive(factory, run, provider, tool_calls=[])
    # The pump is driven entirely by the provider.
    assert provider.classify_calls == 1
    assert provider.choose_calls == 1
    assert result.run.status is RunStatus.COMPLETED
    assert result.response_body


async def test_every_state_transition_is_persisted_in_order(
    factory: sessionmaker[Session],
) -> None:
    run = await _make_run(factory)
    await _drive(factory, run, ScriptedProvider(), tool_calls=[])
    steps = _steps(factory, run.id)
    state_steps = [s for s in steps if s.step_type == "state_change"]
    to_statuses = [(s.output or {})["to_status"] for s in state_steps]
    assert to_statuses == [
        "classifying",
        "retrieving",
        "planning",
        "responding",
        "completed",
    ]
    # Every step carries a strictly increasing sequence.
    sequences = [s.sequence for s in steps]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, len(steps) + 1))


async def test_read_tool_runs_then_the_run_responds_and_completes(
    factory: sessionmaker[Session],
) -> None:
    run = await _make_run(factory)
    provider = ScriptedProvider()
    provider.tool_calls = [
        {
            "tool_name": "crm.get_customer",
            "arguments": {"customer_id": "CUST-1"},
            "reason": "look up the customer",
        }
    ]
    ctx = RunContext(
        run=run,
        ticket_subject="s",
        ticket_body="b",
        customer_email="c@example.com",
    )
    gateway = RecordingGateway()
    result = await run_loop(
        ctx,
        provider=provider,
        gateway=gateway,
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )
    assert gateway.calls == [("crm.get_customer", {"customer_id": "CUST-1"})]
    assert result.run.status is RunStatus.COMPLETED
    assert result.steps_taken == 1
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.COMPLETED


async def test_high_risk_proposal_parks_the_run(factory: sessionmaker[Session]) -> None:
    from opspilot.domain.errors import RunParked

    run = await _make_run(factory)
    provider = ScriptedProvider()
    provider.tool_calls = [
        {
            "tool_name": "billing.issue_refund",
            "arguments": {"transaction_id": "TX-88219", "amount": 129.0},
            "reason": "duplicate charge confirmed",
        }
    ]
    ctx = RunContext(
        run=run,
        ticket_subject="s",
        ticket_body="b",
        customer_email="c@example.com",
    )
    with pytest.raises(RunParked):
        await run_loop(
            ctx,
            provider=provider,
            gateway=RecordingGateway(),
            orchestrator=UnusedOrchestrator(),
            run_store=SqlRunStore(factory),
            tool_call_store=SqlToolCallStore(factory),
            approval_store=SqlApprovalStore(factory),
            recorder=TraceRecorder(run_id=run.id, session_factory=factory),
        )
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.WAITING_APPROVAL
    assert reloaded.failure_reason is None


async def test_schema_invalid_classification_fails_the_run(
    factory: sessionmaker[Session],
) -> None:
    run = await _make_run(factory)
    provider = ScriptedProvider(invalid_classification=True)
    with pytest.raises(ValueError):
        await _drive(factory, run, provider, tool_calls=[])
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.FAILED
    assert reloaded.failure_reason == "schema_invalid"


async def test_max_steps_is_enforced(factory: sessionmaker[Session]) -> None:
    from opspilot.domain.errors import MaxStepsExceeded

    run = await _make_run(factory)
    provider = ScriptedProvider()
    provider.tool_calls = [
        {"tool_name": "crm.get_customer", "arguments": {"customer_id": f"C{i}"}} for i in range(50)
    ]
    ctx = RunContext(
        run=run,
        ticket_subject="s",
        ticket_body="b",
        customer_email="c@example.com",
        max_steps=2,
    )
    with pytest.raises(MaxStepsExceeded):
        await run_loop(
            ctx,
            provider=provider,
            gateway=RecordingGateway(),
            orchestrator=UnusedOrchestrator(),
            run_store=SqlRunStore(factory),
            tool_call_store=SqlToolCallStore(factory),
            approval_store=SqlApprovalStore(factory),
            recorder=TraceRecorder(run_id=run.id, session_factory=factory),
        )
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.FAILED
    assert reloaded.failure_reason == "max_steps_exceeded"


async def test_proposals_mode_still_works(factory: sessionmaker[Session]) -> None:
    """The M3 test path (an explicit proposals list) is preserved."""
    run = await _make_run(factory)
    ctx = RunContext(
        run=run,
        ticket_subject="s",
        ticket_body="b",
        customer_email="c@example.com",
    )
    gateway = RecordingGateway()
    result = await run_loop(
        ctx,
        [ProposedAction(tool_name="crm.get_customer", arguments={"customer_id": "C1"})],
        provider=ScriptedProvider(),
        gateway=gateway,
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )
    assert result.steps_taken == 1
    assert gateway.calls == [("crm.get_customer", {"customer_id": "C1"})]
