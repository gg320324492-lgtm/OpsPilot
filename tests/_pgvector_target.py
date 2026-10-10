"""A ``postgres``-marked test may not destroy a database that holds anything.

## The defect this exists for

Two tests connect to whatever ``OPSPILOT_DATABASE_URL`` names and destroy it:

* ``tests/integration/test_citations.py::test_pgvector_and_memory_stores_agree``
  finished with ``Base.metadata.drop_all(engine)``;
* ``tests/unit/test_vector_stores.py::test_pgvector_store_satisfies_shared_contract``
  did the same.

``Base.metadata.drop_all`` drops **every** table the ORM declares --
``tickets``, ``agent_runs``, ``knowledge_documents``, ``citations`` and the rest
-- in whatever database the URL names. There was no check that the URL named a
throwaway database, and neither file carried a warning either
(``tests/integration/test_migration_schema.py`` at least documents the hazard in
its module docstring). A session pointed that variable at the stack's own
``opspilot`` database, ran the suite, and deleted every application table in it;
the worker then crash-looped with ``relation "agent_runs" does not exist`` until
the schema was rebuilt by hand. The data went with it.

So the rule is not "be careful". It is: **a test that would destroy a database
refuses to run against one that is not provably empty**, and it destroys only
what it created.

## The refusal condition

Every table in ``Base.metadata`` that already exists in the target must hold
**zero rows**. If any holds a row, the test fails -- loudly, naming the database,
the table, and the two supported ways to continue -- before touching anything.

Two alternatives were considered and rejected:

* *"Refuse if any table exists."* It reads stricter and is wrong here: the CI
  ``verify`` job runs ``alembic upgrade head`` **before** these tests, so the
  service container always has all nine tables present when the first one runs.
  A table-existence guard would turn every CI run red on its first postgres test.
* *"Refuse unless the database name looks temporary."* Names do not discriminate:
  CI's disposable container is called ``opspilot``, the same as the database
  that must not be destroyed. The heuristic would reject the one database it is
  safe on and wave through the one that matters.

Rows, not tables, are also the right axis because rows are what cannot be
restored. Today's recovery rebuilt the schema with a ``DROP SCHEMA ... CASCADE``
plus ``alembic upgrade head`` and lost only what happened to be empty; had there
been ingested documents or finished runs in that database, no procedure would
have brought them back.

## What "refuse" means here

``pytest.fail``, not ``pytest.skip``. The asymmetry decides it: a false refusal
destroys nothing, costs one command to resolve, and prints its own remedy; a
false *acceptance* destroys data that has no remedy at all. A skip would also be
the exact failure ADR-0004 is about -- a green result that means "nothing was
checked" -- and it would hide the misconfiguration instead of naming it. This is
a policy refusal caused by configuration, not a missing dependency, and the
project's standing answer to a missing dependency (``pytest.skip``) does not
apply.

## What the teardown is allowed to do

The guard records what it found before it changed anything, and the teardown
inverts exactly that:

* **The schema did not exist** (a genuinely fresh database): the test creates it,
  so the test owns it and drops all of it again. The database is left exactly as
  it was found -- no tables.
* **The schema already existed** (CI's migrated container, or a developer's own
  migrated database): the test creates nothing, so it drops nothing. Instead it
  deletes the rows it inserted, in reverse dependency order. That deletion is
  only sound because of the emptiness proof taken on entry -- and leaving the
  schema in place is what lets the rest of the CI ``verify`` job keep using it
  after these tests have run.

Either way the target is left with the tables it had and none of the rows. That
is the property ``tests/integration/test_pgvector_target_is_never_destroyed.py``
asserts, against a scratch database, by running the real tests and looking at
what survived.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine, Table, inspect, select, text

from opspilot.adapters.persistence.models import Base


def _database_label(engine: Engine) -> str:
    """Where this points, in a form that is safe to print and safe to act on.

    ``render_as_string(hide_password=True)`` because a refusal quoting the URL
    back would otherwise print a password into CI logs; and the *database name*
    is spelled out because that is the part an operator has to change.
    """
    return engine.url.render_as_string(hide_password=True)


def _app_tables_in(engine: Engine) -> tuple[list[Table], set[str]]:
    """The application's tables that exist in ``engine``, and every table name.

    In foreign-key dependency order, which is creation order -- so the reverse of
    it is the only order in which rows can be deleted without a parent row
    blocking its child.

    Split out from the refusal because the same question is asked twice: once to
    prove emptiness before running, and once by
    :func:`assert_ci_shape_is_admitted`, which exists so that "would this guard
    break CI?" is answered by a test instead of by an argument in a comment.
    """
    existing = set(inspect(engine).get_table_names())
    present = [table for table in Base.metadata.sorted_tables if table.name in existing]
    return present, existing


def _tables_holding_rows(engine: Engine, tables: Sequence[Table]) -> list[str]:
    """Names of ``tables`` that hold at least one row.

    ``select(table).limit(1)`` rather than ``count(*)``: the question is whether
    a table is empty, and counting the rows of a populated table is a scan the
    guard does not need to pay for. Only ever called over tables that exist, so
    the query cannot fail on a database whose schema was never built.
    """
    populated: list[str] = []
    with engine.connect() as connection:
        for table in tables:
            if connection.execute(select(table).limit(1)).first() is not None:
                populated.append(table.name)
    return populated


def _refusal_message(engine: Engine, owner: str, populated: Sequence[str], total: int) -> str:
    """The refusal, carrying everything an operator needs to act on.

    Written to *be* the remedy: what refused, what would have been destroyed, and
    the two things that make it run. A refusal that does not say what to do
    instead is a refusal that gets switched off on the next bad day.
    """
    names = "\n".join(f"    {name}" for name in populated)
    return (
        f"{owner} refused to run against a database that holds data.\n\n"
        f"The target has rows in {len(populated)} of its {total} application tables:\n"
        f"{names}\n\n"
        "Refusing to run: this test writes to that database and, on the way out, "
        "either drops the schema or deletes every row in it. That is safe on a "
        "database nobody has put anything into, and is the end of an ingested "
        "corpus otherwise.\n\n"
        f"Database: {_database_label(engine)}\n\n"
        "Either of these makes it run:\n"
        "  1. Point OPSPILOT_DATABASE_URL at a disposable database -- one with "
        "no rows in it, migrated or not. `createdb opspilot_scratch` is enough.\n"
        "  2. Empty this one, if it really is scratch: clear every row from each "
        "table named above.\n\n"
        "What is not a fix, and has already cost this project a stack: assuming "
        "the URL points somewhere disposable because the suite normally runs with "
        "no Postgres at all. Without one these tests skip, so they never had to "
        "be right about this.\n\n"
        "See tests/_pgvector_target.py for the rule, and why the signal is rows "
        "rather than tables."
    )


@contextmanager
def guarded_pg_schema(engine: Engine, *, owner: str) -> Iterator[None]:
    """Yield with the ORM schema in place, guaranteeing what the teardown undoes.

    Args:
        engine: A connection to the database ``OPSPILOT_DATABASE_URL`` named.
            Nothing is written to it unless the emptiness check passes.
        owner: The test asking for this, quoted verbatim in the refusal so the
            reader learns which test refused rather than only which database was
            blamed.

    Yields:
        ``None``, with ``Base.metadata`` present and every application table
        proven empty on entry.

    Raises:
        pytest.fail.Exception: The target holds rows. Raised *before* anything is
            created, inserted or dropped.
    """
    present, existing = _app_tables_in(engine)
    populated = _tables_holding_rows(engine, present)
    if populated:
        pytest.fail(_refusal_message(engine, owner, populated, len(present)))

    owned = [table for table in Base.metadata.sorted_tables if table.name not in existing]
    try:
        if owned:
            # ``create_all`` renders the ``vector(1536)`` column natively, and
            # the ``vector`` type does not exist until the extension does -- so
            # the extension has to precede the schema, and that makes it this
            # guard's job rather than the callers'. Both callers used to create
            # the extension themselves, *inside* the block, where it ran after
            # ``create_all``: a no-op against an already-migrated database
            # (where ``alembic upgrade head`` had created both) and an
            # immediate ``type "vector" does not exist`` against a fresh one.
            # It is conditional because on a migrated database the extension is
            # already there, and because creating one is a write to a database
            # this function has just proved it must not write to.
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(engine)
        yield
    finally:
        if owned:
            # Everything this run brought into existence, and nothing else.
            Base.metadata.drop_all(engine, tables=owned)
        else:
            # The schema was already here, so it stays: dropping it would break
            # whatever else is using this database, which in CI is the rest of
            # the verify job. What goes instead is the rows this run wrote --
            # which, and only because the check above proved every table empty
            # on entry, are provably exactly those rows. Reverse metadata order
            # is dependency order: a child before its parent, so no foreign key
            # blocks the delete.
            for table in reversed(present):
                with engine.begin() as connection:
                    connection.execute(table.delete())


def assert_ci_shape_is_admitted(engine: Engine) -> None:
    """The CI environment satisfies :func:`guarded_pg_schema` with no opt-in.

    Exists so that "would this guard break CI?" is answered by a test rather than
    by an argument. The ``verify`` job creates the service container, waits for
    it to be healthy, runs ``alembic upgrade head``, and only then runs these
    tests -- so the target has all nine application tables present and every one
    of them empty. That state is admitted. A guard requiring an *absent* schema,
    or a name that looks temporary, would refuse there: CI's container is called
    ``opspilot``, the same name as the database that must not be destroyed.
    """
    present, _ = _app_tables_in(engine)
    assert present, (
        "the target has none of the application's tables, so it is not the state "
        "the CI verify job creates and this assertion is not testing what it "
        "claims to test"
    )
    assert not _tables_holding_rows(engine, present), (
        "the target holds rows. CI's container never does at this point: nothing "
        "writes to it between the migration step and the postgres-marked tests. "
        "If that ever changed, the guard above would refuse and the job would say "
        "so -- which is this assertion earning its keep."
    )
