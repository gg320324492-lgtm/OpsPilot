"""``alembic upgrade head`` must produce the schema the ORM actually expects.

## Why this file exists

This repository has a documented, repeated failure mode: a capability that is
described and tested on SQLite while the Postgres path was never executed
(``docs/progress.md``, and ADR-0004 which names the risk). ``0001_initial_schema``
was exactly that case. It ran green on SQLite for the whole of M0-M9 -- the
SQLite column is an inert ``JSON`` and the Postgres-only branches are skipped --
and it had **never successfully applied to a real Postgres**, because

    ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1536)
    ERROR:  column "embedding" cannot be cast automatically to type vector
    HINT:  You might need to specify "USING embedding::vector(1536)".

took down ``docker compose up migrate``. The hint is a red herring: pgvector
registers *no* cast from ``json`` to ``vector``, so the ``USING`` form it
suggests fails identically. A column created as ``json`` can never become a
vector; it has to be born one. That is only knowable by running the migration,
which is what this file does.

A suite that is green while the deployment path is broken is worse than no
suite, because it is trusted. So this test asserts the *migration* output, not
a schema built by ``Base.metadata.create_all`` -- the latter would pass
perfectly happily with the broken migration in place, since it renders the
column straight from the model's ``TypeDecorator``. Only ``alembic upgrade head``
exercises the DDL the migration actually writes.

## What is asserted

- **The migration runs.** ``alembic upgrade head`` against a real Postgres exits
  0 and reaches head.
- **The column is the vector column, at the right width** -- read back from
  ``pg_attribute``, which reports ``vector(1536)`` including the typmod. A bare
  ``information_schema`` ``data_type`` says only ``USER-DEFINED`` and hides the
  dimension, which is precisely the thing that has to be checked.
- **The HNSW index exists**, because it is what makes retrieval an index scan
  rather than a sequential scan. The column and the index are both load-bearing;
  a migration that created one without the other would still "migrate cleanly".
- **The three width constants agree** -- the migration's frozen literal,
  ``models._EMBEDDING_DIM`` (which reads ``settings.embedding_dim``) and
  ``PgVectorStore.DEFAULT_DIM``. This runs *without* Postgres, so a drift
  between the migration and the ORM is caught on a laptop with no Docker, not
  only on a machine that happens to have a database.

The Postgres-dependent tests carry ``@pytest.mark.postgres`` and skip where no
Postgres is reachable, matching the pattern in ``test_citations.py``. They pass
as *skipped*, never as *passed*; ``-ra`` reports the count so a green run is
not read as full coverage.

**These tests drop and recreate the schema**, exactly as ``test_citations.py``
already does with ``Base.metadata.drop_all``. Point them at a disposable database
only -- which is what the CI ``verify`` job does with its service container. Do
not run them against a deployment.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The revision under test. A future schema change gets a new revision, and this
# test follows it there -- it is about *the migration*, not about one file name.
_HEAD = "0001"


def _postgres_url() -> str | None:
    """The configured Postgres URL, or ``None`` where there is no Postgres.

    ``test_citations.py`` reads ``OPSPILOT_DATABASE_URL`` for this; the same
    variable is used so one environment setting configures both.
    """
    url = os.environ.get("OPSPILOT_DATABASE_URL", "")
    return url if url.startswith("postgresql") else None


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """A migrated Postgres engine, or a skip where there is no Postgres.

    The migration runs against a real database through the real Alembic
    configuration -- the same ``alembic.ini`` and ``env.py`` ``docker compose up
    migrate`` uses -- because the defect under test lives in the DDL that
    produces, and no in-process shortcut reproduces it.
    """
    url = _postgres_url()
    if url is None:
        pytest.skip("no PostgreSQL configured (set OPSPILOT_DATABASE_URL); ADR-0004")

    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # `migrations/env.py` overwrites sqlalchemy.url from settings, which reads
    # this variable; set it so the migration cannot fall back to a default.
    os.environ["DATABASE_URL"] = url

    engine = create_engine(url)
    try:
        # Migrate onto a clean slate this fixture owns, rather than inheriting
        # whatever the previous test left behind. `test_citations.py` finishes
        # with `Base.metadata.drop_all`, which removes the tables but leaves
        # `alembic_version` claiming revision 0001 -- a state where `upgrade
        # head` is a silent no-op and `downgrade` fails on indexes that are
        # already gone. Dropping the tables directly avoids depending on that
        # bookkeeping at all, so this test is unaffected by collection order.
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            connection.execute(text("GRANT ALL ON SCHEMA public TO public"))
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()


def _column_type(engine: Engine, table: str, column: str) -> str | None:
    """The column's full declared type, dimension included, or ``None``.

    Read from ``pg_attribute`` + ``format_type`` rather than
    ``information_schema``, whose ``data_type`` for a vector column is the
    uninformative ``USER-DEFINED``. ``format_type`` renders the typmod, so
    ``vector(1536)`` comes back whole -- which is the difference between
    checking the dimension and assuming it.
    """
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = :table AND a.attname = :column "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                "AND n.nspname = 'public'"
            ),
            {"table": table, "column": column},
        ).scalar_one_or_none()


@pytest.mark.postgres
def test_migration_runs_to_head(migrated_engine: Engine) -> None:
    """``alembic upgrade head`` reaches head -- the migration runs at all.

    Named separately from the schema assertions because the failure it catches
    is a different one: a migration that raises is loud and immediate, while a
    migration that runs and produces the wrong schema is silent until an insert
    fails in production.
    """
    with migrated_engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current == _HEAD, (
        f"alembic_version is {current!r} after `upgrade head`, expected {_HEAD!r}. The "
        "migration did not run to completion."
    )


@pytest.mark.postgres
def test_embedding_column_is_vector_at_the_expected_dimension(migrated_engine: Engine) -> None:
    """``knowledge_chunks.embedding`` is ``vector(1536)``, not ``json``.

    This is the direct regression test for the shipped defect. Under the broken
    migration this raised

        (psycopg.errors.DatatypeMismatch) column "embedding" cannot be cast
        automatically to type vector

    and the migration aborted; the assertion is written the way it is because a
    ``json`` column is exactly what a ``sa.JSON()`` declaration produces, and
    ``vector(1536)`` is exactly what the model and the HNSW index need.
    """
    declared = _column_type(migrated_engine, "knowledge_chunks", "embedding")
    assert declared is not None, (
        "knowledge_chunks.embedding does not exist after the migration. The column "
        "is load-bearing for RAG retrieval and must not be dropped to make the "
        "migration succeed."
    )
    assert declared == "vector(1536)", (
        f"knowledge_chunks.embedding is {declared!r}, expected 'vector(1536)'. A json "
        "column cannot be cast to vector by pgvector -- there is no such cast, "
        "explicit or implicit -- so the migration must declare the column AS a "
        "vector on Postgres. 'json' here means the column was created with a "
        "JSON type and the migration still contains a failing ALTER."
    )


@pytest.mark.postgres
def test_hnsw_index_on_embedding_exists(migrated_engine: Engine) -> None:
    """``ix_knowledge_chunks_embedding`` exists and uses the ``hnsw`` method.

    Kept as its own test because it fails independently of the column's type:
    a migration that made the column a vector but skipped the index would leave
    retrieval correct and slow, which is not a failure any type assertion can
    see.
    """
    with migrated_engine.connect() as connection:
        indexdef = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'knowledge_chunks' "
                "AND indexname = 'ix_knowledge_chunks_embedding'"
            )
        ).scalar_one_or_none()

    assert indexdef is not None, (
        "ix_knowledge_chunks_embedding does not exist after the migration. The HNSW "
        "index is what makes pgvector retrieval an index scan instead of a "
        "sequential scan, and it is load-bearing -- docs/data-model.md §5."
    )
    assert "hnsw" in indexdef, (
        f"ix_knowledge_chunks_embedding is {indexdef!r}, expected a USING hnsw index "
        "with vector_cosine_ops."
    )
    assert "vector_cosine_ops" in indexdef, (
        f"ix_knowledge_chunks_embedding is {indexdef!r}; the opclass must be "
        "vector_cosine_ops to match the cosine distance the retriever orders by."
    )


def test_the_three_embedding_widths_agree() -> None:
    """The migration and the two model-side literals declare the same width.

    Runs everywhere -- no Postgres, no Docker, no marker -- because this is the
    mismatch that is otherwise invisible until a deployment with a non-default
    ``EMBEDDING_DIM`` tries to insert a vector. Three literals that agree is the
    deliberate cost of the migration being a fixed point (see the reasoning at
    ``_EMBEDDING_DIM`` in ``0001_initial_schema.py``); this test is what keeps
    that cost honest rather than a comment that quietly goes stale.
    """
    from opspilot.adapters.persistence import models
    from opspilot.adapters.retrieval.pgvector_store import PgVectorStore
    from opspilot.settings import get_settings

    migration_dim = _migration_embedding_dim()
    model_dim = models._EMBEDDING_DIM
    store_dim = PgVectorStore.DEFAULT_DIM
    settings_dim = get_settings().embedding_dim

    assert migration_dim == model_dim == store_dim == settings_dim, (
        "the embedding width is declared in four places and they disagree: "
        f"migration={migration_dim}, models._EMBEDDING_DIM={model_dim}, "
        f"PgVectorStore.DEFAULT_DIM={store_dim}, settings.embedding_dim={settings_dim}. "
        "The migration deliberately does NOT read the setting (a migration must be "
        "a frozen snapshot of history, not a function of the deployer's "
        "environment), so these must be changed together -- by a new revision, "
        "not by an env var."
    )


def _migration_embedding_dim() -> int:
    """The literal width ``0001_initial_schema.py`` declares.

    Read by importing the revision module and reading its constant, rather than
    by parsing the file with a regex: the import is the thing that proves the
    constant exists and is what the migration actually uses, and a regex would
    happily match a number in a comment.
    """
    path = REPO_ROOT / "migrations" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("opspilot_0001_initial_schema", path)
    assert spec is not None and spec.loader is not None, f"cannot import {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module._EMBEDDING_DIM)
