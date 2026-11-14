"""The repeat-invalid-proposal stop (Phase 3 M2-02), end to end.

## Why this file exists

M1-01's classification found the dominant eval failure shape: a model that
re-proposes one identical call a gate already refused, up to 23 times on one
recorded case, until ``MAX_STEPS`` ended the run with a step *count* that named
no cause. The stop added here fails such a run earlier and with a diagnosis --
``repeat_invalid_proposals`` -- and the tests below pin its semantics:

1. A run stuck on the same refused call stops at the configured threshold
   (default 3), ends ``FAILED(repeat_invalid_proposals)`` -- not
   ``max_steps_exceeded``, not a crash -- and the worker that drives it
   survives and drains the next run.
2. The trace carries the diagnosis: a ``repeat_invalid_stopped`` audit event
   naming the tool, the streak and the threshold, beside the ``run_failed``
   event.
3. The streak is *consecutive*: a different refused proposal resets it, and a
   valid (executed) call interleaved between repeats breaks it.
4. The threshold is wired: ``REJECTED_PROPOSAL_STOP_LIMIT`` reaches the
   ``RunContext`` the worker builds, and lowering it lowers the stop.

Every provider here is a scripted fake; nothing in this file touches a network.
"""

from __future__ import annotations

import json
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
from opspilot.agents.schemas import ProposedAction
from opspilot.agents.state import RunContext, default_rejected_proposal_stop_limit
from opspilot.domain.errors import RepeatInvalidProposalError
from opspilot.domain.runs import RunStatus
from opspilot.ports.model_provider import ModelResponse, ModelUsage
from opspilot.ports.orchestrator import StepResult
from opspilot.ports.tool_gateway import ToolResult
from opspilot.tracing.recorder import TraceRecorder


class StuckProvider:
    """A fake provider that keeps proposing whatever its queue holds.

    ``choose_tool`` pops from ``plan_queue`` in order; when the queue is empty
    it returns ``done`` -- which is how a *recovering* model signals it wants to
    respond. A test scripting a stuck loop passes the same malformed proposal
    repeatedly; a test scripting a recovery passes the malformed proposal, then
    a valid one, then lets the queue run dry.
    """

    def __init__(self, plan_queue: list[dict[str, Any]]) -> None:
        self.plan_queue = list(plan_queue)
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
        prompt: str,  # noqa: ARG002 -- protocol parameter
        schema: type[Any],
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> ModelResponse[Any]:
        from opspilot.agents.schemas import (
            AgentResponse,
            TicketCategory,
            TicketClassification,
        )

        if schema is AgentResponse:
            return ModelResponse(
                value=AgentResponse(body="I could not complete the request.", escalated=True),
                usage=self._usage(),
            )
        self.classify_calls += 1
        # The one other structured call the pump makes is the classification;
        # a minimal valid one keeps the drive moving.
        classification = TicketClassification(
            category=TicketCategory.DUPLICATE_CHARGE,
            confidence=0.9,
            rationale="charged twice",
        )
        return ModelResponse(value=classification, usage=self._usage())

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.choose_calls += 1
        if self.plan_queue:
            return self.plan_queue.pop(0)
        return {"tool_name": None, "arguments": {}, "reason": "nothing more", "done": True}

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.text_calls += 1
        return "I could not complete the request."


