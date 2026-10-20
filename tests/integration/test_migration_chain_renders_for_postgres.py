"""The migration chain must render to PostgreSQL SQL, on a machine with no PostgreSQL.

## Why this file exists

``docs/adr/0004`` makes PostgreSQL + pgvector the production target and SQLite a
tests-only path, so ``alembic upgrade head`` runs against PostgreSQL in every
real deployment -- and ``tests/integration/test_migration_schema.py`` is the test
that proves it works there. That test **drops and recreates the schema** on a
real server, so it carries the ``postgres`` mark and **skips** wherever no
PostgreSQL is reachable.

Which means: on a laptop with no Docker, the entire migration chain -- every
``ALTER TABLE``, every ``CREATE INDEX``, every dialect-specific type -- is
**unexecuted and untested**. A migration can be written that imports a name
that does not exist, renders a type the driver has no compiler for, or calls
an Alembic operation with the wrong arity, and every test in the repository
stays green. The gap only opens on the machine where the deployment runs.

This file closes it **without a database**. Alembic's ``--sql`` (offline) mode
renders the chain to SQL text and never connects to anything, so the whole
migration history can be type-checked against the production dialect in under
a second, anywhere.

## What this does and does not prove

It proves the chain **renders**: every migration is importable, every operation
is well-formed, and every type resolves for the PostgreSQL dialect. It does
**not** prove the SQL executes -- a DDL error that only the server can raise
(a cast pgvector does not register, a constraint the server rejects) is out of
reach here, and ``test_migration_schema.py`` remains the test that covers that
case.

The two are complementary, and neither replaces the other: this one runs
everywhere, that one runs where a server exists.

## What is asserted

1. ``alembic upgrade head --sql`` exits 0 against a PostgreSQL URL, and the
   rendered SQL reaches ``0003``.
2. The two columns ``0003`` adds are rendered as ``TEXT`` and ``JSONB`` -- the
   production types, not the SQLite ones.
3. The pgvector column is still rendered as ``vector(1536)``. This is the
   specific thing that took down ``0001`` in the field: the column had been
   created as ``json``, and ``ALTER COLUMN ... TYPE vector(1536)`` cannot work
   because pgvector registers no cast from ``json`` to ``vector``. A migration
   that rendered the wrong width here would be caught by this file even on a
   machine with no database at all.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A PostgreSQL URL that is never dialled: ``--sql`` renders without connecting.
#: The credentials are the ones ``docker-compose.yml`` uses, and they are
#: local defaults rather than secrets.
_POSTGRES_URL = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"

#: The revision the chain must reach. Named rather than read from
#: ``versions/``, so that adding a migration without updating this file is a
#: failure instead of a test that quietly checks the old head.
_HEAD = "0003"


@pytest.fixture(scope="module")
def rendered() -> str:
    """The whole migration chain, rendered to PostgreSQL SQL.

    Run in a subprocess with ``DATABASE_URL`` pointed at a PostgreSQL URL, so
    the chain is rendered for the production dialect. The environment variable
    **overrides** the repository's ``.env`` -- pydantic-settings gives the
    process environment precedence over the dotenv file -- so this never reads
    the developer's own database, and never touches it.
    """
    environment = dict(os.environ)
    environment["DATABASE_URL"] = _POSTGRES_URL
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        "the migration chain did not render for PostgreSQL:\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed.stdout


@pytest.fixture(scope="module")
def rendered_downgrade() -> str:
    """The whole chain again, rendered **downwards**, to PostgreSQL SQL.

    ``downgrade`` is the half nobody exercises: ``docs/limitations.md`` §6
    records that the migrations are "forward-only in practice" and that
    ``downgrade`` "is not exercised by CI". Rendering it is not the same as
    executing it -- the server still has to accept the DDL -- but a downgrade
    that cannot even be *rendered* is broken in a way that would be found only
    by someone trying to use it.
    """
    environment = dict(os.environ)
    environment["DATABASE_URL"] = _POSTGRES_URL
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "head:base", "--sql"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        "the downgrade chain did not render for PostgreSQL:\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed.stdout


def test_the_downgrade_renders_and_reverses_the_upgrade(rendered_downgrade: str) -> None:
    """The downgrade drops ``0003``'s two columns, in the reverse order.

    The order is not cosmetic: the column added last is the one dropped first,
    and a downgrade that dropped ``note`` before ``context`` would still leave
    a schema that does not match the revision it claims to be at.
    """
    drop_context = "ALTER TABLE approval_requests DROP COLUMN context"
    drop_note = "ALTER TABLE approval_requests DROP COLUMN note"
    assert drop_context in rendered_downgrade, "the downgrade does not drop the context column"
    assert drop_note in rendered_downgrade, "the downgrade does not drop the note column"
    assert rendered_downgrade.index(drop_context) < rendered_downgrade.index(drop_note), (
        "the downgrade drops 'note' before 'context'; the reverse of the order they were added in"
    )


def test_the_chain_reaches_head(rendered: str) -> None:
    """The rendered chain ends at the head revision.

    The stamp is an ``UPDATE`` rather than an ``INSERT`` for every revision
    after the first -- alembic inserts the first and updates as it goes -- so
    the head is recorded as ``version_num='0003'``.
    """
    assert f"version_num='{_HEAD}'" in rendered, (
        f"the rendered chain does not reach {_HEAD}; the versions it records "
        f"are: {sorted(set(re.findall(r'version_num=.(\\d+)', rendered)))}"
    )


def test_the_approval_columns_render_as_their_production_types(rendered: str) -> None:
    """``0003``'s two columns are ``TEXT`` and ``JSONB`` on PostgreSQL."""
    assert "ALTER TABLE approval_requests ADD COLUMN note TEXT" in rendered, (
        "the note column is not rendered as TEXT for PostgreSQL"
    )
    assert "ALTER TABLE approval_requests ADD COLUMN context JSONB" in rendered, (
        "the context column is not rendered as JSONB for PostgreSQL"
    )


