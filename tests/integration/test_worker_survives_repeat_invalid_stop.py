"""A run stopped by the repeat-invalid-proposal limit must not kill the worker.

## Why this file exists

``RepeatInvalidProposalError`` (Phase 3 M2-02) is raised by the runtime after
``_fail_run`` has already written ``FAILED(repeat_invalid_proposals)`` and the
``repeat_invalid_stopped`` audit event -- the same shape as
``MaxStepsExceeded``, and it belongs in the same ``contextlib.suppress`` group
in ``drain_once``. This file pins that: the worker survives a run stopped for
re-proposing one refused call, and the next run in the queue is still claimed
afterwards. Before the suppression existed this exception would have escaped
``poll_forever``'s handler-less loop and killed the process over an ordinary
business outcome.

The worker path is also where the ``REJECTED_PROPOSAL_STOP_LIMIT`` wiring is
proved end to end: the setting reaches the ``RunContext`` the worker builds,
so a deployment that lowers the limit lowers the stop.

Everything here is offline: the provider is the scripted fake, the stores are
the real ones over in-memory SQLite, and nothing touches a network.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from opspilot.agents.runtime import run_loop
from opspilot.agents.schemas import AgentResponse, TicketCategory, TicketClassification
from opspilot.agents.state import RunContext
from opspilot.domain.errors import RepeatInvalidProposalError
from opspilot.domain.runs import RunStatus
from opspilot.ports.model_provider import ModelProvider, ModelResponse, ModelUsage
from opspilot.ports.orchestrator import StepResult
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings, get_settings
from opspilot.tracing.recorder import TraceRecorder
from opspilot.worker.loop import drain_once


class _RepeatProvider:
    """A fake provider scripting a stuck loop, then a second, healthy run.

    ``run_index`` selects which script ``choose_tool`` follows: run 0 keeps
    proposing the same gate-1-refused call forever (the pump stops it at the
    limit); run 1 proposes one valid call and then finishes, so a queued
    run behind a stopped one still completes.
    """

    def __init__(self) -> None:
        self.run_index = 0
        self.calls = 0

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
        if schema is AgentResponse:
            return ModelResponse(
                value=AgentResponse(body="Done.", escalated=False),
                usage=self._usage(),
            )
        return ModelResponse(
            value=TicketClassification(
                category=TicketCategory.DUPLICATE_CHARGE,
                confidence=0.9,
                rationale="charged twice",
            ),
            usage=self._usage(),
        )

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.calls += 1
        if self.run_index == 0:
            # The stuck loop: the same malformed proposal every round. Gate 1
            # refuses it; the runtime stops the run at the configured limit.
            return {
                "tool_name": "crm.get_customer",
                "arguments": {"email": "customer@example.com"},
                "reason": "look up the customer",
                "done": False,
            }
        return {
            "tool_name": "crm.get_customer",
            "arguments": {"customer_id": "CUST-1"},
            "reason": "look up the customer",
            "done": True,
        }

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        return "Done."


class _RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True, "tool": name})


class _UnusedOrchestrator:
    """The pump drives through this; it delegates to the real linear one."""

    def __init__(self) -> None:
        from opspilot.adapters.orchestration.linear import LinearOrchestrator

        self._inner = LinearOrchestrator()

    def bind_driver(self, driver: Any) -> None:
        self._inner.bind_driver(driver)

    async def step(self, run: Any) -> StepResult:
        return await self._inner.step(run)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


@pytest.fixture
def tiny_stop_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Set ``REJECTED_PROPOSAL_STOP_LIMIT`` low, and restore the real default.

    Same teardown discipline as ``MAX_STEPS``: undo the environment *before*
    clearing the settings cache, so the next test in the process does not
    inherit this one's threshold.
    """
    monkeypatch.setenv("REJECTED_PROPOSAL_STOP_LIMIT", "2")
    get_settings.cache_clear()
    try:
        yield 2
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _recorder_factory(factory: sessionmaker[Session]) -> Any:
    return lambda run_id: TraceRecorder(run_id=run_id, session_factory=factory)


async def _start_run(factory: sessionmaker[Session]) -> Any:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Duplicate charge",
            body="We were charged twice.",
            customer_email="billing@acme.example",
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


async def _drain(
    factory: sessionmaker[Session],
    provider: ModelProvider,
) -> bool:
    """Drive one poll iteration, exactly as ``poll_forever`` calls it."""
    return await drain_once(
        worker_id="w1",
        run_store=SqlRunStore(factory),
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        provider=provider,
        gateway=_RecordingGateway(),
        orchestrator=_UnusedOrchestrator(),
        recorder_factory=_recorder_factory(factory),
        retrieval=None,
    )