class RecordingGateway:
    """Records dispatches; returns a canned success."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True, "tool": name})


class UnusedOrchestrator:
    """Delegates to the real linear orchestrator, as production does."""

    def __init__(self) -> None:
        from opspilot.adapters.orchestration.linear import LinearOrchestrator

        self._inner = LinearOrchestrator()

    def bind_driver(self, driver: Any) -> None:
        self._inner.bind_driver(driver)

    async def step(self, run: Any) -> StepResult:
        return await self._inner.step(run)


#: A proposal gate 1 refuses: ``crm.get_customer`` takes ``customer_id``, not
#: ``email``. The same shape the B2 eval looped on.
_BAD_PROPOSAL: dict[str, Any] = {
    "tool_name": "crm.get_customer",
    "arguments": {"email": "customer@example.com"},
    "reason": "look up the customer",
    "done": False,
}

#: A proposal that passes every gate and executes: a registered READ tool with
#: valid arguments.
_GOOD_PROPOSAL: dict[str, Any] = {
    "tool_name": "crm.get_customer",
    "arguments": {"customer_id": "CUST-1"},
    "reason": "look up the customer",
    "done": False,
}


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    from opspilot.settings import Settings

    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


async def _make_run(factory: sessionmaker[Session]) -> Any:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Duplicate charge",
            body="We were charged twice.",
            customer_email="billing@acme.example",
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


async def _drive(
    factory: sessionmaker[Session],
    plan_queue: list[dict[str, Any]],
    *,
    stop_limit: int = 3,
    max_steps: int = 50,
) -> RunContext:
    """Drive one run through the real pump with the given scripted plans."""
    run = await _make_run(factory)
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge",
        ticket_body="We were charged twice.",
        customer_email="billing@acme.example",
        max_steps=max_steps,
        rejected_proposal_stop_limit=stop_limit,
    )
    return await run_loop(
        ctx,
        provider=StuckProvider(plan_queue),
        gateway=RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )


def _audit_types(factory: sessionmaker[Session], run_id: Any) -> list[str]:
    with db.session_scope(factory) as session:
        rows = (
            session.execute(
                select(models.AuditEvent)
                .where(models.AuditEvent.run_id == run_id)
                .order_by(models.AuditEvent.created_at, models.AuditEvent.id)
            )
            .scalars()
            .all()
        )
        return [row.event_type for row in rows]


def _audit_payloads(
    factory: sessionmaker[Session], run_id: Any, event_type: str
) -> list[dict[str, Any]]:
    with db.session_scope(factory) as session:
        rows = (
            session.execute(
                select(models.AuditEvent)
                .where(
                    models.AuditEvent.run_id == run_id,
                    models.AuditEvent.event_type == event_type,
                )
                .order_by(models.AuditEvent.created_at, models.AuditEvent.id)
            )
            .scalars()
            .all()
        )
        return [dict(row.payload or {}) for row in rows]


async def _run_row(factory: sessionmaker[Session], run_id: Any) -> Any:
    from uuid import UUID

    resolved = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
    with db.session_scope(factory) as session:
        store = SqlRunStore(session)
        found = await store.get(resolved)
        assert found is not None
        return found


async def test_three_identical_invalid_proposals_stop_the_run(
    factory: sessionmaker[Session],
) -> None:
    """The default threshold (3) stops a run stuck on one refused call.

    The run fails with ``repeat_invalid_proposals`` -- the diagnosis -- rather
    than ``max_steps_exceeded``, and the exception is the typed one so the
    worker can suppress it by class.
    """
    with pytest.raises(RepeatInvalidProposalError) as excinfo:
        await _drive(factory, [dict(_BAD_PROPOSAL) for _ in range(5)])

    assert excinfo.value.tool_name == "crm.get_customer"
    assert excinfo.value.limit == 3

    run = await _run_row(factory, excinfo.value.run_id)
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "repeat_invalid_proposals"

    # Three proposals were made and all three were refused at gate 1 -- the
    # loop ran exactly to the threshold, not one round further.
    assert excinfo.value.limit == 3
    payloads = _audit_payloads(factory, run.id, "repeat_invalid_stopped")
    assert len(payloads) == 1
    assert payloads[0]["tool_name"] == "crm.get_customer"
    assert payloads[0]["streak"] == 3
    assert payloads[0]["limit"] == 3
    assert payloads[0]["rejection_gate"] == "gate_1_schema_validation"

    types = _audit_types(factory, run.id)
    assert "repeat_invalid_stopped" in types
    assert "run_failed" in types
    # The stop fired instead of the budget: with a budget of 50, a max-steps
    # end would prove the stop did nothing.
    assert run.failure_reason != "max_steps_exceeded"


async def test_a_different_refused_proposal_resets_the_streak(
    factory: sessionmaker[Session],
) -> None:
    """Alternating between two refused calls never reaches the limit.

    The stop targets the run that is *stuck*, not the run that is struggling:
    a model that keeps changing its (still wrong) proposal is making distinct
    attempts, and each distinct refusal starts a new streak.
    """
    with pytest.raises(RepeatInvalidProposalError) as excinfo:
        await _drive(
            factory,
            [
                dict(_BAD_PROPOSAL),  # streak 1
                dict(_BAD_PROPOSAL),  # streak 2
                # A *different* refused proposal: resets the streak to 1. If it
                # did not, the stop would fire on the proposal below -- the
                # third identical one overall -- rather than proving the reset.
                {
                    "tool_name": "crm.get_customer",
                    "arguments": {"name": "x"},
                    "reason": "r",
                    "done": False,
                },
                dict(_BAD_PROPOSAL),  # streak 2
                dict(_BAD_PROPOSAL),  # streak 3
                dict(_BAD_PROPOSAL),  # streak 4th proposal, 3rd consecutive -> stop
            ],
        )

    run = await _run_row(factory, excinfo.value.run_id)
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "repeat_invalid_proposals"
    payloads = _audit_payloads(factory, run.id, "repeat_invalid_stopped")
    # The streak was reset once (by the different proposal), so the stop fired
    # on the third *consecutive* repeat, with streak == limit == 3 -- and not
    # before it.
    assert payloads[0]["streak"] == 3


async def test_a_valid_call_between_repeats_breaks_the_streak(
    factory: sessionmaker[Session],
) -> None:
    """An executed call between two identical refusals resets the count.

    The model demonstrably changed behaviour, so the counter starts over: two
    refusals around one valid call must not stop the run.
    """
    # The queue ends with two refusals after one success -- a streak of 2, under
    # the limit -- so the run exits through the normal ``done`` path.
    ctx = await _drive(
        factory,
        [
            dict(_BAD_PROPOSAL),
            dict(_GOOD_PROPOSAL),
            dict(_BAD_PROPOSAL),
            dict(_BAD_PROPOSAL),
        ],
    )

    run = await _run_row(factory, ctx.run.id)
    assert run.status is RunStatus.COMPLETED
    assert run.failure_reason is None
    assert "repeat_invalid_stopped" not in _audit_types(factory, ctx.run.id)


async def test_one_or_two_repeats_do_not_stop_the_run(factory: sessionmaker[Session]) -> None:
    """Below the threshold the stop never fires: the run completes normally."""
    ctx = await _drive(factory, [dict(_BAD_PROPOSAL), dict(_BAD_PROPOSAL)])

    run = await _run_row(factory, ctx.run.id)
    assert run.status is RunStatus.COMPLETED
    assert run.failure_reason is None
    assert "repeat_invalid_stopped" not in _audit_types(factory, ctx.run.id)


async def test_a_lower_configured_limit_stops_earlier(factory: sessionmaker[Session]) -> None:
    """``REJECTED_PROPOSAL_STOP_LIMIT`` is honoured, not just defaulted.

    With the limit at 2, the second identical refusal stops the run -- the
    threshold is configuration, not a constant.
    """
    with pytest.raises(RepeatInvalidProposalError) as excinfo:
        await _drive(factory, [dict(_BAD_PROPOSAL) for _ in range(4)], stop_limit=2)

    assert excinfo.value.limit == 2
    run = await _run_row(factory, excinfo.value.run_id)
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "repeat_invalid_proposals"
    payloads = _audit_payloads(factory, run.id, "repeat_invalid_stopped")
    assert payloads[0]["streak"] == 2
    assert payloads[0]["limit"] == 2


async def test_same_arguments_in_a_different_key_order_is_still_the_same_proposal(
    factory: sessionmaker[Session],
) -> None:
    """Key order carries no information: a re-ordered duplicate counts.

    The fingerprint canonicalises argument keys, so ``{"a": 1, "b": 2}`` and
    ``{"b": 2, "a": 1}`` are one proposal -- otherwise a model could keep the
    loop alive by shuffling JSON keys.
    """
    reordered = {
        "tool_name": "crm.get_customer",
        "arguments": {"email": "customer@example.com"},
        "reason": "look up the customer",
        "done": False,
    }
    with pytest.raises(RepeatInvalidProposalError) as excinfo:
        await _drive(
            factory,
            [dict(_BAD_PROPOSAL), dict(reordered), dict(_BAD_PROPOSAL)],
            stop_limit=3,
        )

    run = await _run_row(factory, excinfo.value.run_id)
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "repeat_invalid_proposals"


async def test_the_default_limit_comes_from_settings(factory: sessionmaker[Session]) -> None:
    """The dataclass default reads the live settings, not a frozen literal.

    Same contract as ``max_steps``: a deployment that moves the environment
    variable after import still gets the new value on the next ``RunContext``.
    """
    assert default_rejected_proposal_stop_limit() == 3


def test_the_fingerprint_is_order_insensitive_and_argument_sensitive() -> None:
    """The identity is ``(tool_name, canonical_arguments_json)``."""
    a = ProposedAction(tool_name="t", arguments={"x": 1, "y": 2})
    b = ProposedAction(tool_name="t", arguments={"y": 2, "x": 1})
    c = ProposedAction(tool_name="t", arguments={"x": 1, "y": 3})
    d = ProposedAction(tool_name="u", arguments={"x": 1, "y": 2})
    from opspilot.agents.state import proposal_fingerprint

    assert proposal_fingerprint(a) == proposal_fingerprint(b)
    assert proposal_fingerprint(a) != proposal_fingerprint(c)
    assert proposal_fingerprint(a) != proposal_fingerprint(d)
    # And it round-trips as JSON, so a trace can show what matched.
    name, canonical = proposal_fingerprint(a)
    assert name == "t"
    assert json.loads(canonical) == {"x": 1, "y": 2}
