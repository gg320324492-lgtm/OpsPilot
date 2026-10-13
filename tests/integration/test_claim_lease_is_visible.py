"""The claim lease has to reach the operator, or ``received`` says nothing.

``SqlRunStore.claim_next`` takes a row and writes ``claimed_until``/``claimed_by``
**in the same transaction, without changing the run's status**
(``adapters/persistence/repositories.py``; ``migrations/versions/
0002_claim_lease.py``). That is what makes two workers safe, and it is why a
claimed run and a run nothing claimed are the *same object* above the repository:
the only column that told them apart was dropped by ``_to_agent_run``, so
``GET /api/runs/{id}`` served two identical payloads for the two states and the
dashboard had nothing to render.

The consequence was measured rather than argued (``docs/limitations.md`` §2): on
a live stack with a healthy worker, a ``received`` run carrying an unexpired
lease went unclaimed for the full 300 s of ``WORKER_CLAIM_LEASE_SECONDS``, and
the page said "the worker has not claimed it yet" for five minutes. The lease
cannot be shortened -- it has to exceed a run's p95 drive duration (276.7 s) or a
long run loses it mid-drive -- so the only place left to bound is *detecting* the
state.

So this file asserts the projection, against ``SqlRunStore`` rather than a fake:
``_to_agent_run`` dropping the columns is exactly the defect, and a test that ran
against ``FakeRunStore`` would pass with them dropped in production. Every lease
here is written by ``claim_next`` itself -- no test writes the columns directly,
because a row no code path can produce is not a state an operator can reach.

``docs/api-contract.md`` §3 names the field, and the last test pins that: a field
in a response the contract does not describe is the same defect class this
repository keeps finding (``ticket_exists``; §8 of ``docs/limitations.md``).
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, repositories
from opspilot.adapters.persistence.models import Base
from opspilot.domain.runs import ClaimLeaseState, RunStatus
from opspilot.settings import Settings

_SUBJECT = "Charged twice for invoice INV-2026-384"
_BODY = "We were charged twice."
_EMAIL = "billing@acme.example"

# Comfortably longer than any test can take to read a page, and the same value
# `docker-compose.yml` ships as the default. The exact number is not what is
# under test -- only that the deadline is in the future.
_LEASE_SECONDS = 300.0


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


async def _new_run(factory: sessionmaker[Session]) -> UUID:
    """A ticket and its ``RECEIVED`` run, written through the real stores."""
    with db.session_scope(factory) as session:
        ticket_id = await repositories.SqlTicketStore(session).create(
            subject=_SUBJECT, body=_BODY, customer_email=_EMAIL
        )
        run = await repositories.SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
    return run.id


# ---------------------------------------------------------------------------
# The three states an operator has to be able to tell apart
# ---------------------------------------------------------------------------


async def test_an_unclaimed_run_does_not_claim_it_is_claimed(
    factory: sessionmaker[Session],
) -> None:
    """A run nothing has taken reports ``unclaimed`` and names no holder.

    The negative case, and the load-bearing half. Before the lease was projected
    there was nothing here to get wrong, so it needs no guard; now that the field
    exists, the defect this file exists to prevent can return in the *other*
    direction -- a run that was never claimed reporting a lease it does not have,
    which would put an operator in a debugger chasing a worker that never existed.
    """
    from opspilot.api.routers.runs import get_run

    run_id = await _new_run(factory)
    detail = await get_run(run_id, repositories.SqlRunStore(factory))

    assert detail.status is RunStatus.RECEIVED
    assert detail.claim_lease.state is ClaimLeaseState.UNCLAIMED
    assert detail.claim_lease.claimed_by is None
    assert detail.claim_lease.claimed_until is None


async def test_a_claimed_run_names_the_holder_and_the_deadline(
    factory: sessionmaker[Session],
) -> None:
    """The claimed case: ``held``, with the ``WORKER_ID`` and the deadline.

    Both facts, not one. ``state`` alone says somebody has this run, which is the
    sentence; ``claimed_by`` says who to go and look at, and ``claimed_until``
    turns an open-ended silence into a bounded one, which is the whole difference
    between "this is stuck" and "this is late, and here is how late it can get".
    """
    from opspilot.api.routers.runs import get_run

    run_id = await _new_run(factory)
    runs = repositories.SqlRunStore(factory)
    claimed = await runs.claim_next(worker_id="worker-compose", lease_seconds=_LEASE_SECONDS)
    assert claimed is not None
    assert claimed.id == run_id

    detail = await get_run(run_id, repositories.SqlRunStore(factory))

    # The claim does not move the run. That is the premise of the whole feature
    # (``EXECUTING`` must stay claimable), and it is why the lease -- not the
    # status -- is what the page has to read.
    assert detail.status is RunStatus.RECEIVED
    assert detail.claim_lease.state is ClaimLeaseState.HELD
    assert detail.claim_lease.claimed_by == "worker-compose"
    assert detail.claim_lease.claimed_until is not None


async def test_a_lapsed_lease_is_reported_expired_not_held(
    factory: sessionmaker[Session],
) -> None:
    """A run whose holder died is ``expired`` -- neither held nor unclaimed.

    ``lease_seconds: 0`` is ``claim_next``'s own arithmetic rather than a state
    the API offers: ``deadline = now + timedelta(seconds=0)``, which is in the past
    by the time anything reads it. That is the row a worker that died mid-claim
    leaves behind, produced by the same method, and it is the third case an
    operator needs -- the run *was* claimed (so "not claimed" is false) and
    nobody holds it now (so "a worker is working" is false too).
    """
    from opspilot.api.routers.runs import get_run

    run_id = await _new_run(factory)
    runs = repositories.SqlRunStore(factory)
    assert await runs.claim_next(worker_id="worker-compose", lease_seconds=0.0) is not None

    detail = await get_run(run_id, repositories.SqlRunStore(factory))

    assert detail.claim_lease.state is ClaimLeaseState.EXPIRED
    # The holder is still named: knowing *which* worker went away is what makes
    # "nobody is holding it" a diagnosis rather than an observation.
    assert detail.claim_lease.claimed_by == "worker-compose"


async def test_a_terminal_run_reports_no_lease(
    factory: sessionmaker[Session],
) -> None:
    """Reaching a terminal state clears the lease, so a failed run is not claimed.

    ``set_status`` clears ``claimed_until``/``claimed_by`` on ``FAILED`` because
    the row is no longer being driven by anyone. Without that the dashboard would
    render a failed run as "held by <worker> until <future>", pointing an operator
    at a worker that has already let the run go.
    """
    from opspilot.api.routers.runs import get_run

    run_id = await _new_run(factory)
    runs = repositories.SqlRunStore(factory)
    claimed = await runs.claim_next(worker_id="worker-compose", lease_seconds=_LEASE_SECONDS)
    assert claimed is not None
    await runs.set_status(run_id, RunStatus.FAILED, failure_reason="max_steps_exceeded")

    detail = await get_run(run_id, repositories.SqlRunStore(factory))

    assert detail.status is RunStatus.FAILED
    assert detail.failure_reason == "max_steps_exceeded"
    assert detail.claim_lease.state is ClaimLeaseState.UNCLAIMED
    assert detail.claim_lease.claimed_by is None
    assert detail.claim_lease.claimed_until is None


# ---------------------------------------------------------------------------
# The decision, on the domain object
# ---------------------------------------------------------------------------


async def test_the_state_is_decided_against_a_clock_the_caller_supplies(
    factory: sessionmaker[Session],
) -> None:
    """``claim_state`` is a pure function of ``(claimed_until, now)``.

    Asserted on both sides of the deadline with the clock placed by hand, because
    the alternative -- sleeping across a boundary -- is either slow or flaky, and
    a flaky test of a time comparison is a test that will eventually pass for the
    wrong reason. It also pins the SQLite normalisation: the deadline read back
    from the tests-only path is naive, and comparing that against an aware clock
    without normalising raises ``TypeError`` rather than answering.
    """
    run_id = await _new_run(factory)
    runs = repositories.SqlRunStore(factory)
    claimed = await runs.claim_next(worker_id="worker-compose", lease_seconds=_LEASE_SECONDS)
    assert claimed is not None

    stored = await runs.get(run_id)
    assert stored is not None
    assert stored.claim_state() is ClaimLeaseState.HELD

    # One moment inside the deadline: still held. One moment past it: expired.
    assert stored.claim_state(now=datetime.now(UTC)) is ClaimLeaseState.HELD
    assert (
        stored.claim_state(now=datetime.now(UTC) + timedelta(seconds=_LEASE_SECONDS + 1))
        is ClaimLeaseState.EXPIRED
    )

    # A run that was never claimed is `unclaimed` at every clock, including one
    # far in the future: there is no deadline to compare against.
    never = await runs.get(run_id)
    assert never is not None
    never.claimed_until = None
    never.claimed_by = None
    assert never.claim_state(now=datetime.now(UTC) + timedelta(days=3650)) is (
        ClaimLeaseState.UNCLAIMED
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_the_api_contract_documents_the_lease(repo_root: pathlib.Path) -> None:
    """``docs/api-contract.md`` §3 names ``claim_lease``.

    Pinned because it is the defect class this repository ships repeatedly
    (§8 of ``docs/limitations.md``): a field in a response the contract does not
    describe is a capability no document covers, which is what
    ``ticket_exists`` was. The check is deliberately narrow -- the field is named
    in the document that specifies the payload -- because a broader
    prose-correspondence test would be asserting this file's writing style.
    """
    contract = (repo_root / "docs" / "api-contract.md").read_text(encoding="utf-8")
    assert "claim_lease" in contract, (
        "GET /api/runs/{id} serves a `claim_lease` field that docs/api-contract.md "
        "does not describe. A field the contract does not mention is a capability "
        "no document covers."
    )
