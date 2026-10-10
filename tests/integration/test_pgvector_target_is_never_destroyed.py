"""The two postgres-marked vector store tests cannot destroy a database.

## The defect this exists for

Two tests finished with ``Base.metadata.drop_all(engine)`` -- every table the ORM
declares, in whatever database ``OPSPILOT_DATABASE_URL`` named. Nothing checked
that the database was disposable, and neither file carried a warning. A session
pointed that variable at the stack's own ``opspilot`` database, the suite ran, and
every application table in it was deleted; the worker crash-looped with
``relation "agent_runs" does not exist`` until the schema was rebuilt by hand.

``tests/_pgvector_target.py`` is what now sits between those two tests and the
database. It carries the rule -- *a test that would destroy a database refuses to
run against one that is not provably empty* -- the two alternatives that were
rejected and why, and the argument for refusing rather than skipping. This file is
the proof that it holds, because a guard nobody has watched refuse is a guard
nobody knows works.

## What is asserted here

1. :func:`test_a_populated_target_refuses_and_loses_nothing` -- the incident,
   reproduced on a scratch database: a target holding one row makes **both**
   postgres-marked tests refuse, by name, and loses nothing.
2. :func:`test_the_shape_ci_creates_runs_and_keeps_the_schema` -- the shape the
   ``verify`` job creates (all nine tables, no rows) is admitted, both tests
   pass, and the schema survives for whatever the job runs next.
3. :func:`test_a_fresh_target_is_left_as_it_was_found` -- a target with no schema
   at all gets one created and then dropped again, and is left with no tables.
4. :func:`test_neither_pgvector_test_can_reach_a_drop_all_of_its_own` -- the
   always-on pin that runs with no Docker and no Postgres: neither module calls
   ``drop_all`` and both name the guard.

## Why the subjects are run as subprocesses

The thing under test is the *delivery path*, not the guard's function. Both tests
decide what to destroy by reading ``OPSPILOT_DATABASE_URL``, so the only way to
show a plain ``pytest`` with that variable set ending differently is to run a plain
``pytest`` with that variable set. An in-process call with a patched factory would
prove the guard can refuse; it would not prove that the test as the suite collects
it connects where the environment names.

The databases are **never the configured one**. Each test creates a database of
its own -- ``opspilot_guard_<pid>_<random>`` -- and drops it however the test
ends, so this file can be pointed at a live deployment's URL and the only thing
it will ever have done to that database is ``CREATE DATABASE`` a name that was
random a moment ago. The refusal scenario is likewise exercised on a scratch
database: running it against the real thing is the incident.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from opspilot.adapters.persistence import models
from opspilot.adapters.persistence.models import Base
from tests._pgvector_target import assert_ci_shape_is_admitted

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The two tests the guard protects, named the way ``pytest`` names them.
DIFFERENTIAL_NODE = "tests/integration/test_citations.py::test_pgvector_and_memory_stores_agree"
CONTRACT_NODE = "tests/unit/test_vector_stores.py::test_pgvector_store_satisfies_shared_contract"

#: Where the rule lives, quoted in failures so the reader is sent to the
#: argument and not only to the verdict.
GUARD_MODULE = "tests/_pgvector_target.py"

#: The two modules the guard covers. Both are inside the ruff/mypy roots, so the
#: always-on test below collects them with no database and no Docker.
COVERED_MODULES = (
    REPO_ROOT / "tests/integration/test_citations.py",
    REPO_ROOT / "tests/unit/test_vector_stores.py",
)

#: Every application table the ORM declares: what ``create_all`` makes, and what
#: the incident destroyed.
APP_TABLES = frozenset(Base.metadata.tables)

#: The table the refusal scenario puts a row in, and the first one an
#: ``AgentRun`` points at -- the one whose absence crash-looped the worker.
SEED_TABLE = "tickets"

#: A phrase from the guard's refusal. Matched against whitespace-collapsed
#: output, because pytest wraps a long message at the terminal width and a
#: phrase split across two lines would otherwise read as absent.
REFUSAL_MARKER = "refused to run against a database that holds data"


class ScratchTarget(NamedTuple):
    """A scratch database: its URL for a child process, and an engine to it."""

    url: str
    engine: Engine


def _flatten(output: str) -> str:
    """``output`` with every run of whitespace collapsed to a single space.

    A child's report is wrapped to whatever width the runner used, so an
    assertion on an exact phrase becomes a test that depends on terminal width.
    Collapsing first makes the check independent of it.
    """
    return re.sub(r"\s+", " ", output)


@contextmanager
def scratch_target() -> Iterator[ScratchTarget]:
    """Create a database this file owns, and drop it however this ends.

    The name is random and the URL is derived from it, so nothing here can name
    the configured database: ``opspilot_guard_<pid>_<hex>`` is not a name a
    deployment has. The only statements ever issued against the *configured*
    database are the ``CREATE DATABASE`` and ``DROP DATABASE`` of that name, on
    a connection in ``AUTOCOMMIT``.

    Yields:
        The scratch database's URL (for the child process) and an engine to it.

    Raises:
        pytest.skip.Exception: No Postgres is configured or reachable, or the
            role may not create databases. A missing capability is the project's
            standing reason to skip (ADR-0004); the ``verify`` job's own
            assertion that this test PASSED is what keeps the skip from reading
            as a pass.
    """
    configured = os.environ.get("OPSPILOT_DATABASE_URL", "")
    if not configured.startswith("postgresql"):
        pytest.skip("no PostgreSQL configured (set OPSPILOT_DATABASE_URL); ADR-0004")

    name = f"opspilot_guard_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    # `render_as_string(hide_password=False)`, not `str(url)`: SQLAlchemy 2.1's
    # `str(URL)` *hides* the password (`user:***@host`), and the scratch engine
    # would then authenticate to PostgreSQL with the literal four characters
    # `***` -- `FATAL: password authentication failed` on every machine that has
    # a Postgres to fail against, and a skip (so, invisible) on every machine
    # that does not. `str(make_url(...).set(...))` is the shape that reads
    # correct and is not; the visible-password render is the one that carries
    # what the child process and the scratch engine need.
    url = make_url(configured).set(database=name).render_as_string(hide_password=False)

    # `WITH (FORCE)` terminates the leftovers that would otherwise block the
    # drop -- a child killed mid-test can leave one. It is PostgreSQL 13+, which
    # is the floor this project already set (docker-compose.yml and the CI
    # service both pin pg16).
    drop_scratch = f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
    maintenance = create_engine(configured, isolation_level="AUTOCOMMIT")
    try:
        try:
            with maintenance.connect() as connection:
                connection.execute(text(drop_scratch))
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        except SQLAlchemyError as error:
            pytest.skip(
                "a scratch database could not be created, so the refusal "
                "behaviour is unproven here. Point OPSPILOT_DATABASE_URL at a "
                f"Postgres whose role may CREATE DATABASE: {error}"
            )
        engine = create_engine(url)
        try:
            yield ScratchTarget(url=url, engine=engine)
        finally:
            engine.dispose()
    finally:
        # The database goes even if the test failed: a scratch database left
        # behind is residue that becomes a surprise later.
        with maintenance.connect() as connection:
            connection.execute(text(drop_scratch))
        maintenance.dispose()


def _tables_in(engine: Engine) -> set[str]:
    """Every table in ``engine`` -- application tables, ``alembic_version``, any."""
    return set(inspect(engine).get_table_names())


def _present_app_tables(engine: Engine) -> set[str]:
    """The application tables that exist in ``engine``."""
    return set(APP_TABLES) & _tables_in(engine)


def _row_count(engine: Engine, table: str) -> int:
    """How many rows ``table`` holds.

    Through ``Base.metadata`` rather than an interpolated string: the table is
    named by the ORM's own object, so there is no query text to build and no
    S608 exception to write.
    """
    statement = select(func.count()).select_from(Base.metadata.tables[table])
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def _create_the_schema_as_the_migration_leaves_it(engine: Engine) -> None:
    """All nine application tables, as ``alembic upgrade head`` leaves them.

    ``create_all`` rather than a second ``alembic upgrade head``: the same table
    set in a fraction of the time, and this file is not testing the migration --
    ``test_migration_schema.py`` does that. The vector extension is created first
    because ``create_all`` renders the ``vector(1536)`` column natively and the
    type does not exist until the extension does.
    """
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def _seed_one_row(engine: Engine) -> None:
    """One row in one table: the state the guard refuses.

    Written through the ORM so the column defaults are the model's own, and in
    ``tickets`` specifically -- it is the table the incident's crash loop
    complained about.
    """
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(
            models.Ticket(
                subject="DO NOT DELETE",
                body="a row that must survive a test that is about to refuse",
                customer_email="someone@example.com",
            )
        )
        session.commit()


def _run(node: str, url: str) -> subprocess.CompletedProcess[str]:
    """Run one test node with the URL variables pointing at ``url``.

    Both are set because the two tests read the database through different
    layers -- ``Settings`` for one, the environment directly for the other -- and
    a child that inherited a different value would be running a different test.
    ``no:cacheprovider`` because a child writing ``.pytest_cache/lastfailed``
    would corrupt the run that spawned it.
    """
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "OPSPILOT_DATABASE_URL": url,
        "OPSPILOT_ENV": "test",
    }
    return subprocess.run(  # noqa: S603 - [sys.executable, "-m", "pytest", <literal args>]
        [sys.executable, "-m", "pytest", node, "-rA", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=300,
    )


@pytest.mark.postgres
def test_a_populated_target_refuses_and_loses_nothing() -> None:
    """A target holding a row makes both tests refuse, and keeps the row.

    This is the incident, reproduced: the same environment that once deleted
    every application table in a live database. Each child is asserted to have
    **failed** -- a refusal is a failure, deliberately, because a skip would read
    as "nothing was checked" (ADR-0004) -- and to have shown the guard's own
    marker, so a connection error or a crash cannot pass for a refusal.

    What is asserted afterwards matters more: every application table is still
    there, and the row that triggered the refusal is still in it. Before the
    guard, this scenario ended with an empty database and a crash-looping
    worker.
    """
    with scratch_target() as scratch:
        _create_the_schema_as_the_migration_leaves_it(scratch.engine)
        _seed_one_row(scratch.engine)
        assert _row_count(scratch.engine, SEED_TABLE) == 1

        for node in (DIFFERENTIAL_NODE, CONTRACT_NODE):
            result = _run(node, scratch.url)
            report = _flatten(result.stdout + result.stderr)
            assert result.returncode != 0, (
                f"{node} PASSED against a database holding a row in "
                f"`{SEED_TABLE}`. It drops every application table in the target "
                "on the way out, so this is the incident and not a success -- "
                f"the guard in {GUARD_MODULE} is what is supposed to stop "
                f"it.\n\n{report}"
            )
            assert REFUSAL_MARKER in report, (
                f"{node} failed, but not with the guard's refusal, so it failed "
                f"for some other reason. Expected {REFUSAL_MARKER!r}.\n\n{report}"
            )
            assert SEED_TABLE in report, (
                f"{node} failed without naming the table that held the row, so "
                "the refusal did not say what to empty.\n\n" + report
            )

        assert _present_app_tables(scratch.engine) == set(APP_TABLES), (
            "the populated database did not keep all of its tables after these "
            f"tests refused. Found: {sorted(_tables_in(scratch.engine))}"
        )
        assert _row_count(scratch.engine, SEED_TABLE) == 1, (
            "the row that triggered the refusal is gone. This is the exact "
            "outcome the guard exists to prevent."
        )


@pytest.mark.postgres
def test_the_shape_ci_creates_runs_and_keeps_the_schema() -> None:
    """The CI shape is admitted: the tests pass and leave the schema standing.

    The ``verify`` job runs ``alembic upgrade head`` before these tests, so its
    service container has all nine tables and none of them holds a row. That is
    the state this builds, and asserting on it is what makes "the guard does not
    break CI" a property rather than an argument: ``assert_ci_shape_is_admitted``
    is the guard's own statement about it, and running both tests is the rest of
    it -- they pass, and the schema is still there when the job's next step
    starts.
    """
    with scratch_target() as scratch:
        _create_the_schema_as_the_migration_leaves_it(scratch.engine)
        assert_ci_shape_is_admitted(scratch.engine)

        for node in (DIFFERENTIAL_NODE, CONTRACT_NODE):
            result = _run(node, scratch.url)
            report = _flatten(result.stdout + result.stderr)
            assert result.returncode == 0 and f"PASSED {node}" in report, (
                f"{node} did not pass against a database with the migrated "
                "schema and no rows in it -- which is exactly the state the "
                "verify job puts it in, so this is the guard refusing where CI "
                "needs it to run.\n\n" + report
            )
            assert "SKIPPED" not in report, (
                f"{node} skipped instead of passing. A skip here is a green that "
                "means nothing was checked (ADR-0004).\n\n" + report
            )

        assert _present_app_tables(scratch.engine) == set(APP_TABLES), (
            "the schema did not survive the tests. On CI this would break every "
            "step that runs after them."
        )
        empty = {table: _row_count(scratch.engine, table) for table in APP_TABLES}
        assert not any(empty.values()), (
            f"a test left rows behind in a database it does not own: "
            f"{ {k: v for k, v in empty.items() if v} }"
        )


@pytest.mark.postgres
def test_a_fresh_target_is_left_as_it_was_found() -> None:
    """A target with no schema at all is created and destroyed again.

    The other branch of the guard's teardown, and the one a developer without a
    migrated database lands in: a URL naming a database nothing has touched yet.
    It has to come back out with the tables it went in with -- none -- because
    "leave it as you found it" is the whole contract. A schema the suite created
    and did not drop would also leave the next run finding tables nobody told it
    about, which is the branch the refusal is measured against.
    """
    with scratch_target() as scratch:
        assert _present_app_tables(scratch.engine) == set(), (
            "the scratch database was not empty to begin with, so this is not "
            "the branch it claims to test."
        )

        result = _run(DIFFERENTIAL_NODE, scratch.url)
        report = _flatten(result.stdout + result.stderr)
        assert result.returncode == 0 and f"PASSED {DIFFERENTIAL_NODE}" in report, (
            "the differential test did not pass against a database with no "
            "schema in it, which is what a fresh local URL looks like.\n\n" + report
        )
        assert _present_app_tables(scratch.engine) == set(), (
            "the test created the schema it needed and left it behind. The "
            "guard's fresh-database branch must drop exactly what it created: "
            f"{sorted(_tables_in(scratch.engine))}"
        )


def _drop_all_call_lines(tree: ast.Module) -> list[int]:
    """Line numbers of ``<something>.drop_all(...)`` calls, in source order."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "drop_all"
    ]


