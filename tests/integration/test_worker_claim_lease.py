"""The claim lease: two workers, one run, and what an expired lease means.

## Why this file exists

``claim_next`` claimed with ``FOR UPDATE SKIP LOCKED``, but that lock closed with
its own ``SELECT`` and the claim never changed the run, so two workers whose
polls did **not** overlap both received the same still-``RECEIVED`` run. Measured:
**thirty concurrent claim pairs on one row returned it to both, thirty times out
of thirty** (``docs/limitations.md`` §2 and §8; commit 23d3ffe). The test that
recorded that boundary was deleted when the lease fixed it -- its own docstring
named the fix as the prompt to delete it
(``test_worker_claim_postgres.py::test_a_leased_claim_is_withheld_from_a_second_worker``
is its replacement). This file is the regression: it reproduces the **30/30
shape** and asserts it is now zero.

## Why SQLite, when the defect was measured on Postgres

The defect is not in ``SKIP LOCKED`` -- that clause is real and tested against a
real PostgreSQL (``test_worker_claim_postgres.py``). The defect is that the claim
changed nothing for a second worker to see. The lease does the work in the
*database row*, not in a row lock, so the property is dialect-independent: a
second ``claim_next`` bound to its own session sees ``claimed_until`` in the
future and is withheld, on SQLite as on Postgres. That is what makes this the
regression test every machine runs (ADR-0004: a missing PostgreSQL is a clean
skip, but the *defect's* regression must not be one). The Postgres file asserts
the same property where the lease meets the locking clause.

## What "concurrent" means here, and why not two threads

The measured shape was **pairs of ``claim_next`` calls whose polls do not
overlap** -- the widest window for the duplicate, and the one a bare thread race
cannot guarantee to hit: whether two threads overlap is scheduling luck, and a
race that cannot fail proves nothing. So each pair is sequential and named:
worker A claims and commits (its transaction is over, its lock released), then
worker B claims. Under the old code B received the same run every time; under the
lease B receives ``None``. Thirty pairs is the number the measurement used.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import SqlRunStore, SqlTicketStore
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.settings import Settings

#: The number of pairs the original measurement used (30/30). Kept as the
#: measurement's own number so a reader can compare like with like.
_PAIRS = 30

#: A lease long enough that it cannot expire during a test yet is not the
#: production default, so a test asserting "held" is asserting the predicate and
#: not a coincidence of timing.
_LONG_LEASE = 300.0

#: A lease short enough to expire inside a test after a real ``sleep``. One
#: hundredth of a second: two orders of magnitude below any assertion's own
#: runtime, so an expiry check is decisive rather than racy.
_TINY_LEASE = 0.01

#: The lease used by the drain_once renewal test. Its renewal interval is
#: ``_DRIVE_LEASE / 3`` = 0.01s, which is well inside the ~0.1s a slowed drive
#: takes, so the renewer fires during the drive rather than after it.
_DRIVE_LEASE = 0.03


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """An in-memory SQLite factory with the schema created."""
    session_factory = db.session_factory(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _make_run(
    factory: sessionmaker[Session], *, status: RunStatus = RunStatus.RECEIVED
) -> AgentRun:
    """Create one ticket and its run, committed, in ``status``."""
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        store = SqlRunStore(session)
        run = await store.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
        if status is not RunStatus.RECEIVED:
            await store.set_status(run.id, status)
            run.status = status
        return run


async def _expire_lease(factory: sessionmaker[Session], run_id: UUID) -> None:
    """Move a run's lease into the past, as a dead worker's lease would be.

    Writes the column directly rather than sleeping: a test that slept for a real
    lease would be slow and, worse, would pass for the wrong reason on a loaded
    machine. The claim only reads the timestamp, so backdating it is exactly the
    state a lapsed lease is in.
    """
    with db.session_scope(factory) as session:
        row = session.get(models.AgentRun, run_id)
        assert row is not None
        row.claimed_until = datetime.now(UTC) - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# The regression: the 30/30 shape
# ---------------------------------------------------------------------------


async def test_thirty_non_overlapping_claim_pairs_never_share_the_run(
    factory: sessionmaker[Session],
) -> None:
    """The measured defect, reproduced and fixed: no pair both receives the run.

    The regression for the 30/30 measurement. Thirty *independent* pairs -- one
    fresh ``RECEIVED`` run per pair, so each pair is the measurement's own unit
    (one run, two claims that do not overlap): A claims and commits, then B
    claims. Under the old claim both received the run every time. Under the lease,
    A holds a lease A set in its own transaction, so B's predicate excludes the
    row and B gets ``None``. The assertion is the delivered set: zero run ids
    handed to a pair's second claimant.

    This test is what "a concurrency test that cannot fail" is measured against:
    break the lease predicate (drop the ``claimed_until`` clause from the claim)
    and every pair returns the run to both, so this goes red at 30/30 rather than
    staying green.
    """
    duplicates: list[UUID] = []

    for _ in range(_PAIRS):
        run = await _make_run(factory)
        first = await SqlRunStore(factory).claim_next(
            worker_id="worker-a", lease_seconds=_LONG_LEASE
        )
        # A's transaction has committed here: its row lock is gone, and the only
        # thing that can withhold the run from B is the lease it wrote.
        second = await SqlRunStore(factory).claim_next(
            worker_id="worker-b", lease_seconds=_LONG_LEASE
        )
        # The first pairwise claim should always be the run; re-claiming it
        # requires the lease to have lapsed, which it cannot have.
        assert first is not None and first.id == run.id
        if second is not None:
            duplicates.append(second.id)

    assert duplicates == [], (
        f"{len(duplicates)} of {_PAIRS} non-overlapping claim pairs both received "
        "the same run. This is the 30/30 defect (docs/limitations.md §2): a second "
        "claim a moment after the first must be withheld by the lease the first "
        "wrote, not receive the same run."
    )


async def test_the_second_claim_of_a_pair_gets_none_not_the_same_run(
    factory: sessionmaker[Session],
) -> None:
    """The smallest form of the regression: one pair, second claim is ``None``.

    Named separately from the thirty because the failure it reports is the one a
    reader looks for first: with a single run and a single pair, the old claim
    returned the run to B and the fixed claim returns ``None``. A green run here
    is the defect not reproducing in its most legible shape.
    """
    run = await _make_run(factory)
    first = await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_LONG_LEASE)
    assert first is not None and first.id == run.id

    second = await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
    assert second is None, (
        "a second, non-overlapping claim received the run the first claim already "
        "returned. The lease the first claim wrote (claimed_until in the future) "
        "must exclude the row; a run here means the lease predicate is missing or "
        "the lease was not written in the claim's transaction."
    )


# ---------------------------------------------------------------------------
# A lapsed lease is reclaimable -- a dead worker must not strand its run
# ---------------------------------------------------------------------------


async def test_an_expired_lease_is_reclaimable(factory: sessionmaker[Session]) -> None:
    """A run whose lease has expired is claimed again -- recovery, not a lock-out.

    The whole point of a *lease* rather than a permanent claim: a worker that dies
    leaves a lease that expires, and the run returns to the queue. Without this a
    dead worker strands its run forever, which is worse than the duplicate the
    lease prevents.
    """
    run = await _make_run(factory)
    claimed = await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_LONG_LEASE)
    assert claimed is not None and claimed.id == run.id

    # A second claim is withheld while the lease is live ...
    assert (
        await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
        is None
    )

    # ... and taken once it has expired, as a dead holder's would be.
    await _expire_lease(factory, run.id)
    recovered = await SqlRunStore(factory).claim_next(
        worker_id="worker-b", lease_seconds=_LONG_LEASE
    )
    assert recovered is not None and recovered.id == run.id, (
        "the run was not reclaimable after its lease expired. An expired lease "
        "means the holder is gone (or stalled); the run must become claimable "
        "again, or a dead worker strands it forever -- worse than the duplicate "
        "the lease exists to prevent."
    )


async def test_a_lease_that_lapses_mid_drive_is_the_tested_failure_mode(
    factory: sessionmaker[Session],
) -> None:
    """A run that outlives an *unrenewed* lease is claimed by a second worker.

    **The failure mode, made a test rather than an assumption.** If a worker
    stalls past its lease (the renewer not running, the process wedged) the lease
    lapses and another worker claims the same run: two workers driving one run.
    That is the honest cost of a lease, and it is what renewal mitigates and what
    the refund's idempotency key makes safe for the money. This asserts the
    behaviour directly -- a real ``sleep`` past a tiny lease, no renewal -- so a
    future change that *relied* on "the lease cannot lapse mid-drive" fails here
    instead of in production.
    """
    import asyncio

    run = await _make_run(factory)
    claimed = await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_TINY_LEASE)
    assert claimed is not None and claimed.id == run.id

    # Drive takes longer than the lease, and nobody renewed it.
    await asyncio.sleep(_TINY_LEASE * 5)

    stolen = await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
    assert stolen is not None and stolen.id == run.id, (
        "a run whose lease lapsed un-renewed was NOT picked up by a second "
        "worker. That contradicts the documented failure mode: an unrenewed lease "
        "that expires makes the run claimable again. If this is now withheld, some "
        "other mechanism is holding the run -- say which in the docstring rather "
        "than deleting this."
    )


# ---------------------------------------------------------------------------
# EXECUTING stays claimable: the M6 constraint, tested
# ---------------------------------------------------------------------------


async def test_executing_with_a_live_lease_is_not_claimed(
    factory: sessionmaker[Session],
) -> None:
    """An ``EXECUTING`` run a live worker is driving is withheld from a second.

    The lease's *first* job: distinguish "alive but busy" from "dead and
    recoverable" in the one state a status column cannot. A worker that approved-
    and-is-driving sits in ``EXECUTING`` with a live lease; a second worker must
    leave it alone, or it would re-drive a run in flight.
    """
    run = await _make_run(factory, status=RunStatus.EXECUTING)
    first = await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_LONG_LEASE)
    assert first is not None and first.id == run.id

    second = await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
    assert second is None, (
        "an EXECUTING run with a live lease was handed to a second worker. The "
        "lease is what tells 'this worker is driving it right now' from 'the "
        "worker died'; a live lease must withhold the row."
    )


async def test_executing_with_an_expired_lease_is_claimed(
    factory: sessionmaker[Session],
) -> None:
    """An ``EXECUTING`` run whose worker died **is** claimable -- the M6 constraint.

    ``EXECUTING`` must stay claimable, and this is why the whole lease design
    exists rather than a status change. A run is ``EXECUTING`` either because the
    pump is driving it right now (lease live -- test above) or because a human
    approved and the worker died before finishing (lease expired). The second is a
    decision a person made that ``docs/milestones.md`` §M6 requires to resume, so
    it must be claimable. Making ``EXECUTING`` unclaimable would strand the
    approved refund; the lease lets a *dead* worker's ``EXECUTING`` run be
    reclaimed while withholding a *live* one.
    """
    run = await _make_run(factory, status=RunStatus.EXECUTING)
    # The worker that was driving it took the lease and then died.
    claimed = await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_LONG_LEASE)
    assert claimed is not None and claimed.id == run.id
    await _expire_lease(factory, run.id)

    recovered = await SqlRunStore(factory).claim_next(
        worker_id="worker-b", lease_seconds=_LONG_LEASE
    )
    assert recovered is not None and recovered.id == run.id, (
        "an EXECUTING run whose lease expired was not claimed. This is the M6 "
        "constraint read the other way: EXECUTING must stay claimable, so a run a "
        "human approved before the worker died can resume. A None here means "
        "EXECUTING became unclaimable, which strands the approved refund."
    )


# ---------------------------------------------------------------------------
# Renewal: a live worker keeps its lease
# ---------------------------------------------------------------------------


async def test_renewing_extends_a_held_lease(factory: sessionmaker[Session]) -> None:
    """``renew_lease`` by the holder extends the deadline; the run stays withheld.

    What a long drive depends on: the worker renews before the lease lapses, so
    the deadline moves forward and a second claim keeps getting ``None``.
    """
    run = await _make_run(factory)
    await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_TINY_LEASE)

    # Renew well before the tiny lease lapses, then prove the run is still held.
    renewed = await SqlRunStore(factory).renew_lease(
        run.id, worker_id="worker-a", lease_seconds=_LONG_LEASE
    )
    assert renewed is True, "the holder's renewal should extend the lease"

    import asyncio

    await asyncio.sleep(_TINY_LEASE * 5)
    assert (
        await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
        is None
    ), (
        "the lease renewer did not keep the run withheld: after renewal the "
        "deadline was pushed out by _LONG_LEASE, so a second claim must still "
        "get None long after the original _TINY_LEASE would have lapsed."
    )


async def test_renewal_by_a_non_holder_is_refused(factory: sessionmaker[Session]) -> None:
    """A worker that no longer holds the run cannot renew it -- the ``claimed_by`` guard.

    The reason the lease is two columns and not one. If a stalled worker's lease
    lapses and a second worker takes the run, the first worker waking up must not
    be able to extend the *new* holder's lease: that would keep a run the first
    worker no longer owns out of everyone else's hands. Renewal is conditional on
    ``claimed_by``, so the stale worker's call affects zero rows and returns
    ``False``.
    """
    run = await _make_run(factory)
    await SqlRunStore(factory).claim_next(worker_id="worker-a", lease_seconds=_TINY_LEASE)
    await _expire_lease(factory, run.id)
    # worker-b steals the lapsed run.
    stolen = await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
    assert stolen is not None and stolen.id == run.id

    refused = await SqlRunStore(factory).renew_lease(
        run.id, worker_id="worker-a", lease_seconds=_LONG_LEASE
    )
    assert refused is False, (
        "worker-a renewed a run worker-b now holds. Without the `claimed_by` "
        "guard, a stalled worker waking after its lease lapsed would extend the "
        "new holder's lease and keep a run it does not own out of the queue."
    )

    # And worker-b, the real holder, can still renew.
    assert (
        await SqlRunStore(factory).renew_lease(
            run.id, worker_id="worker-b", lease_seconds=_LONG_LEASE
        )
        is True
    )


# ---------------------------------------------------------------------------
# The lease is released when the row is handed back
# ---------------------------------------------------------------------------


async def test_parking_releases_the_lease_so_approval_is_claimable_at_once(
    factory: sessionmaker[Session],
) -> None:
    """A parked run's lease is cleared, so approving it is claimable immediately.

    A run parked in ``WAITING_APPROVAL`` is not being driven, so its lease is
    released as it parks. This matters because approval re-queues the run to
    ``EXECUTING``: if the old lease survived, the worker would not pick the
    approved run up until the lease lapsed -- an approval taking minutes to take
    effect for no reason a human could see. The test drives the whole edge:
    claim, park, approve (``EXECUTING``), and assert the restarted worker claims
    it on the next poll rather than waiting out the lease.
    """
    run = await _make_run(factory)
    store = SqlRunStore(factory)
    claimed = await store.claim_next(worker_id="worker-a", lease_seconds=_LONG_LEASE)
    assert claimed is not None
    await store.set_status(run.id, RunStatus.WAITING_APPROVAL)

    # The approval edge: WAITING_APPROVAL -> EXECUTING, as SqlApprovalStore.decide
    # does. Status alone; no lease is written here.
    await store.set_status(run.id, RunStatus.EXECUTING)

    resumed = await SqlRunStore(factory).claim_next(worker_id="worker-b", lease_seconds=_LONG_LEASE)
    assert resumed is not None and resumed.id == run.id, (
        "an approved run (EXECUTING) was not claimable because a stale lease from "
        "the previous worker's drive survived the park. Parking must release the "
        "lease; otherwise approval waits out a lease the operator never set."
    )


# ---------------------------------------------------------------------------
# Renewal inside drain_once: a slow drive keeps its lease
# ---------------------------------------------------------------------------


class _RenewalSpy:
    """A ``RunStore`` that wraps the real one and counts renewals.

    Delegates every method to ``SqlRunStore`` so the claim's real SQL runs, and
    records each ``renew_lease`` call so a test can prove the worker's background
    renewer fired during a drive it did not control.
    """

    def __init__(self, inner: SqlRunStore) -> None:
        self._inner = inner
        self.renew_calls: list[UUID] = []

    async def create(self, **kwargs: object) -> AgentRun:
        return await self._inner.create(**kwargs)  # type: ignore[arg-type]

    async def get(self, run_id: UUID) -> AgentRun | None:
        return await self._inner.get(run_id)

    async def claim_next(self, *, worker_id: str, lease_seconds: float) -> AgentRun | None:
        return await self._inner.claim_next(worker_id=worker_id, lease_seconds=lease_seconds)

    async def renew_lease(self, run_id: UUID, *, worker_id: str, lease_seconds: float) -> bool:
        self.renew_calls.append(run_id)
        return await self._inner.renew_lease(
            run_id, worker_id=worker_id, lease_seconds=lease_seconds
        )

    async def set_status(self, *args: object, **kwargs: object) -> None:
        await self._inner.set_status(*args, **kwargs)  # type: ignore[arg-type]


class _SlowProvider:
    """A provider whose every call sleeps, so a drive outlasts a short lease.

    A drive fast enough to finish inside the lease would never exercise renewal;
    the sleep makes the claim's lease lapse in wall-clock terms unless it is
    renewed, which is exactly the window the background task covers. It wraps the
    real scripted provider so the drive still completes normally -- it is only
    slow.
    """

    def __init__(self, inner: object, *, sleep_seconds: float) -> None:
        self._inner = inner
        self._sleep_seconds = sleep_seconds

    def __getattr__(self, name: str) -> object:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        async def _slow(*args: object, **kwargs: object) -> object:
            import asyncio

            await asyncio.sleep(self._sleep_seconds)
            result = attribute(*args, **kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result

        return _slow


async def test_drain_once_renews_the_lease_while_driving(
    factory: sessionmaker[Session],
) -> None:
    """The worker renews a claimed run's lease during the drive, then stops.

    **Where renewal lives, tested at the level it lives.** ``drain_once`` starts
    a background task that renews the lease every ``lease/3``; this drives a real
    run with a short lease (so the interval is milliseconds and the renewer fires
    even on a fast drive) through a spy store that counts renewals, and asserts
    the renewals happened. It also asserts the task is *stopped* when the drive
    ends: the count must not grow once ``drain_once`` has returned, or a finished
    run would keep a lease alive forever.

    A run that took longer than its lease and was **not** renewed would lose the
    lease mid-drive and be claimed by a second worker; that failure mode is pinned
    directly by ``test_a_lease_that_lapses_mid_drive_is_the_tested_failure_mode``.
    """
    from opspilot.adapters.persistence.repositories import (
        SqlApprovalStore,
        SqlTicketStore,
        SqlToolCallStore,
    )
    from opspilot.tracing.recorder import TraceRecorder
    from opspilot.worker.loop import drain_once
    from tests.agent.test_runtime_fake import ScriptedProvider, UnusedOrchestrator
    from tests.integration.test_worker_claim import RecordingGateway

    # A scripted provider slowed so the drive lasts longer than the renewal
    # interval: the renewer must fire *during* a drive, so the drive cannot be
    # instantaneous. Every provider call sleeps, and the pump makes several, so
    # the drive comfortably exceeds the 0.01s interval below.
    provider = _SlowProvider(ScriptedProvider(), sleep_seconds=0.02)
    spy = _RenewalSpy(SqlRunStore(factory))
    run = await _make_run(factory)

    processed = await drain_once(
        worker_id="worker-a",
        run_store=spy,
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        # ``_SlowProvider`` forwards every attribute through ``__getattr__``, so
        # it satisfies ``ModelProvider`` at run time while being statically
        # invisible to mypy. Declaring the protocol explicitly would mean
        # re-spelling all three methods and losing the forwarding that keeps this
        # a pure delay -- the ignore is the honest description of the trade.
        provider=provider,  # type: ignore[arg-type]
        gateway=RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        recorder_factory=lambda run_id: TraceRecorder(run_id=run_id, session_factory=factory),
        retrieval=None,
        lease_seconds=_DRIVE_LEASE,
    )
    assert processed is True
    assert spy.renew_calls == [run.id] * len(spy.renew_calls) and spy.renew_calls, (
        "the worker drove a run past its lease without renewing it once. Renewal "
        "is what keeps a long run out of a second worker's hands; a run that "
        "outlives an unrenewed lease is the documented failure mode."
    )

    # The renewer stops with the drive. Wait past several more intervals and
    # confirm no further renewals: a task left running would renew a leased run
    # forever.
    import asyncio

    settled = len(spy.renew_calls)
    await asyncio.sleep(_TINY_LEASE * 10)
    assert len(spy.renew_calls) == settled, (
        "the lease renewer kept running after the drive finished. It must be "
        "cancelled when the run reaches its terminal state, or a completed run "
        "carries a lease nobody releases."
    )
