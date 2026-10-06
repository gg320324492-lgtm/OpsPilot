"""Worker claim semantics against a real SQLite schema.

These replace the M0 placeholder. The properties the worker's claim predicate
rests on (``docs/agent-state-machine.md`` §5):

- a ``RECEIVED`` run is claimed and driven; a ``WAITING_APPROVAL`` run is **not**
  -- the parked run is the one state the worker must not hold;
- ``drain_once`` returns ``False`` on an empty queue (and touches nothing);
- on boot, a mid-flight run is marked ``FAILED('interrupted')`` and a
  ``WAITING_APPROVAL`` run is left alone.

The worker is driven through ``drain_once`` -- no sleeping -- with the scripted
provider and a recording gateway, so nothing does I/O outside the run.
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
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder
from opspilot.worker.loop import drain_once, mark_interrupted_on_boot
from tests.agent.test_runtime_fake import ScriptedProvider, UnusedOrchestrator


class RecordingGateway:
    """A gateway that records calls and returns a canned success."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True})


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _make_run(
    factory: sessionmaker[Session], *, status: RunStatus = RunStatus.RECEIVED
) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        run = await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
        if status is not RunStatus.RECEIVED:
            await SqlRunStore(session).set_status(run.id, status)
            run.status = status
        return run


async def _drain(
    factory: sessionmaker[Session],
    *,
    provider: ModelProvider | None = None,
) -> bool:
    provider = provider or ScriptedProvider()
    return await drain_once(
        worker_id="w1",
        run_store=SqlRunStore(factory),
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        provider=provider,
        gateway=RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        recorder_factory=lambda run_id: TraceRecorder(run_id=run_id, session_factory=factory),
        retrieval=None,
    )


# ---------------------------------------------------------------------------
# Claim predicate
# ---------------------------------------------------------------------------


async def test_a_received_run_is_claimed_and_driven(factory: sessionmaker[Session]) -> None:
    run = await _make_run(factory)
    processed = await _drain(factory)
    assert processed is True
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.COMPLETED


async def test_a_waiting_approval_run_is_not_claimed(factory: sessionmaker[Session]) -> None:
    run = await _make_run(factory, status=RunStatus.WAITING_APPROVAL)
    processed = await _drain(factory)
    assert processed is False
    reloaded = await SqlRunStore(factory).get(run.id)
    assert reloaded is not None
    assert reloaded.status is RunStatus.WAITING_APPROVAL


async def test_drain_once_returns_false_on_an_empty_queue(
    factory: sessionmaker[Session],
) -> None:
    assert await _drain(factory) is False


async def test_drain_once_does_nothing_when_only_a_parked_run_exists(
    factory: sessionmaker[Session],
) -> None:
    await _make_run(factory, status=RunStatus.WAITING_APPROVAL)
    assert await _drain(factory) is False


# ---------------------------------------------------------------------------
# Boot behaviour
# ---------------------------------------------------------------------------


async def test_boot_marks_interrupted_runs_and_spares_waiting_approval(
    factory: sessionmaker[Session],
) -> None:
    """Boot fails runs mid-step and preserves every human decision.

    Three classes, and the third is the one that used to be wrong:

    * ``RETRIEVING`` (like ``CLASSIFYING``/``PLANNING``/``RESPONDING``) -- no
      approval behind it, nothing but the pump claims it, so it was mid-step when
      the process died and is marked ``FAILED('interrupted')``.
    * ``WAITING_APPROVAL`` -- nobody has decided yet. Failing it would erase a
      pending decision.
    * ``EXECUTING`` -- somebody *may* have decided. A single status column cannot
      distinguish "the pump is driving this right now" from "a human approved and
      the worker died before finishing", and failing the second kind discards a
      decision someone made: the approved refund is never issued and nothing
      records that anyone said yes. Preserved, per ``docs/architecture.md`` §5.
    """
    mid_step = await _make_run(factory, status=RunStatus.RETRIEVING)
    waiting = await _make_run(factory, status=RunStatus.WAITING_APPROVAL)
    executing = await _make_run(factory, status=RunStatus.EXECUTING)

    marked = mark_interrupted_on_boot(factory)
    assert marked == 1, "only the genuinely mid-step run should be marked"

    reloaded_mid_step = await SqlRunStore(factory).get(mid_step.id)
    assert reloaded_mid_step is not None
    assert reloaded_mid_step.status is RunStatus.FAILED
    assert reloaded_mid_step.failure_reason == "interrupted"

    reloaded_waiting = await SqlRunStore(factory).get(waiting.id)
    assert reloaded_waiting is not None
    assert reloaded_waiting.status is RunStatus.WAITING_APPROVAL
    assert reloaded_waiting.failure_reason is None

    reloaded_executing = await SqlRunStore(factory).get(executing.id)
    assert reloaded_executing is not None
    assert reloaded_executing.status is RunStatus.EXECUTING, (
        "boot failed a run whose refund a human had already approved; the "
        "decision must survive the restart (docs/milestones.md §M6)"
    )
    assert reloaded_executing.failure_reason is None

    # A preserved EXECUTING run is claimable, so a restarted worker picks it up
    # rather than stranding it. This is what makes preservation useful rather
    # than merely safe.
    claimed = await SqlRunStore(factory).claim_next(worker_id="w-after-boot")
    assert claimed is not None
    assert claimed.id == executing.id


async def test_claim_next_is_the_queue_primitive(factory: sessionmaker[Session]) -> None:
    """``claim_next`` returns the run; ``drain_once`` builds on it."""
    from opspilot.worker.loop import claim_next

    run = await _make_run(factory)
    claimed = await claim_next(worker_id="w1", run_store=SqlRunStore(factory))
    assert claimed is not None
    assert claimed.id == run.id
    with db.session_scope(factory) as session:
        assert session.execute(select(models.AgentRun)).scalars().first() is not None
