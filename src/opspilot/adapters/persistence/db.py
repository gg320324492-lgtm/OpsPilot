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
    """
    url = settings.database_url
    if settings.is_sqlite:
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            return _sa_create_engine(url, connect_args=connect_args, poolclass=StaticPool)
        return _sa_create_engine(url, connect_args=connect_args)
    return _sa_create_engine(url, pool_pre_ping=True)


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

    Only the claimable mid-flight states are touched (``CLAIMABLE`` minus
    ``RECEIVED``, which has no work to interrupt). ``WAITING_APPROVAL`` is left
    alone because those runs are legitimately waiting on a human, and
    ``COMPLETED``/``FAILED`` are terminal. Returns the number marked.
    """
    interrupted_statuses: Sequence[str] = tuple(
        status.value for status in sorted(CLAIMABLE - {RunStatus.RECEIVED}, key=lambda s: s.value)
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
