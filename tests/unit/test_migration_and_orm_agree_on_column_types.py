"""The ORM and the migration chain must declare the same column types.

## Why this file exists

``tickets.external_id`` was declared ``String()`` in the ORM
(``adapters/persistence/models.py``) and ``sa.Text()`` in the migration
(``0001_initial_schema.py``). Those are different types: ``String()`` with no
length renders as ``VARCHAR`` and ``sa.Text()`` renders as ``TEXT``.

The consequence was not a comment that had gone stale -- it was two different
databases. ``Base.metadata.create_all`` (what the SQLite test path uses)
built the column as ``VARCHAR``; ``alembic upgrade head`` (what a deployment
runs) built it as ``TEXT``. Two databases with the same name and the same
tables, differing on a column's type. The ORM and the migration are the two
sides of one schema, and this file is what keeps them the same schema.

## Why it compares the *declared* types and not the SQLite schema

SQLite would not catch this. Every SQLite column has a type *affinity*, and
both ``VARCHAR`` and ``TEXT`` map to the same ``TEXT`` affinity -- a SQLite
database built from the ORM and one built from the migration are byte-identical
in the column type. So this file replays the migration chain's ``create_table``
and ``add_column`` calls and compares the **declared** type of every column
against the ORM's.

That is also why the comparison is over the whole chain and not just
``0001``: ``0002`` adds ``agent_runs.claimed_until``/``claimed_by`` and
``0003`` adds ``approval_requests.note``/``context``, and a comparison against
``0001`` alone would report those four as "missing from the migration" --
which is what a reader would want to know if a column really had gone missing.

## What it asserts

* every column the ORM declares exists in the migrated schema, and vice versa;
* every column's declared type is the same on both sides -- ``TEXT`` is not
  ``VARCHAR``, and ``JSON`` is not ``TEXT``;
* every column's nullability is the same on both sides.

The types are compared by their **rendered DDL** (``VARCHAR``, ``TEXT``,
``JSON``, ``UUID``, ``DATETIME``...), because that is the thing a database
ends up holding, and because it is the difference that was invisible on
SQLite.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine.interfaces import Dialect

from opspilot.adapters.persistence.models import Base

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

#: The revision chain, in order. Each is a module whose ``upgrade()`` declares
#: the schema as of that revision.
REVISIONS = (
    "0001_initial_schema",
    "0002_claim_lease",
    "0003_approval_note_and_context",
)


class _CapturingOperations:
    """A stand-in for Alembic's ``op`` that records the schema it is given.

    Every method the revisions call is recorded: ``create_table`` builds a
    table in a shared ``MetaData`` and ``add_column`` appends to one. Anything
    else -- ``create_index``, ``execute``, ``get_bind`` -- is a no-op, because
    the question this file asks is about *columns*.
    """

    def __init__(self) -> None:
        self.metadata = MetaData()
        self.tables: dict[str, Table] = {}

    def create_table(self, name: str, *columns: object, **_: object) -> None:
        self.tables[name] = Table(name, self.metadata, *columns)  # type: ignore[arg-type]

    def add_column(self, table_name: str, column: object, **_: object) -> None:
        self.tables[table_name].append_column(column)  # type: ignore[arg-type]

    def get_bind(self) -> object:
        return self

    @property
    def dialect(self) -> Dialect:
        return sqlite.dialect()

    def __getattr__(self, _name: str) -> object:
        return lambda *args, **kwargs: None


def _migrated_schema() -> dict[str, Table]:
    """Replay the migration chain and return the tables it declares."""
    operations = _CapturingOperations()
    for revision in REVISIONS:
        path = MIGRATIONS / f"{revision}.py"
        spec = importlib.util.spec_from_file_location(f"opspilot_{revision}", path)
        assert spec is not None and spec.loader is not None, f"cannot import {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = operations  # type: ignore[attr-defined]
        module.upgrade()  # type: ignore[attr-defined]
    return operations.tables


def _rendered(type_: object) -> str:
    """The DDL a column type renders to on SQLite."""
    return str(type_.compile(sqlite.dialect()))  # type: ignore[attr-defined]


def test_every_orm_column_exists_in_the_migrated_schema() -> None:
    """The ORM declares no column the migrations do not create."""
    migrated = _migrated_schema()

    missing = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name not in migrated[table.name].columns
    ]

    assert not missing, (
        f"the ORM declares columns the migration chain does not create: {missing}. "
        "A database built by create_all and a database built by alembic upgrade "
        "would have different shapes."
    )


def test_the_migrations_declare_no_column_the_orm_does_not() -> None:
    """The migrations create no column the ORM does not declare."""
    migrated = _migrated_schema()

    extra = [
        f"{name}.{column.name}"
        for name, table in migrated.items()
        for column in table.columns
        if column.name not in Base.metadata.tables[name].columns
    ]

    assert not extra, (
        f"the migration chain creates columns the ORM does not declare: {extra}. "
        "Those columns would be invisible to every query the application makes."
    )


def test_every_column_has_the_same_type_on_both_sides() -> None:
    """Every column's declared type is the same in the ORM and the migration.

    This is the assertion that catches ``tickets.external_id``: the ORM said
    ``String()`` (``VARCHAR``) and the migration said ``sa.Text()`` (``TEXT``).
    SQLite would not have caught it -- both have ``TEXT`` affinity there --
    which is why the comparison is on the declared type.
    """
    migrated = _migrated_schema()

    mismatched = [
        f"{name}.{column.name}: ORM={_rendered(column.type)} "
        f"migration={_rendered(migrated[name].columns[column.name].type)}"
        for name, table in Base.metadata.tables.items()
        for column in table.columns
        if _rendered(column.type)
        != _rendered(migrated[name].columns[column.name].type)
    ]

    assert not mismatched, (
        "the ORM and the migration chain declare different types for the same "
        f"column: {mismatched}. The migration is the schema's authority -- a "
        "database built by create_all and one built by alembic upgrade would "
        "otherwise differ."
    )


def test_every_column_has_the_same_nullability_on_both_sides() -> None:
    """Every column is nullable on one side exactly when it is on the other."""
    migrated = _migrated_schema()

    mismatched = [
        f"{name}.{column.name}: ORM nullable={column.nullable} "
        f"migration nullable={migrated[name].columns[column.name].nullable}"
        for name, table in Base.metadata.tables.items()
        for column in table.columns
        if column.nullable != migrated[name].columns[column.name].nullable
    ]

    assert not mismatched, (
        f"the ORM and the migration chain disagree on nullability: {mismatched}."
    )


@pytest.mark.parametrize("column_name", ["external_id"])
def test_the_column_the_two_sides_disagreed_on_is_text_on_both(
    column_name: str,
) -> None:
    """``tickets.external_id`` is ``TEXT`` on both sides -- the drift that started this.

    Named explicitly so the fix is legible in the history: the ORM said
    ``String()``, the migration said ``sa.Text()``, and this file is the
    guard that keeps them one type.
    """
    migrated = _migrated_schema()

    orm_type = Base.metadata.tables["tickets"].columns[column_name].type
    migration_type = migrated["tickets"].columns[column_name].type

    assert _rendered(orm_type) == "TEXT", (
        f"the ORM declares tickets.{column_name} as {_rendered(orm_type)}; the "
        "migration declares it as TEXT and the migration is the authority."
    )
    assert _rendered(migration_type) == "TEXT"