def _audit_payloads(
    factory: sessionmaker[Session], run_id: UUID, event_type: str
) -> list[dict[str, object]]:
    with db.session_scope(factory) as session:
        rows = (
            session.execute(
                select(models.AuditEvent).where(
                    models.AuditEvent.run_id == run_id,
                    models.AuditEvent.event_type == event_type,
                )
            )
            .scalars()
            .all()
        )
    return [dict(row.payload or {}) for row in rows]


async def test_a_run_stopped_for_repeat_invalid_proposals_does_not_kill_the_worker(
    factory: sessionmaker[Session],
) -> None:
    """``drain_once`` returns after the stop fires instead of raising.

    Before the suppression this raised ``RepeatInvalidProposalError`` out of
    ``drain_once`` and out of the handler-less ``poll_forever`` loop.
    """
    provider = _RepeatProvider()
    run = await _start_run(factory)

    processed = await _drain(factory, provider)

    assert processed is True, "drain_once reported it processed nothing"
    with db.session_scope(factory) as session:
        refreshed = await SqlRunStore(session).get(run.id)
        assert refreshed is not None
        assert refreshed.status is RunStatus.FAILED
        assert refreshed.failure_reason == "repeat_invalid_proposals"


async def test_the_stop_writes_the_trace_event_and_the_next_run_is_claimed(
    factory: sessionmaker[Session],
) -> None:
    """The diagnosis reaches the ledger, and the queue keeps moving.

    ``repeat_invalid_stopped`` names the tool, the streak and the limit; a
    second ticket submitted after the stopped one is claimed and completes --
    the property an operator actually feels.
    """
    provider = _RepeatProvider()
    first = await _start_run(factory)
    assert await _drain(factory, provider) is True

    payloads = _audit_payloads(factory, first.id, "repeat_invalid_stopped")
    assert len(payloads) == 1
    assert payloads[0]["tool_name"] == "crm.get_customer"
    assert payloads[0]["streak"] == 3
    assert payloads[0]["limit"] == 3
    assert payloads[0]["rejection_gate"] == "gate_1_schema_validation"

    # The next run: a fresh ticket, and the provider's second script (a valid
    # call, then done) -- proving the worker survived the stopped run above.
    provider.run_index = 1
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Second ticket",
            body="Please check.",
            customer_email="billing@acme.example",
        )
        second = await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )

    assert await _drain(factory, provider) is True
    with db.session_scope(factory) as session:
        refreshed = await SqlRunStore(session).get(second.id)
        assert refreshed is not None
        assert refreshed.status is RunStatus.COMPLETED


async def test_the_environment_variable_lowers_the_stop(
    factory: sessionmaker[Session],
    tiny_stop_limit: int,
) -> None:
    """``REJECTED_PROPOSAL_STOP_LIMIT=2`` reaches the context the worker builds.

    The wiring is the whole point of the setting: the worker reads it from
    settings when it constructs the ``RunContext``, so an operator's value --
    not a dataclass default -- drives the stop.
    """
    provider = _RepeatProvider()
    run = await _start_run(factory)

    assert await _drain(factory, provider) is True

    with db.session_scope(factory) as session:
        refreshed = await SqlRunStore(session).get(run.id)
        assert refreshed is not None
        assert refreshed.status is RunStatus.FAILED
        assert refreshed.failure_reason == "repeat_invalid_proposals"

    payloads = _audit_payloads(factory, run.id, "repeat_invalid_stopped")
    assert payloads[0]["limit"] == 2
    assert payloads[0]["streak"] == 2


async def test_the_runtime_exception_is_the_typed_one(factory: sessionmaker[Session]) -> None:
    """``run_step`` raises ``RepeatInvalidProposalError`` at the threshold.

    The runtime-level contract, separate from the worker: the exception the
    suppress group names is the one the stop actually raises.
    """
    from opspilot.adapters.orchestration.linear import LinearOrchestrator

    run = await _start_run(factory)
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge",
        ticket_body="We were charged twice.",
        customer_email="billing@acme.example",
        max_steps=50,
        rejected_proposal_stop_limit=3,
    )
    provider = _RepeatProvider()

    with pytest.raises(RepeatInvalidProposalError) as excinfo:
        await run_loop(
            ctx,
            provider=provider,
            gateway=_RecordingGateway(),
            orchestrator=LinearOrchestrator(),
            run_store=SqlRunStore(factory),
            tool_call_store=SqlToolCallStore(factory),
            approval_store=SqlApprovalStore(factory),
            recorder=TraceRecorder(run_id=run.id, session_factory=factory),
        )

    assert excinfo.value.tool_name == "crm.get_customer"
    assert excinfo.value.limit == 3
