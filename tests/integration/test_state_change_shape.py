"""A ``state_change`` step has one shape, whoever wrote it.

## Why this file exists

``docs/api-contract.md`` §3 says a ``state_change`` step "carries the transition
it accompanies: ``{from_status, to_status, at}``", and adds three words that
turn the sentence into a promise about every writer rather than about the
common one: **"fixed by the writers."**

There were two writers.

- ``SqlRunStore.set_status`` -- the path every live transition goes through --
  writes all three keys, with ``at`` an ISO-8601 instant in UTC.
- ``TraceRecorder.record_state_change`` wrote **two**: ``from_status`` and
  ``to_status``, and no ``at``.

There are **three** now: the worker's boot sweep fails a run left mid-flight
and records the transition it made, so it writes a ``state_change`` step too --
and a guard that compares two writers would not have noticed if the third
wrote a different shape.

So a step written through the recorder had a different shape from a step
written through the store, for the same ``step_type``. A reader told "the shape
is fixed by the writers" would be told a false thing by one of them, and a
reader who wrote ``payload["at"]`` would raise ``KeyError`` on a step that was
perfectly legal to write.

The recorder now writes the same three keys. This file is what stops the two
writers drifting apart again -- and it does so by **comparing them to each
other** rather than by pinning each one to a literal, because the thing that
went wrong was a divergence, and a divergence is only visible when the two
ends are held to the same standard.

## What is asserted

1. Every writer produces a payload with **exactly** the keys
   ``{from_status, to_status, at}`` -- no fewer (the original defect) and no
   more (a fourth key would be a shape change again).
2. ``at`` parses as an ISO-8601 instant and is timezone-aware, in each.
3. The payloads have the **same key set**, asserted directly against each
   other, so that a future change to one writer's shape fails here even if
   the contract is updated to match it.

Point 3 is the one that earns its keep: points 1 and 2 pin the shape to the
contract, and point 3 pins it to the contract *and* to the other writer, which
is what "fixed by the writers" actually means.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from opspilot.adapters.persistence.db import mark_interrupted_runs
from opspilot.domain.runs import RunStatus
from opspilot.tracing.recorder import TraceRecorder

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    # The concrete type the recorder's ``session_factory`` port takes.
    Factory = sessionmaker[Session]

#: The shape ``docs/api-contract.md`` §3 documents for a ``state_change`` step.
_CONTRACT_KEYS = frozenset({"from_status", "to_status", "at"})


@pytest.fixture
def factory() -> Factory:
    """An in-memory database both writers write to.

    The recorder must be handed this same factory (rather than building its own
    over the same URL) or its rows would land in a *different* empty database.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from opspilot.adapters.persistence import models

    engine = create_engine("sqlite+pysqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


async def _state_change_payloads(factory: Factory) -> list[tuple[str, dict[str, Any]]]:
    """Write one state change through each writer, and return the payloads.

    There are **three** writers now, and each is labelled with its name so a
    failure says which one drifted:

    - the worker's boot sweep, which fails a run left mid-flight and records
      the transition it made;
    - ``SqlRunStore.set_status``, the path every live transition goes through;
    - ``TraceRecorder.record_state_change``, for a caller that moved the status
      some other way.

    The sweep runs **first**, and the runs the other two writers use are
    created after it. The sweep fails every run left in a mid-flight state, so
    running it last would have it reach the other two runs as well, and the
    payload count would not be one per writer. The run it is meant to touch
    has its status set directly rather than through ``set_status``, so the only
    step for that run is the sweep's: otherwise the store's writer would
    contribute a step to it too, and the guard would be comparing a writer
    with itself.
    """
    from sqlalchemy import select

    from opspilot.adapters.persistence import db, models
    from opspilot.adapters.persistence.repositories import SqlRunStore, SqlTicketStore

    ticket_store = SqlTicketStore(factory)
    run_store = SqlRunStore(factory)

    ticket_id = await ticket_store.create(
        subject="duplicate charge",
        body="charged twice",
        customer_email="billing@acme.example",
    )

    # Writer 1: the boot sweep, on a run left mid-flight.
    swept = await run_store.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    with db.session_scope(factory) as session:
        row = session.get(models.AgentRun, swept.id)
        assert row is not None
        row.status = RunStatus.PLANNING.value
    assert mark_interrupted_runs(factory) == 1, "the sweep did not fail the mid-flight run"

    # The runs the other two writers use, created after the sweep.
    run = await run_store.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")

    # Writer 2: the store, which every live transition goes through.
    await run_store.set_status(run.id, RunStatus.CLASSIFYING)

    # Writer 3: the recorder, for a caller that moved the status some other way.
    recorder = TraceRecorder(run_id=run.id, session_factory=factory)
    await recorder.record_state_change(
        from_status=RunStatus.CLASSIFYING.value, to_status=RunStatus.RETRIEVING.value
    )

    with db.session_scope(factory) as session:
        rows = session.execute(
            select(models.AgentStep)
            .where(models.AgentStep.step_type == "state_change")
            .order_by(models.AgentStep.run_id, models.AgentStep.sequence)
        ).scalars()
        payloads = [dict(row.output or {}) for row in rows]

    # The three payloads are told apart by what they say, not by the order the
    # rows came back in -- the runs' ids are random, so the order is not
    # stable. The order is not what is under test (the shapes are), but a
    # stable labelling makes a failure message readable.
    names = ["the sweep", "the store", "the recorder"]
    expected = {
        "the sweep": RunStatus.FAILED.value,
        "the store": RunStatus.CLASSIFYING.value,
        "the recorder": RunStatus.RETRIEVING.value,
    }
    assert len(payloads) == len(names), (
        f"expected one state_change step per writer, got {len(payloads)}: {payloads}"
    )
    labelled: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        matching = [payload for payload in payloads if payload.get("to_status") == expected[name]]
        assert len(matching) == 1, (
            f"expected exactly one step from {name} (to_status={expected[name]!r}), "
            f"got {len(matching)} of {payloads}"
        )
        labelled.append((name, matching[0]))
    return labelled


async def test_both_writers_produce_the_documented_shape(factory: Factory) -> None:
    """Each writer's payload is exactly ``{from_status, to_status, at}``."""
    for writer, payload in await _state_change_payloads(factory):
        assert set(payload) == _CONTRACT_KEYS, (
            f"{writer} wrote a state_change payload with the wrong keys. "
            f"docs/api-contract.md §3 documents exactly {sorted(_CONTRACT_KEYS)}. "
            f"Got: {sorted(payload)}"
        )


async def test_both_writers_agree(factory: Factory) -> None:
    """Every writer produces the same shape, not merely a valid one.

    This is the assertion the contract's phrase "fixed by the writers" asks
    for, and the one that would have caught the original defect: the writers'
    key sets are compared to **each other**, so a change to one is caught even
    if the contract is later updated to match it.
    """
    payloads = await _state_change_payloads(factory)
    first_name, first = payloads[0]
    for other_name, other in payloads[1:]:
        assert set(first) == set(other), (
            f"the writers of a state_change step disagree about its shape: "
            f"{first_name} wrote {sorted(first)}, {other_name} wrote "
            f"{sorted(other)}"
        )


async def test_both_writers_stamp_the_instant_the_same_way(factory: Factory) -> None:
    """``at`` is an ISO-8601 instant with a UTC offset, in both payloads.

    The contract's example writes ``2026-10-05T10:42:03.481902+00:00`` -- an
    aware instant in UTC. A naive datetime, or a string without an offset,
    would satisfy "has an ``at`` key" and break every reader that compares two
    transitions.
    """
    for writer, payload in await _state_change_payloads(factory):
        raw = payload.get("at")
        assert isinstance(raw, str), f"{writer} wrote a non-string 'at': {raw!r}"
        parsed = datetime.fromisoformat(raw)
        assert parsed.tzinfo is not None, (
            f"{writer} wrote a naive 'at' ({raw!r}); a naive instant cannot be "
            "compared with another transition's"
        )
        assert parsed.utcoffset() == UTC.utcoffset(None), (
            f"{writer} wrote 'at' in a non-UTC offset ({raw!r})"
        )