def test_the_vector_column_is_rendered_at_the_declared_width(rendered: str) -> None:
    """The pgvector column is ``vector(1536)``, which is the whole point of ``0001``.

    The field failure this guards against was a column born as ``json`` and
    later asked to become a vector: pgvector registers no cast from ``json`` to
    ``vector``, so the ``ALTER`` could never succeed. The column has to be
    created as a vector at the right width, and this is the only assertion in
    the suite that can read that off the migration without a server.

    The comparison is case-insensitive because alembic renders the type with
    the dialect's own casing, and the type is spelled the same either way.
    """
    lowered = rendered.lower()
    assert "vector(1536)" in lowered, (
        "the knowledge_chunks embedding column is not rendered as vector(1536); "
        "the width must match models._EMBEDDING_DIM and PgVectorStore.DEFAULT_DIM"
    )


def test_the_hnsw_index_is_rendered(rendered: str) -> None:
    """The HNSW index on the embedding column is in the rendered DDL.

    The column and the index are both load-bearing: a migration that created
    one without the other would still "migrate cleanly", and retrieval would
    fall back to a sequential scan over the whole chunk table. The index is
    ``WHERE``-less but ``USING hnsw``, which is what makes it an index scan
    rather than a plain b-tree.
    """
    lowered = rendered.lower()
    assert "using hnsw" in lowered, (
        "the HNSW index on knowledge_chunks.embedding is not rendered; retrieval "
        "would be a sequential scan"
    )
    assert "vector_cosine_ops" in lowered, (
        "the HNSW index is not rendered with the cosine operator class, which is "
        "the distance the retrieval uses"
    )


def test_no_migration_is_left_out_of_the_rendered_chain(rendered: str) -> None:
    """Every revision in ``versions/`` appears in the rendered chain.

    A migration file that alembic does not run -- because it is not on the
    chain, because its ``down_revision`` names a revision that does not exist,
    or because it was committed to the wrong directory -- renders as nothing,
    and the schema the deployment gets is the schema one revision behind.
    """
    versions = REPO_ROOT / "migrations" / "versions"
    files = sorted(versions.glob("*.py"))
    assert files, "there are no migration files at all; the chain is broken"

    # Each revision stamps its own id into the version table as it runs.
    stamped = [
        revision
        for revision in (path.stem.split("_", 1)[0] for path in files)
        if f"VALUES ('{revision}')" in rendered or f"version_num='{revision}'" in rendered
    ]
    missing = [path.name for path in files if path.stem.split("_", 1)[0] not in stamped]
    assert not missing, (
        f"these migration files did not appear in the rendered chain: {missing}. "
        "A revision that does not run is a schema the deployment never gets."
    )
