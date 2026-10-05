"""Engine and session factory, with dialect handling.

Responsibility: build the SQLAlchemy engine from ``Settings.database_url`` and
provide a session/transaction factory. Also the place where Postgres/SQLite
differences are made explicit -- the worker's ``FOR UPDATE SKIP LOCKED`` claim,
JSONB-vs-JSON, pgvector availability and ``gen_random_uuid()`` emulation all
branch on the dialect here rather than leaking conditionals into repositories.

Layer: ``adapters``. Reads ``opspilot.settings`` and SQLAlchemy.

Deployment note: for a SQLite URL the claim degrades to a single-threaded
``SELECT`` and vector search is served by the in-memory store -- a documented,
honest gap (``docs/data-model.md`` §6, ADR-0004), not a silent one.
"""

from __future__ import annotations

from collections.abc import Iterator

from opspilot.settings import Settings


def create_engine(settings: Settings) -> object:
    """Create a configured SQLAlchemy engine for the configured URL.

    M0 stub -- returns the engine in M1. Typed loosely here because the
    ``Engine`` type is a SQLAlchemy import the stub does not need yet.
    """
    raise NotImplementedError


def session_factory(settings: Settings) -> object:
    """Return a ``sessionmaker`` bound to the engine. M0 stub."""
    raise NotImplementedError


def session_scope(settings: Settings) -> Iterator[object]:
    """Yield a session inside a transaction, committing on success.

    The context manager makes "the status update and its step insert commit
    together" the default rather than something a caller must remember. M0 stub.
    """
    raise NotImplementedError


def mark_interrupted_runs(settings: Settings) -> int:
    """On worker boot, fail runs left mid-flight with ``failure_reason='interrupted'``.

    Only mid-flight states are touched; ``WAITING_APPROVAL`` is left alone
    because those runs are legitimately waiting. Returns the number marked. M0
    stub.
    """
    raise NotImplementedError
