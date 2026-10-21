"""The step-sequence allocator must take the lock it claims to take.

## Why this file exists

``docs/data-model.md`` §2 says a run's step sequence is allocated "inside the
same transaction as the step insert (``SELECT COALESCE(MAX(sequence),0)+1 ...
FOR UPDATE``)", and ``repositories.py``'s docstring repeated that sentence.
Neither was true of the code: the allocator ran
``SELECT sequence ... ORDER BY sequence DESC LIMIT 1`` and added one in
Python, with **no** ``FOR UPDATE`` anywhere. A reader auditing the concurrency
argument for the timeline would go looking for a lock the code never took.

The fix has a catch, and this file is the catch. The store runs on **both**
SQLite and Postgres (``docs/adr/0004``), and SQLite has no ``FOR UPDATE``.
So the question is not "does SQLite support the clause" but "what does
SQLAlchemy's SQLite dialect do with it":

* it **renders it away** -- ``SQLiteCompiler.for_update_clause`` returns the
  empty string, so the emitted SQL is a plain ``SELECT``; and
* it does **not** raise, and does not warn.

That is what the first two tests below assert, using the statement the
allocator actually runs rather than a copy of it. A test that rebuilt the
statement itself would keep passing if the allocator lost its lock.

## What the lock does and does not buy

The third and fourth tests are the ones that state the honest version of the
guarantee, because "two writers cannot allocate the same number" is **not**
what a row lock on the highest step row buys:

* A step row is immutable. Nothing else ever contends for it, so the lock
  never blocks anybody.
* On a run with **no steps yet** the locking select matches no row, and a
  ``FOR UPDATE`` over no rows locks nothing.

What actually stops a duplicate is the claim path -- ``claim_next`` takes the
run row with ``FOR UPDATE SKIP LOCKED``, so a run has exactly one writer --
and behind that the table's ``UNIQUE (run_id, sequence)`` constraint, which
**refuses** the second writer rather than letting the timeline silently
reorder. The fourth test asserts that invariant directly, by racing two
writers against each other and checking what the table ends up holding.

The aggregate form the doc quoted is also not the form the code can use:
Postgres refuses ``FOR UPDATE`` on an aggregate query ("FOR UPDATE is not
allowed with aggregate functions"), which is why the allocator reads the
highest sequence with ``ORDER BY sequence DESC LIMIT 1`` and adds one in
Python.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.repositories import SqlRunStore, SqlTicketStore
from opspilot.settings import Settings


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh file-backed SQLite schema.

    File-backed rather than ``:memory:``: the two writer threads below need
    two connections to the *same* database, and an in-memory database is
    private to the connection that opened it unless the engine pins a single
    pooled connection to it.
    """
    directory = tempfile.mkdtemp()
    url = f"sqlite+pysqlite:///{pathlib.Path(directory) / 'seq.db'}"
    factory = db.session_factory(Settings(DATABASE_URL=url))
    engine = factory.kw["bind"]
    models.Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


def _compiled(run_id: UUID, dialect: object) -> str:
    """The SQL the allocator's statement renders to, for ``dialect``."""
    statement = db.next_step_sequence_statement(run_id)
    return str(statement.compile(dialect=dialect))


def test_the_sequence_statement_is_locked_on_postgres() -> None:
    """On Postgres the allocator's select carries ``FOR UPDATE``.

    This is the lock ``docs/data-model.md`` §2 names. Without it the
    statement is a plain read, and the document is describing a lock the
    production path never takes.
    """
    sql = _compiled(UUID(int=1), postgresql.dialect())

    assert "FOR UPDATE" in sql, (
        f"the step-sequence statement does not take a row lock on Postgres: {sql!r}. "
        "docs/data-model.md §2 says the sequence is allocated under one."
    )


