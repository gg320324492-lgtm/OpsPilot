"""Engine and session factory, with dialect handling.

Responsibility: build the SQLAlchemy engine from ``Settings.database_url`` and
provide a session/transaction factory. Also the place where Postgres/SQLite
differences are made explicit -- JSONB-vs-JSON, pgvector availability and
``gen_random_uuid()`` emulation all live on the models; the ``FOR UPDATE SKIP
LOCKED`` claim branches on the dialect in ``repositories.py``. This module owns
the engine and the transaction boundary.

Layer: ``adapters``. Reads ``opspilot.settings`` and SQLAlchemy.

Deployment note: for a SQLite URL the claim degrades to a single-threaded
``SELECT`` and vector search is served by the in-memory store -- a documented,
honest gap (``docs/data-model.md`` §6, ADR-0004), not a silent one.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import cast

from sqlalchemy import Engine, event, select, update
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from opspilot.adapters.persistence.models import AgentRun
from opspilot.domain.runs import CLAIMABLE, RunStatus
from opspilot.settings import Settings


def create_engine(settings: Settings) -> Engine:
    """Create a configured SQLAlchemy engine for the configured URL.

    SQLite gets ``check_same_thread=False`` so a FastAPI ``TestClient`` (which
    runs the app on its own thread) can share the connection -- and, for the
    in-memory URL, ``StaticPool`` so every session in a process sees the same
    ``:memory:`` database rather than a fresh empty one per connection.

    Postgres gets ``connect_timeout`` in ``connect_args``. Without it an
    unreachable server does not fail fast: on Windows a silently-dropped SYN
    makes the driver block until the OS's own connect timeout, which can be
    minutes -- and ``GET /ready`` would hang on exactly the state it exists to
    report. A bounded connect is what makes the readiness probe answer *fast
    and unavailable* rather than not answering at all. Five seconds is long
    enough for a healthy network and short enough that an orchestrator's
    healthcheck gets a verdict within its own timeout.
    """
    url = settings.database_url
    if settings.is_sqlite:
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            return _sa_create_engine(url, connect_args=connect_args, poolclass=StaticPool)
        return _sa_create_engine(url, connect_args=connect_args)
    return _sa_create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    """Turn on foreign-key enforcement for a SQLite connection.

    SQLite silently ignores ``FOREIGN KEY`` declarations unless this pragma is
    set, per connection. It matters here because
    ``approval_requests.tool_call_id`` referencing ``tool_calls.id`` is part of
    the safety story: without it the schema would accept an approval pointing at
    a tool call that does not exist.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def register_sqlite_pragmas(engine: Engine) -> None:
    """Attach the SQLite foreign-key listener to *engine*, if it is SQLite.

    Separate from :func:`create_engine` so a caller that builds the engine some
    other way (a test, an existing session) can opt in explicitly.
    """
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)


def session_factory(settings: Settings) -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to the engine.

    ``expire_on_commit=False`` so a domain object built from an ORM row keeps
    its attributes after the transaction commits -- the repositories read the
    values out before returning, but a caller doing so after ``session_scope``
    should not hit a lazy reload against a closed transaction.
    """
    engine = create_engine(settings)
    register_sqlite_pragmas(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session inside a transaction, committing on success.

    Commits on a clean exit, rolls back on any exception, and always closes.
    Repositories never commit (see ``repositories.py``), so this is the one
    place a transaction ends -- which is what lets a caller compose several
    repository calls (create a ticket, then its run) into a single atomic unit.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_interrupted_runs(factory: sessionmaker[Session]) -> int:
    """On worker boot, fail runs left mid-flight with ``failure_reason='interrupted'``.

    ``CLASSIFYING``, ``RETRIEVING``, ``PLANNING`` and ``RESPONDING`` are marked;
    they have no approval behind them and nothing claims them but the pump, so a
    run found in one of them was mid-step when the process died.
    ``RECEIVED`` has no work to interrupt, and ``COMPLETED``/``FAILED`` are
    terminal.

    ``WAITING_APPROVAL`` and ``EXECUTING`` are **left alone**, and the second is
    the interesting one. A run is ``EXECUTING`` either because the pump is
    driving it right now or because a human approved and the worker died before
    finishing -- and a single-column status cannot tell those apart. Sweeping it
    turns the second case into ``FAILED('interrupted')``, which discards a
    decision a person made and leaves an approved refund unmade: the run can
    never resume, and nothing records that anyone said yes. ``docs/
    milestones.md`` §M6 requires that approving later resumes the run under a
    worker that restarted in between, and ``docs/agent-state-machine.md`` §3
    says ``FAILED`` means OpsPilot did not finish the job -- neither is true of
    a run that is one resume away from finishing.

    An ``EXECUTING`` row is claimable, so ``claim_next`` reclaims it like any
    other. Automatic *mid-step* resume remains Phase 2 work; what is claimed here
    is narrower and is not pretending to more -- a run whose approval has already
    been granted completes that approval rather than losing it. A run that was
    genuinely mid-write is made safe by the refund's idempotency key, not by the
    sweep.

    Returns the number marked.
    """
    interrupted_statuses: Sequence[str] = tuple(
        status.value
        for status in sorted(
            CLAIMABLE - {RunStatus.RECEIVED, RunStatus.EXECUTING}, key=lambda s: s.value
        )
    )
    with session_scope(factory) as session:
        result = cast(
            CursorResult[tuple[object, ...]],
            session.execute(
                update(AgentRun)
                .where(AgentRun.status.in_(interrupted_statuses))
                .values(status=RunStatus.FAILED.value, failure_reason="interrupted")
            ),
        )
        return int(result.rowcount or 0)


def run_exists(factory: sessionmaker[Session], run_id: object) -> bool:
    """Whether a run row exists, for callers that only need existence."""
    with session_scope(factory) as session:
        return session.execute(select(AgentRun.id).where(AgentRun.id == run_id)).first() is not None


__all__ = [
    "create_engine",
    "mark_interrupted_runs",
    "register_sqlite_pragmas",
    "run_exists",
    "session_factory",
    "session_scope",
]
