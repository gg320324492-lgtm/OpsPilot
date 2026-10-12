"""Two claimants, one PostgreSQL: what ``FOR UPDATE SKIP LOCKED`` guarantees.

## Why this file exists

``tests/integration/test_worker_claim.py`` drives the claim *predicate* on
SQLite: a ``RECEIVED`` run is claimed, a ``WAITING_APPROVAL`` run is not, boot
preserves what it must. What it cannot touch is the clause that is supposed to
make two workers safe. SQLite has no ``SKIP LOCKED``; the store branches on the
dialect and runs the same statement *without* the locking clause there, so on
every machine the default suite runs on, the one database-level guarantee behind
the concurrency design was never executed. ``docs/limitations.md`` §2 and §8
record it as a gap, ADR-0004 lists it as the divergence with no test, and
``claim_next``'s own docstring used to name a two-worker ``@pytest.mark.postgres``
test that did not exist.

This file is that test. It carries ``@pytest.mark.postgres`` and skips cleanly,
naming ``OPSPILOT_DATABASE_URL``, where no Postgres is reachable -- the ordinary
local outcome (ADR-0004). The CI ``verify`` job selects it by node id and asserts
it **PASSED**, so there a skip is a failure and the gap cannot reopen unnoticed.

## What ``SKIP LOCKED`` actually guarantees -- and what it does not

The clause is worth exactly what the claim *transaction* holds. `SELECT ... FOR
UPDATE SKIP LOCKED` locks the rows it returns for the life of the transaction
that issued it, and a second claim running **while that transaction is open**
skips those rows. That is the property this file asserts, at the session boundary
the store itself uses: while one claim holds a row, a real
``SqlRunStore.claim_next`` from a second engine does not hand back that row.

It is important to be exact about what that is **not**. ``SqlRunStore.claim_next``
bound to a ``sessionmaker`` opens its own transaction, runs the ``SELECT``,
returns, and commits -- so it holds the row lock only for the duration of the
``SELECT`` and never changes the row's status. The worker is expected, per
``docs/adr/0005`` and ``docs/architecture.md``, to hold the row while the runtime
drives the run to a terminal or parked state; the shipped implementation does
not, because the lock closes with the claim. Two workers polling a moment apart
therefore both see the run as claimable and both receive it. That is characterised
in ``test_a_released_claim_is_reclaimable_by_a_second_worker`` and reported as a
defect in the change that added this file; it is *not* fixed here, and this file
does not pretend the clause covers it. What this file proves is the narrower
property the clause really has: **overlapping claims take different rows.**

## Why a held lock, not a bare thread race

Two claimant tasks racing in a bare loop was rejected as the *correctness* test:
whether they overlap at all is scheduling luck, and a race that cannot fail is
worthless. The contention is made deterministic instead. ``claim_next`` keeps its
lock for one statement, so a second ``claim_next`` issued from another connection
during that statement meets a **definitely locked** row. That window is what the
interleave and the labelled race below reproduce, and ``SKIP LOCKED`` is the only
reason a second claimant sees the *next* row instead of blocking or duplicating.

## Why two factories

Every store here is bound to its own ``sessionmaker`` -- its own engine and pool
-- the way a second worker process is. ``SqlRunStore.claim_next`` owns its
transaction through ``db.session_scope``, so pointing two claimants at two real
session boundaries is what reproduces two workers; a caller issuing the ``SELECT``
against a single shared session would be testing a copy of the query, not the
store's own claim.
"""

from __future__ import annotations