def test_the_sequence_statement_drops_the_lock_on_sqlite(
    factory: sessionmaker[Session],
) -> None:
    """On SQLite the same statement renders without the clause -- and runs.

    SQLite has no ``FOR UPDATE``, and SQLAlchemy's SQLite compiler drops it
    when it renders the statement. The store runs on SQLite (ADR-0004), so
    this is the difference between "the lock is taken on Postgres and skipped
    on SQLite" and "the store cannot be used on SQLite at all".
    """
    sql = _compiled(UUID(int=1), sqlite.dialect())

    assert "FOR UPDATE" not in sql, (
        f"the SQLite dialect rendered the row lock into the statement: {sql!r}. "
        "SQLite has no FOR UPDATE; the dialect is expected to drop it."
    )

    # And the statement really does execute on SQLite -- the dialect dropping
    # the clause is only useful if the rest of the statement still works.
    with db.session_scope(factory) as session:
        assert db.next_step_sequence(session, UUID(int=1)) == 1


def test_the_allocator_is_monotonic_within_a_run(factory: sessionmaker[Session]) -> None:
    """Each allocation for one run returns the next number, starting at one."""
    run_id = _make_run(factory)
    with db.session_scope(factory) as session:
        assert db.next_step_sequence(session, run_id) == 1
    with db.session_scope(factory) as session:
        session.add(_step(run_id, db.next_step_sequence(session, run_id)))
    with db.session_scope(factory) as session:
        assert db.next_step_sequence(session, run_id) == 2


def test_two_writers_allocating_for_one_run_never_persist_a_duplicate(
    factory: sessionmaker[Session],
) -> None:
    """Racing two writers leaves the table with one step per sequence number.

    This is the guarantee that actually holds, and it is enforced by the
    table's ``UNIQUE (run_id, sequence)`` constraint: whichever writer loses
    the race is **refused** by the database, and the timeline never ends up
    holding two steps with the same number.

    The allocator's own row lock does not decide this race -- a step row is
    immutable, and on a run with no steps yet the locking select matches
    nothing at all. So the test asserts the outcome that must hold either
    way: the persisted sequences are pairwise distinct, and a writer that did
    not get a number was refused rather than silently writing a duplicate.

    SQLite is where this runs (ADR-0004), and on SQLite the outcome of the
    race is genuinely undecided -- sometimes both writers get distinct
    numbers, sometimes the second is refused. The test asserts the invariant,
    not which of the two happens.
    """
    for _ in range(5):
        run_id = _make_run(factory)
        barrier = threading.Barrier(2)
        allocated: list[int] = []
        refused: list[Exception] = []

        def writer() -> None:
            try:
                with db.session_scope(factory) as session:
                    barrier.wait(timeout=10)
                    sequence = db.next_step_sequence(session, run_id)
                    session.add(_step(run_id, sequence))
                    session.flush()
                allocated.append(sequence)
            except Exception as exc:  # noqa: BLE001 -- recorded, asserted below
                refused.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not [t for t in threads if t.is_alive()], "a writer thread hung"

        with db.session_scope(factory) as session:
            sequences = [
                row[0]
                for row in session.execute(
                    select(models.AgentStep.sequence).where(models.AgentStep.run_id == run_id)
                ).all()
            ]

        assert len(sequences) == len(set(sequences)), (
            f"two steps share a sequence number for one run: {sequences!r}. The "
            "UNIQUE (run_id, sequence) constraint should have refused the second "
            "writer."
        )
        assert len(sequences) == len(allocated), (
            f"persisted {len(sequences)} steps but {len(allocated)} writers reported "
            f"success; the losers were {refused!r}."
        )
        for error in refused:
            assert isinstance(error, (IntegrityError, OperationalError)), (
                f"a writer failed with an unexpected error: {error!r}. The only "
                "refusals this race may produce are the unique constraint and "
                "SQLite's own write lock."
            )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _step(run_id: UUID, sequence: int) -> models.AgentStep:
    return models.AgentStep(
        run_id=run_id,
        sequence=sequence,
        step_type="response",
        started_at=datetime.now(UTC),
    )


def _make_run(factory: sessionmaker[Session]) -> UUID:
    """Create a ticket and a run, returning the run id.

    ``agent_runs.ticket_id`` is not nullable, so the ticket is required
    (``docs/data-model.md`` §2).
    """

    async def _make() -> UUID:
        ticket_id = await SqlTicketStore(factory).create(
            subject="duplicate charge", body="charged twice", customer_email="a@example.com"
        )
        run = await SqlRunStore(factory).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
        return run.id

    return asyncio.run(_make())
