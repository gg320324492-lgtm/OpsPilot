"""Readiness probing: is the database reachable, and is it migrated to head?

Responsibility: build the async callable ``GET /ready`` invokes, which answers
two independent questions -- can a connection be opened, and does
``alembic_version`` name the same revision the migration scripts consider head.

Layer: ``adapters`` (persistence). It reads ``Settings`` and the migration
scripts, and is imported *lazily* by ``api/app.py`` -- the same degradation rule
that lets the API be built with fakes while the persistence layer is absent.

Why this module exists at all
-----------------------------

``api/routers/health.py`` had a ``readiness_check`` hook on ``app.state`` and no
caller ever bound one, so every deployment reported ``database: ok`` without
opening a connection. A readiness probe that always says ready is worse than no
probe: it is what an orchestrator uses to decide whether to send traffic, so it
routes requests to a process that cannot serve them. Compose keys ``api``'s
healthcheck on ``/ready`` (``docker-compose.yml``), which is exactly the
deployment this defect would have poisoned.

The two checks
--------------

``database`` opens a connection and runs ``SELECT 1``. That is deliberately the
cheapest query that still proves the socket, the credentials and the current
database are usable -- a probe that ran a business query would fail for reasons
that are not the probe's to report.

``migrations`` reads ``alembic_version.version_num`` and compares it to the
script directory's head. ``docs/api-contract.md`` §7 names this exact table. A
database that is reachable but one revision behind is the state that produces
"table does not exist" three layers down, which is the failure this probe is
for.

SQLite
------

Both checks run against SQLite unchanged: ``SELECT 1`` works anywhere, and
``alembic_version`` is a real table on SQLite too (the initial migration is
dialect-guarded so ``alembic upgrade head`` runs there -- ADR-0004). The local
dev path is SQLite and it is the one configuration a developer runs, so a probe
that only worked on Postgres would be useless where it is used most.

Failure is a state, not an exception
------------------------------------

Every failure mode -- an unreachable server, a missing table, a connection that
times out -- is caught and returned as a *named check*, never raised. The route
turns a non-``ok`` check into a ``503``; a raised exception would become a
``500``, which tells a probe nothing about which half failed and reports a
reachable-but-unmigrated database as a crash.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.settings import Settings

__all__ = ["build_readiness_check", "resolve_migrations_dir"]

# The two checks, in the order the response names them. Ordered so the JSON body
# reads the same on every probe; a dict from a set would be non-deterministic.
_CHECK_NAMES = ("database", "migrations")


def resolve_migrations_dir(start: Path | None = None) -> Path | None:
    """Locate the repository's ``migrations/`` directory, or ``None``.

    The Alembic scripts are not part of the installed package -- they live at the
    repository root and are mounted into the image (``docker-compose.yml``), not
    copied into site-packages. So the directory is found by walking up from a
    known file until a ``migrations/env.py`` appears, rather than by a
    package-relative path that an editable install would satisfy but a wheel
    would not.

    ``None`` rather than a raise: a deployment whose migrations directory is
    genuinely absent cannot answer the migrations check, and that is a check
    result (``migrations: unknown``), not a startup failure. Reporting it as a
    state keeps the probe honest instead of crashing the route.
    """
    here = start or Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations"
        if (candidate / "env.py").is_file():
            return candidate
    return None


def _current_revision(factory: sessionmaker[Session]) -> str | None:
    """Read ``alembic_version.version_num``, or ``None`` when the table is absent.

    An unmigrated database has no ``alembic_version`` table at all, which is a
    distinct state from a migrated one -- so the missing table is caught and
    reported as ``None`` rather than propagating the driver's ``ProgrammingError``
    as a 500. A migrated database whose table is empty is the same answer: no
    revision has been stamped.
    """
    with factory() as session:
        try:
            row = session.execute(text("SELECT version_num FROM alembic_version")).first()
        except SQLAlchemyError:
            # No table yet -- the database exists but nothing has been applied.
            return None
    if row is None:
        return None
    return str(row[0])


def _head_revision(migrations_dir: Path) -> str | None:
    """The head revision the scripts on disk declare.

    Uses Alembic's own ``ScriptDirectory`` rather than parsing version files, so
    the notion of "head" is the one ``alembic upgrade head`` uses and cannot
    drift from it. A script directory with several heads (a branch, which this
    project does not have) returns the first, because the check is "is the
    database at a head", and any single answer is more useful than a crash.
    """
    from alembic.script import ScriptDirectory

    return ScriptDirectory(str(migrations_dir)).get_current_head()


def build_readiness_check(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session] | None = None,
    migrations_dir: Path | None = None,
) -> Callable[[], Awaitable[dict[str, str]]]:
    """Build the async readiness callable for one deployment.

    Args:
        settings: The process configuration (used for ``is_sqlite`` and the URL
            when no factory is supplied).
        session_factory: The sessionmaker to share with the SQL stores, so the
            probe and the stores talk to the same engine and pool. ``None``
            (tests, or a caller that wants an isolated probe) builds one from
            settings.
        migrations_dir: The Alembic script directory. ``None`` resolves it by
            walking up to the repository root.

    Returns:
        An async callable returning ``{"database": ..., "migrations": ...}``
        where each value is ``"ok"`` or a short failure state. It never raises.
    """
    from opspilot.adapters.persistence import db

    factory = session_factory if session_factory is not None else db.session_factory(settings)
    resolved_migrations = migrations_dir if migrations_dir is not None else resolve_migrations_dir()

    async def check() -> dict[str, str]:
        database = _check_database(factory)
        if database != "ok":
            # Do not attempt the migration check when the connection already
            # failed: it would spend a second connect timeout to learn nothing.
            # The value is still named, so the body answers "which check failed"
            # for both -- and "unknown" is honest, since the migrations check
            # genuinely could not run.
            return {"database": database, "migrations": "unknown"}
        return {
            "database": database,
            "migrations": _check_migrations(factory, resolved_migrations),
        }

    return check


def _check_database(factory: sessionmaker[Session]) -> str:
    """``SELECT 1`` over a fresh connection. ``"ok"`` or a named failure."""
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return "unreachable"
    return "ok"


def _check_migrations(factory: sessionmaker[Session], migrations_dir: Path | None) -> str:
    """Compare the stamped revision to the script directory's head.

    Returns ``"ok"`` only when the two are equal. Every other outcome is named:
    ``"unknown"`` when the scripts cannot be found, ``"unmigrated"`` when the
    database has no ``alembic_version`` row, ``"behind"`` when it is stamped at
    something other than head (including ahead, which is also not "at head" and
    which this project treats as a state to look at, not to certify).
    """
    if migrations_dir is None:
        # The check cannot be made. Reporting "ok" here is the original defect in
        # miniature, so it is reported as the unknown state it is.
        return "unknown"
    try:
        head = _head_revision(migrations_dir)
    except Exception:
        # A migrations directory that cannot be parsed (a corrupt script, a
        # missing revision file) is a check state, not a 500 out of the route.
        # The broad catch is deliberate: Alembic raises a different type per
        # malformation, and the route's contract is that it never raises.
        return "unknown"
    if head is None:
        return "unknown"
    try:
        current = _current_revision(factory)
    except SQLAlchemyError:
        # The database half already reports unreachable; the migration half must
        # not raise out of the route for the same connection.
        return "unknown"
    if current is None:
        return "unmigrated"
    return "ok" if current == head else "behind"