import asyncio
import os
import textwrap
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence import models as orm
from opspilot.adapters.persistence.repositories import (
    _CLAIMABLE_VALUES,
    SqlRunStore,
    SqlTicketStore,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.settings import Settings
from tests._pgvector_target import guarded_pg_schema

#: The variable the other postgres-marked tests read (``test_citations.py``,
#: ``test_migration_schema.py``). One variable configures them all.
_URL_VARIABLE = "OPSPILOT_DATABASE_URL"

#: How long a claimant that has to *wait* for a lock waits before it is treated
#: as blocked. The property under test is that ``SKIP LOCKED`` makes a claim
#: **not wait** -- it skips the locked row. A wait long enough to prove the claim
#: is not skipping, short enough that a mutation produces a red test rather than
#: a CI timeout. Applied two ways: as a Postgres ``lock_timeout`` (so the wait
#: becomes an error) and as an ``asyncio`` timeout (so any other wait is caught).
_CLAIM_GRACE_SECONDS = 5.0


class ClaimBlockedError(AssertionError):
    """A claim waited for a lock instead of skipping the locked row.

    Raised in place of an unbounded wait, so a claim that does not skip fails the
    test with a message instead of hanging the run. It is an ``AssertionError`` so
    pytest reports it as the assertion failure it is.
    """

    def __init__(self, worker_id: str) -> None:
        super().__init__(
            f"claimant {worker_id!r} waited for a row lock rather than skipping "
            "it. `FOR UPDATE SKIP LOCKED` makes a claimant skip a locked row; a "
            "claim that blocks means the clause is not doing its job (or is "
            "missing), which is the defect this file exists to catch."
        )


def _bound_lock_waits(engine: object) -> None:
    """Make row-lock waits on ``engine`` fail fast instead of hanging.

    Sets ``lock_timeout`` on every connection the engine opens, so a claim that
    would otherwise block on a row another transaction holds raises
    ``LockNotAvailable`` (surfaced by SQLAlchemy as ``OperationalError``) after
    ``_CLAIM_GRACE_SECONDS``. The marked tests turn that into a red assertion --
    the pre-``SKIP LOCKED`` behaviour is a failure, and this makes it a fast one.

    Deliberately only on the *claiming* connection. The holder's lock is a plain
    ``FOR UPDATE`` and must be allowed to wait if it ever needs to; it is the
    claimant's *not* waiting that is the property.
    """

    @event.listens_for(engine, "connect")
    def _set_lock_timeout(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute(f"SET lock_timeout = {int(_CLAIM_GRACE_SECONDS * 1000)}")
        cursor.close()


def _postgres_url() -> str | None:
    """The configured Postgres URL, or ``None`` where there is none.

    ``None`` is the ordinary local case -- no Postgres, no Docker -- and the
    marked tests that spread this into a factory skip before they go further.
    """
    url = os.environ.get(_URL_VARIABLE, "")
    return url if url.startswith("postgresql") else None


@pytest.fixture
def pg_factories() -> Iterator[list[sessionmaker[Session]]]:
    """Two independent session factories over one Postgres, schema created.

    The schema comes from ``Base.metadata.create_all`` rather than the migration:
    this file is about the claim's locking clause, and
    ``test_migration_schema.py`` already asserts the migration produces the
    schema the ORM expects. The tables are laid down through
    :func:`tests._pgvector_target.guarded_pg_schema`, the same guard the two
    pgvector tests use: it refuses to run against a database that holds rows, and
    tears down only what it created. That guard exists because two tests once
    dropped every application table in whatever database the URL named -- this
    file writes to the same variable, so it takes the same protection rather than
    repeating the incident.

    It runs against whatever ``OPSPILOT_DATABASE_URL`` names -- which the CI
    ``verify`` job points at its throwaway service container, exactly as the two
    pgvector tests do. A run here leaves no residue.
    """
    url = _postgres_url()
    if url is None:
        pytest.skip(f"no PostgreSQL configured (set {_URL_VARIABLE}); ADR-0004")
    # Two factories: two engines, two pools, two connections. Two callers that
    # cannot share a session is what two workers are.
    first = db.session_factory(Settings(DATABASE_URL=url))
    second = db.session_factory(Settings(DATABASE_URL=url))
    # The claimant (`second`) must not wait for a lock; the holder (`first`) may.
    _bound_lock_waits(second.kw["bind"])

    engine = first.kw["bind"]
    try:
        with guarded_pg_schema(engine, owner=__name__):
            yield [first, second]
    finally:
        # A `finally`, not a statement after the block: a refusal from the guard
        # raises straight through here, and an undisposed engine would hold a
        # connection open against the database that just refused to be touched.
        first.kw["bind"].dispose()
        second.kw["bind"].dispose()


async def _claim_without_blocking(
    factory: sessionmaker[Session], *, worker_id: str
) -> AgentRun | None:
    """Claim via ``SqlRunStore.claim_next``, failing fast if it has to wait.

    The property under test is that a claimant **skips** a row another
    transaction holds. A claim that instead *blocks* on that row is the
    pre-``SKIP LOCKED`` behaviour, and it is a failure -- but a failure that
    manifests as an indefinite wait is a CI timeout, not a red test. So the
    claim is bounded twice: a Postgres ``lock_timeout`` on the claiming session
    turns "waiting for a row lock" into a raised error after a short grace, and
    an ``asyncio.wait_for`` backstops the whole call for any wait that is not a
    row lock. Either trips, the caller sees :class:`ClaimBlockedError`, and the
    assertion that follows says what it means.
    """
    store = SqlRunStore(factory)

    async def claim() -> AgentRun | None:
        return await store.claim_next(worker_id=worker_id)

    try:
        return await asyncio.wait_for(claim(), timeout=_CLAIM_GRACE_SECONDS)
    except (TimeoutError, OperationalError) as error:
        raise ClaimBlockedError(worker_id) from error


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


async def _hold_locked_claim(factory: sessionmaker[Session]) -> tuple[Session, UUID | None]:
    """Open the exact claim ``claim_next`` runs, and keep its row lock held.

    This is the store's own statement -- the same ``SELECT`` over the same
    ``_CLAIMABLE_VALUES`` predicate from ``CLAIMABLE``, with the same ``FOR UPDATE
    SKIP LOCKED`` -- inside a transaction this function does **not** commit, so
    the returned session holds the row lock until the caller ends it. That is
    what lets a test choose when the lock is released instead of racing for it:
    the second claimant can be run against a definitely locked row.

    Returns the open session and the locked row's id (``None`` if nothing was
    claimable), or the caller rolls back and closes the session; nothing here
    commits.
    """
    session = factory()
    statement = (
        select(orm.AgentRun)
        .where(orm.AgentRun.status.in_(_CLAIMABLE_VALUES))
        .order_by(orm.AgentRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = session.execute(statement).scalars().first()
    return session, (None if row is None else row.id)


def _delivered_twice(claims: list[AgentRun | None]) -> list[UUID]:
    """The run ids handed to more than one claimant -- the property, computed once."""
    seen: set[UUID] = set()
    duplicates: list[UUID] = []
    for claim in claims:
        if claim is None:
            continue
        if claim.id in seen:
            duplicates.append(claim.id)
        seen.add(claim.id)
    return duplicates


def _claim_next_locks_on_postgres() -> bool:
    """Whether ``SqlRunStore.claim_next`` applies ``FOR UPDATE SKIP LOCKED``.

    Read from the production source rather than reconstructed here: the point of
    a pin is to fail when the store changes, and a copy of the statement in the
    test cannot do that. The assertion is over the *shape* -- a ``with_for_update``
    call with ``skip_locked=True`` inside the ``claim_next`` body
    (``repositories.py``) -- which is what a mutation would remove.
    """
    import ast
    import inspect

    from opspilot.adapters.persistence import repositories

    source = inspect.getsource(repositories.SqlRunStore.claim_next)
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "with_for_update":
            continue
        if any(
            keyword.arg == "skip_locked"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# The property the clause really has: overlapping claims take different rows
# ---------------------------------------------------------------------------


@pytest.mark.postgres
async def test_a_held_claim_makes_the_next_claimant_skip_to_another_row(
    pg_factories: list[sessionmaker[Session]],
) -> None:
    """Two claimable runs, one held locked: the second claimant gets the other.

    The property a single claimable row cannot show. The first claimant holds the
    lock on the oldest row; a real ``SqlRunStore.claim_next`` from a second engine
    runs while that lock is held. With ``SKIP LOCKED`` it skips the locked row and
    takes the next one. Without the clause it would block on the locked row until
    the holder released it -- and the mutation that takes the first row
    unconditionally (``test_the_clause_is_what_makes_the_second_claimant_skip``
    pins the shape) would hand the *same* row to both.

    The holder commits only after the second claim has returned, so the order is
    chosen and the interleave is deterministic rather than a race.
    """
    first, second = pg_factories
    oldest = await _make_run(first)
    newest = await _make_run(first)

    holder, held = await _hold_locked_claim(first)
    try:
        assert held == oldest.id, "the oldest run should be the one this transaction locks"

        other = await _claim_without_blocking(second, worker_id="worker-b")
        assert other is not None, (
            "the second claimant got nothing while a claimable row existed and "
            "another was locked. `SKIP LOCKED` skips the locked row and takes the "
            "next; a None here means the claim is not reading past the lock."
        )
        assert other.id != oldest.id, (
            "both claimants hold the oldest run. This is a run claimed twice: "
            "`SKIP LOCKED` did not skip the locked row."
        )
        assert other.id == newest.id, (
            "the second claimant took a row that was neither the locked one nor "
            "the only other claimable one, so this is not exercising the skip."
        )
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.postgres
async def test_an_overlapping_claim_takes_the_locked_run_once(
    pg_factories: list[sessionmaker[Session]],
) -> None:
    """One claimable run held by one claimant: the second claimant gets ``None``.

    The property in its smallest form. The holder locks the only claimable row; a
    real ``claim_next`` from the second engine runs while it is held. With ``SKIP
    LOCKED`` every claimable row is locked, so the second claimant gets ``None``
    -- the run reaches one claimant. The mutation that drops the clause would
    block here until the connection timeout, and the mutation that takes the first
    row unconditionally would hand back the *same* run: both are the duplicate
    this test exists to catch.

    After the holder releases, the row is claimable again -- which proves the
    ``None`` above was the lock, not the row having vanished.
    """
    first, second = pg_factories
    run = await _make_run(first)

    holder, held = await _hold_locked_claim(first)
    try:
        assert held == run.id

        intruder = await _claim_without_blocking(second, worker_id="worker-b")
        assert intruder is None, (
            f"run {run.id} was delivered to a second claimant while the first "
            "still held it. A claimable run must reach exactly one claimant for "
            "as long as it is locked; `SKIP LOCKED` is the clause that guarantees it."
        )

        holder.rollback()
        recovered = await SqlRunStore(second).claim_next(worker_id="worker-b")
        assert recovered is not None and recovered.id == run.id, (
            "the run was not reclaimable after the holder released it, so the "
            "earlier None did not mean what this test claims it meant."
        )
    finally:
        holder.close()


@pytest.mark.postgres
async def test_two_claimants_racing_over_one_locked_row_never_share_it(
    pg_factories: list[sessionmaker[Session]],
) -> None:
    """Two overlapping claim transactions never hand the same run to both.

    A real second claimant is issued to run **while** the first holds the lock,
    inside a timeout that would trip if that claimant blocked on the lock (which
    is what the pre-``SKIP LOCKED`` behaviour would do). Because the first holds a
    lock the second must skip, there is no interleaving that gives both the same
    row: what scheduling varies is *which* row the second claimant gets, never
    *whether* one row is shared. The assertion is the delivered set -- no id
    appears twice.

    Three runs, so the second claimant is not forced onto a single leftover: it
    must genuinely skip the locked one and select from the rest.
    """
    first, second = pg_factories
    runs = [await _make_run(first) for _ in range(3)]
    run_ids = {run.id for run in runs}

    holder, held = await _hold_locked_claim(first)
    try:
        assert held is not None, "the oldest run should be locked by this transaction"

        # `second` claims while `first` holds its row. Under `SKIP LOCKED` it
        # cannot receive `held`; it takes the next claimable row. The bounded
        # helper makes a claim that blocks on the held lock fail here, not hang.
        other = await _claim_without_blocking(second, worker_id="worker-b")
        assert other is not None, (
            "the second claimant got nothing while claimable rows existed and one "
            "was locked; `SKIP LOCKED` must skip the locked row, not go without a run."
        )
        assert other.id != held, (
            f"both overlapping claimants hold run {held}. `FOR UPDATE SKIP LOCKED` "
            "is the only thing preventing this, and it did not."
        )
        assert other.id in run_ids, "the second claimant returned a run this test did not create"
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.postgres
async def test_a_released_claim_is_reclaimable_by_a_second_worker(
    pg_factories: list[sessionmaker[Session]],
) -> None:
    """Two claims a moment apart both receive an unchanged run -- the known gap.

    This is **not** the promised property; it is its boundary, asserted so the
    boundary is visible in code rather than only in prose. ``claim_next`` bound to
    a ``sessionmaker`` opens its own transaction, runs the ``SELECT``, returns and
    commits. The lock is gone the instant the claim returns, and the claim does
    not change the row's status, so a second worker whose poll runs *after* the
    first claim has finished sees the same ``RECEIVED`` row as claimable and
    receives it too. Two workers therefore share one run unless something holds
    the row across the claim.

    ``docs/adr/0005`` and ``docs/architecture.md`` both describe the worker
    holding the row while the runtime drives the run to a terminal or parked
    state; the shipped claim does not, because its lock closes with the
    statement. So the concurrency guarantee those documents advertise ("scaling
    out is adding replicas") does not hold as implemented. This test records the
    real behaviour; fixing the implementation would make it fail, which is the
    prompt to delete it and record the fix, not to loosen it.
    """
    first, second = pg_factories
    run = await _make_run(first)

    worker_a = await SqlRunStore(first).claim_next(worker_id="worker-a")
    # Bounded, because a claim that *waited* here would be the fix this test
    # documents the absence of -- and a wait is not what a green run should take.
    worker_b = await _claim_without_blocking(second, worker_id="worker-b")

    assert worker_a is not None and worker_a.id == run.id
    assert worker_b is not None and worker_b.id == run.id, (
        "the second, non-overlapping claim did NOT receive the run the first "
        "claim already returned. That is a change in behaviour: if the claim now "
        "holds its row (or marks it claimed) across the worker's driving of the "
        "run, the `SKIP LOCKED` guarantee is stronger than this test records -- "
        "delete this test and record that the two-worker property now holds end "
        "to end."
    )


# ---------------------------------------------------------------------------
# The phase-boundary contract on a Postgres, and the mutation guard
# ---------------------------------------------------------------------------


@pytest.mark.postgres
async def test_a_waiting_approval_run_is_not_claimable_on_postgres(
    pg_factories: list[sessionmaker[Session]],
) -> None:
    """The claim predicate holds on the real dialect, not only on SQLite.

    ``test_worker_claim.py`` asserts this on SQLite; the partial-index-driven
    predicate is Postgres-only in production (ADR-0004). A ``WAITING_APPROVAL``
    run must not be claimed -- the parked run is the one state a worker must not
    hold, and it is the property that makes the approval gate stop work rather
    than loop on it.
    """
    first, _ = pg_factories
    parked = await _make_run(first, status=RunStatus.WAITING_APPROVAL)

    claimed = await SqlRunStore(first).claim_next(worker_id="worker-a")
    assert claimed is None, (
        f"a WAITING_APPROVAL run ({parked.id}) was claimed. The parked run is "
        "what the human decision is waiting on; claiming it re-drives work nobody "
        "has approved."
    )


def test_claim_next_in_the_store_still_asks_for_skip_locked() -> None:
    """``SqlRunStore.claim_next`` applies ``FOR UPDATE SKIP LOCKED`` on Postgres.

    Runs with no database and no Docker -- so the clause the marked tests
    exercise is also pinned where the suite runs by default. It reads the
    production method's source (a ``with_for_update(skip_locked=True)`` inside
    ``claim_next``) rather than a copy of the statement in the test, so an edit
    that drops the branch is red on every machine instead of only where a real
    Postgres is reachable. The marked tests above record what the loss would cost
    a real deployment; this one makes the loss catchable without one.
    """
    assert _claim_next_locks_on_postgres(), (
        "SqlRunStore.claim_next no longer applies `FOR UPDATE SKIP LOCKED` on "
        "Postgres. That clause is the only thing that makes two workers take "
        "different rows; without it they claim the same run."
    )


def test_the_skip_is_what_withholds_the_locked_row() -> None:
    """A held lock withholds a row from a second claim *only* with ``SKIP LOCKED``.

    The classifier the marked tests rest on: that a second claim is denied a row
    another transaction holds because the clause is present, and would not be
    denied it otherwise. Pinned by compiling the claim statement both ways
    against the Postgres dialect -- no database, no Docker -- so the mutation the
    marked tests are meant to catch is also expressed here, on every machine.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.engine import Dialect

    # `postgresql.dialect.__init__` is untyped in SQLAlchemy's stubs; the
    # instance is what `Dialect` describes, so the ignore is narrow and named.
    dialect: Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    base = (
        select(orm.AgentRun)
        .where(orm.AgentRun.status.in_(_CLAIMABLE_VALUES))
        .order_by(orm.AgentRun.created_at)
        .limit(1)
    )
    with_skip = str(base.with_for_update(skip_locked=True).compile(dialect=dialect))
    without_skip = str(base.compile(dialect=dialect))

    assert "FOR UPDATE SKIP LOCKED" in with_skip, (
        f"`with_for_update(skip_locked=True)` no longer compiles to `FOR UPDATE "
        f"SKIP LOCKED`:\n{with_skip}"
    )
    assert "SKIP LOCKED" not in without_skip, (
        "the statement without the lock clause carries SKIP LOCKED, so this "
        "classifier cannot tell the two apart and the mutation it guards proves "
        "nothing"
    )
