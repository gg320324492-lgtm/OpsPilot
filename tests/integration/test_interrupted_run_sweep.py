"""The boot sweep must do what every other path into ``FAILED`` does.

## Why this file exists

``docs/api-contract.md`` §3 states an invariant: *"`claimed_by` and
`claimed_until` are `null` **together** … A run that reaches a terminal state
therefore never reports a holder, even if a worker held it a moment
earlier."* And ``docs/agent-state-machine.md`` §4 states two more: *"Every
transition writes an `AgentStep(step_type='state_change')` row"* and
*"`completed_at` is set on entry to `COMPLETED` or `FAILED`"*.

All three held for every way a run reaches ``FAILED`` **except one**: the
worker's boot sweep. ``db.mark_interrupted_runs`` used to issue a single bulk
``UPDATE`` that set ``status`` and ``failure_reason`` and nothing else. It did
not clear the lease, it did not set ``completed_at``, and it wrote no
``state_change`` step.

The consequence was not cosmetic. The sweep is the **ordinary worker-crash
path**: worker A claims a run and dies mid-drive; worker B boots and sweeps
it. The run became `failed` while still carrying A's lease, and the API
projects the lease with no status check — so the run-detail page showed
`status=failed`, `claim_lease.state=held`, and a `claimed_by` naming a worker
that no longer existed. The contract said a terminal run never reports a
holder, and the dashboard renders that field precisely so an operator can tell
a held run from a run nothing claimed.

The fix is that the sweep now does what ``SqlRunStore.set_status`` does. It
was a shortcut that duplicated a statement's *effect* and duplicated only part
of it.

## What this file asserts

1. A swept run has **no lease**: `claimed_by` and `claimed_until` are both
   null, and the API's projection says `unclaimed`.
2. A swept run has a **`completed_at`**.
3. A swept run has a **`state_change` step** carrying
   `(from_status, to_status, at)`.
4. The states the sweep **spares** are still spared, with their leases intact
   — `EXECUTING` above all, because it may hold an approval a human granted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import SqlRunStore, SqlTicketStore
from opspilot.api.routers import runs as runs_router
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.settings import Settings


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """An in-memory SQLite factory with the schema created."""
    session_factory = db.session_factory(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _make_run(factory: sessionmaker[Session], *, status: RunStatus) -> AgentRun:
    """Create one ticket and its run, committed, in ``status``."""
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        store = SqlRunStore(session)
        run = await store.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
        await store.set_status(run.id, status)
        run.status = status
        return run


def _hold_a_lease(
    factory: sessionmaker[Session], run_id: UUID, *, worker: str = "worker-A"
) -> None:
    """Give the run a live lease, as a worker that then died would have left."""
    with db.session_scope(factory) as session:
        row = session.get(models.AgentRun, run_id)
        assert row is not None
        row.claimed_by = worker
        row.claimed_until = datetime.now(UTC) + timedelta(seconds=300)


def _read(factory: sessionmaker[Session], run_id: UUID) -> models.AgentRun:
    with db.session_scope(factory) as session:
        row = session.get(models.AgentRun, run_id)
        assert row is not None
        return row


def _state_changes(factory: sessionmaker[Session], run_id: UUID) -> list[dict[str, object]]:
    with db.session_scope(factory) as session:
        rows = session.execute(
            select(models.AgentStep).where(
                models.AgentStep.run_id == run_id,
                models.AgentStep.step_type == "state_change",
            )
        ).scalars()
        return [dict(row.output or {}) for row in rows]


async def test_a_swept_run_reports_no_holder(
    factory: sessionmaker[Session],
) -> None:
    """The run the sweep fails has no lease left on it.

    The contract sentence is about the *API's* answer, so the assertion is made
    through the run-detail projection rather than through the row: the field
    the dashboard renders is the field that has to be right.
    """
    run = await _make_run(factory, status=RunStatus.PLANNING)
    _hold_a_lease(factory, run.id)

    marked = db.mark_interrupted_runs(factory)
    assert marked == 1

    row = _read(factory, run.id)
    assert row.status == RunStatus.FAILED.value
    assert row.failure_reason == "interrupted"
    assert row.claimed_by is None
    assert row.claimed_until is None

    # ...and the API agrees. The projection takes the *domain* run, which is
    # what the router passes it -- `SqlRunStore.get` is what builds one.
    stored = await SqlRunStore(factory).get(run.id)
    assert stored is not None
    lease = runs_router._claim_lease(stored)
    assert lease.state.value == "unclaimed"
    assert lease.claimed_by is None
    assert lease.claimed_until is None


async def test_a_swept_run_has_a_completed_at(factory: sessionmaker[Session]) -> None:
    """``completed_at`` is set on entry to ``FAILED`` — including by the sweep.

    A terminal run with no completion time is a run whose end the API cannot
    report, and ``docs/data-model.md`` §2 documents the column as "Set on
    entry to `COMPLETED`/`FAILED`".
    """
    run = await _make_run(factory, status=RunStatus.RETRIEVING)
    _hold_a_lease(factory, run.id)

    assert db.mark_interrupted_runs(factory) == 1

    row = _read(factory, run.id)
    assert row.completed_at is not None


async def test_a_swept_run_records_the_transition_it_made(
    factory: sessionmaker[Session],
) -> None:
    """The sweep writes a ``state_change`` step, like every other transition.

    Without it the trace shows a run that simply stopped: no row says how it
    got from ``planning`` to ``failed``, and the timeline has a hole where the
    worker's death was.
    """
    run = await _make_run(factory, status=RunStatus.PLANNING)

    assert db.mark_interrupted_runs(factory) == 1

    changes = _state_changes(factory, run.id)
    finals = [change for change in changes if change.get("to_status") == "failed"]
    assert len(finals) == 1, f"expected exactly one transition into failed, got {changes}"
    assert finals[0]["from_status"] == "planning"
    assert finals[0]["at"], "the transition carries no timestamp"


async def test_the_states_the_sweep_spares_are_still_spared(
    factory: sessionmaker[Session],
) -> None:
    """``EXECUTING`` and ``WAITING_APPROVAL`` keep their leases and their status.

    ``EXECUTING`` is spared because a single-column status cannot tell "the
    pump is driving it" from "a human approved and the worker died", and
    failing the second one discards a person's decision
    (``docs/agent-state-machine.md`` §3). This test says so, so that a change
    to the sweep's status set is a deliberate one.
    """
    for status in (RunStatus.EXECUTING, RunStatus.WAITING_APPROVAL, RunStatus.RECEIVED):
        run = await _make_run(factory, status=status)
        _hold_a_lease(factory, run.id, worker=f"worker-{status.value}")

        assert db.mark_interrupted_runs(factory) == 0, f"{status.value} should not be swept"

        row = _read(factory, run.id)
        assert row.status == status.value
        assert row.claimed_by == f"worker-{status.value}"
        assert row.claimed_until is not None