def _names_the_guard(tree: ast.Module) -> bool:
    """Does this module reference ``guarded_pg_schema`` anywhere?"""
    return any(
        isinstance(node, ast.Name) and node.id == "guarded_pg_schema" for node in ast.walk(tree)
    )


def test_neither_pgvector_test_can_reach_a_drop_all_of_its_own() -> None:
    """Neither module calls ``drop_all``, and both name the guard. Always on.

    The one check here that needs no database, so the property is still verified
    on the machine where the incident happened -- with no Docker running, which
    is the state the two postgres tests are *skipped* in. Reading the AST rather
    than the text matters: both modules now explain the old
    ``Base.metadata.drop_all`` teardown in their docstrings, and a text search
    would match the record of the fix rather than the fix.
    """
    problems: list[str] = []
    for path in COVERED_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        drop_calls = _drop_all_call_lines(tree)
        if drop_calls:
            problems.append(
                f"{relative}:{drop_calls[0]}: calls drop_all. It drops every "
                "table the ORM declares in whatever database "
                f"OPSPILOT_DATABASE_URL names -- the incident. Route the writes "
                f"through {GUARD_MODULE} instead."
            )
        if not _names_the_guard(tree):
            problems.append(
                f"{relative}: never names `guarded_pg_schema`, so its Postgres "
                "writes are no longer guarded."
            )

    assert not problems, (
        "the postgres-marked tests can reach a database without the guard:\n  "
        + "\n  ".join(problems)
    )
