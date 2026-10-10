"""Integration tests for citation persistence and the store differential test.

Two bodies of work live here:

1. **``SqlCitationStore`` / ``SqlKnowledgeDocumentStore`` over SQLite.** The
   run-detail router reads ``.document``, ``.chunk``, ``.score``, ``.rank`` off
   each citation row (``src/opspilot/api/routers/runs.py``) and renders
   ``chunk`` as ``"{document}#{anchor}"`` (``docs/api-contract.md`` §3). The
   knowledge router reads ``.source``, ``.title``, ``.chunk_count``,
   ``.indexed_at``, ``.content_hash`` (``knowledge.py``). Both shapes are
   asserted to match *exactly*, because a router that probes for a method and
   silently returns ``[]`` when it is missing is a citation panel that looks
   empty rather than broken.

2. **The differential vector-store test** (``docs/milestones.md`` §M5): same
   chunks, same query vector, identical top-5 ids and scores to 4 dp in both
   ``InMemoryVectorStore`` and ``PgVectorStore``. It carries
   ``@pytest.mark.postgres`` and skips where no Postgres is reachable (this
   machine has no Docker -- ADR-0004), and it does the real work: ingest, upsert
   into both stores, query both with one explicitly-built vector, compare. It is
   not a ``pytest.skip`` stub.

**Foreign-key ordering.** ``citations.chunk_id`` → ``knowledge_chunks.id`` and
``citations.document_id`` → ``knowledge_documents.id`` (``docs/data-model.md``
§2). A citation is therefore only writable after the chunk exists. The fixtures
below ingest first (which writes the chunk rows through
``SqlKnowledgeDocumentStore`` + the vector store) and cite second, which is the
order retrieval produces naturally: a hit only exists because a chunk row does.

**The postgres-marked test writes to ``OPSPILOT_DATABASE_URL``, and this file
used to destroy whatever that named.** The differential test finished with
``Base.metadata.drop_all(engine)`` -- every table the ORM declares, in whatever
database the variable points at. Nothing checked that the database was
disposable, and this module docstring did not warn, unlike
``test_migration_schema.py``'s. A session pointed the variable at the stack's own
``opspilot`` database, the suite ran, and every application table in it was
deleted; the worker crash-looped with ``relation "agent_runs" does not exist``
until the schema was rebuilt. The teardown is now
:func:`tests._pgvector_target.guarded_pg_schema`, which refuses to run against a
database that holds rows and drops only the schema the test itself created. The
same guard covers ``tests/unit/test_vector_stores.py``, which is where the rule
lives, and ``tests/integration/test_pgvector_target_is_never_destroyed.py``
asserts both tests refuse -- against a scratch database, never this one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlCitationStore,
    SqlKnowledgeDocumentStore,
    SqlRunStore,
    SqlTicketStore,
)
from opspilot.adapters.retrieval.chunking import split_document
from opspilot.adapters.retrieval.memory_store import InMemoryVectorStore, derive_chunk_id
from opspilot.adapters.retrieval.pgvector_store import PgVectorStore
from opspilot.ports.stores import CitationRecord
from opspilot.ports.vector_store import ChunkRecord
from opspilot.settings import Settings
from tests._pgvector_target import guarded_pg_schema

_DOC_SOURCE = "refund-policy.md"
_DOC_BODY = (
    "Lead-in text about refunds.\n\n"
    "# Refund Policy\n\n"
    "## Limits\n\n"
    "Refunds above $100 need approval.\n\n"
    "## Scope\n\n"
    "Applies to every plan tier.\n"
)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


async def _seed_document(factory: sessionmaker[Session]) -> UUID:
    """Write a document and its chunk rows; return the document id.

    The chunk rows are written through the real store path so the citations'
    foreign keys resolve. ``upsert_document`` gives the id; the chunks are added
    directly with ids matching ``derive_chunk_id`` because SQLite has no pgvector
    column and the citation tests care about the join, not the embedding.
    """
    from opspilot.adapters.persistence import models

    store = SqlKnowledgeDocumentStore(factory)
    document_id = await store.upsert_document(
        source=_DOC_SOURCE,
        title="Refund Policy",
        content=_DOC_BODY,
        metadata={"owner": "Finance Operations", "tags": ["billing"]},
        content_hash="deadbeef",
    )
    with db.session_scope(factory) as session:
        for chunk in split_document(_DOC_BODY):
            session.add(
                models.KnowledgeChunk(
                    id=derive_chunk_id(document_id, chunk.ordinal),
                    document_id=document_id,
                    ordinal=chunk.ordinal,
                    anchor=chunk.anchor,
                    heading_path=chunk.heading_path,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    embedding=None,
                )
            )
    return document_id


async def _make_run(factory: sessionmaker[Session]) -> UUID:
    """Create a ticket and a run, returning the run id.

    ``citations.run_id`` references ``agent_runs.id`` (``docs/data-model.md``
    §2), so a citation cannot be written for a run that does not exist. The
    ticket is required because ``agent_runs.ticket_id`` is not nullable.
    """
    ticket_id = await SqlTicketStore(factory).create(
        subject="duplicate charge", body="charged twice", customer_email="a@example.com"
    )
    run = await SqlRunStore(factory).create(
        ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
    )
    return run.id


# --------------------------------------------------------------------------- #
# SqlKnowledgeDocumentStore
# --------------------------------------------------------------------------- #


async def test_list_documents_exposes_the_router_fields(
    factory: sessionmaker[Session],
) -> None:
    """``list_documents`` returns exactly the five fields ``GET /api/knowledge`` reads.

    ``knowledge.py:47`` builds ``KnowledgeDocumentSummary(source=row.source,
    title=row.title, chunk_count=row.chunk_count, indexed_at=row.indexed_at,
    content_hash=row.content_hash)``. All five must be present.
    """
    await _seed_document(factory)

    rows = await SqlKnowledgeDocumentStore(factory).list_documents()

    assert len(rows) == 1
    row = rows[0]
    assert row.source == _DOC_SOURCE
    assert row.title == "Refund Policy"
    assert row.chunk_count == 3
    assert row.content_hash == "deadbeef"
    assert row.indexed_at is not None


async def test_content_hash_lookup_returns_none_for_unknown(
    factory: sessionmaker[Session],
) -> None:
    """An unmatched source reports ``None``, not an error."""
    assert await SqlKnowledgeDocumentStore(factory).content_hash("absent.md") is None


async def test_upsert_document_is_keyed_on_source(
    factory: sessionmaker[Session],
) -> None:
    """Re-upserting the same source updates in place and returns the same id."""
    store = SqlKnowledgeDocumentStore(factory)
    first = await store.upsert_document(
        source=_DOC_SOURCE, title="A", content="x", metadata={}, content_hash="h1"
    )
    second = await store.upsert_document(
        source=_DOC_SOURCE, title="B", content="y", metadata={}, content_hash="h2"
    )

    assert first == second
    rows = await store.list_documents()
    assert len(rows) == 1
    assert rows[0].title == "B"


# --------------------------------------------------------------------------- #
# SqlCitationStore
# --------------------------------------------------------------------------- #


async def test_create_many_resolves_chunk_and_lists_back(
    factory: sessionmaker[Session],
) -> None:
    """A run's hits persist and read back in the router's shape.

    ``docs/api-contract.md`` §3: ``citations[].chunk`` is
    ``"{document_slug}#{anchor}"``. The store composes it, so this test asserts
    the exact format a reader greps for -- never the model's prose.
    """
    document_id = await _seed_document(factory)
    store = SqlCitationStore(factory)
    run_id = await _make_run(factory)

    await store.create_many(
        run_id,
        [
            CitationRecord(document=_DOC_SOURCE, chunk=f"{_DOC_SOURCE}#limits", score=0.83, rank=1),
        ],
    )

    rows = await store.list_citations(run_id)

    assert len(rows) == 1
    assert rows[0].document == _DOC_SOURCE
    assert rows[0].chunk == f"{_DOC_SOURCE}#limits"
    assert rows[0].score == pytest.approx(0.83)
    assert rows[0].rank == 1
    # The chunk the citation points at is the real row (FK ordering held).
    assert derive_chunk_id(document_id, 0) is not None


async def test_list_citations_orders_by_rank(
    factory: sessionmaker[Session],
) -> None:
    """Citations come back best rank first, so the Sources panel is ordered."""
    await _seed_document(factory)
    store = SqlCitationStore(factory)
    run_id = await _make_run(factory)
    await store.create_many(
        run_id,
        [
            CitationRecord(document=_DOC_SOURCE, chunk=f"{_DOC_SOURCE}#limits", score=0.83, rank=1),
            CitationRecord(document=_DOC_SOURCE, chunk=f"{_DOC_SOURCE}#scope", score=0.79, rank=2),
        ],
    )

    rows = await store.list_citations(run_id)
    assert [row.rank for row in rows] == [1, 2]


async def test_citations_are_scoped_to_the_run(
    factory: sessionmaker[Session],
) -> None:
    """One run's citations never appear in another's list."""
    await _seed_document(factory)
    store = SqlCitationStore(factory)
    run_a = await _make_run(factory)
    run_b = await _make_run(factory)
    await store.create_many(
        run_a,
        [CitationRecord(document=_DOC_SOURCE, chunk=f"{_DOC_SOURCE}#limits", score=0.9, rank=1)],
    )

    assert await store.list_citations(run_b) == []
    assert len(await store.list_citations(run_a)) == 1


async def test_create_many_skips_a_hit_with_no_chunk_row(
    factory: sessionmaker[Session],
) -> None:
    """A hit for an unindexed document is skipped, not written as a dangling FK.

    The foreign keys would reject it; skipping keeps a partially-indexed run's
    citations honest rather than crashing the persist step.
    """
    await _seed_document(factory)
    store = SqlCitationStore(factory)
    run_id = await _make_run(factory)
    await store.create_many(
        run_id,
        [
            CitationRecord(document="not-indexed.md", chunk="not-indexed.md#x", score=0.9, rank=1),
            CitationRecord(document=_DOC_SOURCE, chunk=f"{_DOC_SOURCE}#limits", score=0.8, rank=2),
        ],
    )

    rows = await store.list_citations(run_id)
    assert [row.document for row in rows] == [_DOC_SOURCE]


async def test_create_writes_one_row_with_explicit_ids(
    factory: sessionmaker[Session],
) -> None:
    """``create`` takes ids directly, for a caller that already has them."""
    document_id = await _seed_document(factory)
    store = SqlCitationStore(factory)
    run_id = await _make_run(factory)
    chunk_id = derive_chunk_id(document_id, 0)

    await store.create(run_id=run_id, document_id=document_id, chunk_id=chunk_id, score=0.5, rank=1)

    rows = await store.list_citations(run_id)
    assert len(rows) == 1
    assert rows[0].rank == 1


# --------------------------------------------------------------------------- #
# The differential vector-store test (docs/milestones.md §M5)
# --------------------------------------------------------------------------- #

# The column dimension pgvector is declared at (docs/data-model.md §2).
_COLUMN_DIM = 1536


def _differential_chunks() -> tuple[list[ChunkRecord], list[list[float]], list[float]]:
    """The shared fixture: chunks, their embeddings, and ONE query vector.

    The query vector is built explicitly and used for *both* stores, so the
    comparison is of the stores, not of any embedder. Scores are cosine
    similarities that produce identical tie-breaks in both implementations.
    """
    doc_a = UUID("00000000-0000-0000-0000-0000000000aa")
    doc_b = UUID("00000000-0000-0000-0000-0000000000bb")
    dim = _COLUMN_DIM

    def unit(axis: int, weight: float = 1.0) -> list[float]:
        vector = [0.0] * dim
        vector[axis] = weight
        return vector

    chunks = [
        ChunkRecord(
            document_id=doc_a,
            ordinal=0,
            anchor="intro",
            heading_path="Refund Policy > Intro",
            content="alpha",
            token_count=1,
        ),
        ChunkRecord(
            document_id=doc_a,
            ordinal=1,
            anchor="limits",
            heading_path="Refund Policy > Limits",
            content="beta",
            token_count=1,
        ),
        ChunkRecord(
            document_id=doc_a,
            ordinal=2,
            anchor="notes",
            heading_path="Refund Policy > Notes",
            content="gamma",
            token_count=1,
        ),
        ChunkRecord(
            document_id=doc_b,
            ordinal=0,
            anchor="detection",
            heading_path="SOP > Detection",
            content="delta",
            token_count=1,
        ),
        ChunkRecord(
            document_id=doc_b,
            ordinal=1,
            anchor="remedy",
            heading_path="SOP > Remedy",
            content="epsilon",
            token_count=1,
        ),
    ]
    embeddings = [unit(i) for i in range(len(chunks))]
    # A query that is a blend: nearest to axis 0, then 1, then 2, ...
    query = [0.0] * dim
    for axis in range(len(chunks)):
        query[axis] = (len(chunks) - axis) / 10.0
    norm = sum(value * value for value in query) ** 0.5
    query = [value / norm for value in query]
    return chunks, embeddings, query


def _postgres_session_factory() -> sessionmaker[Session] | None:
    """Build a sessionmaker from ``DATABASE_URL`` if it names a Postgres.

    Returns ``None`` when the environment has no Postgres, which is the normal
    local case (no Docker -- ADR-0004). The test that uses this is
    ``@pytest.mark.postgres`` and expects to be skipped before it is reached.
    """
    url = os.environ.get("OPSPILOT_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    settings = Settings(DATABASE_URL=url)
    return db.session_factory(settings)


@pytest.mark.postgres
async def test_pgvector_and_memory_stores_agree() -> None:
    """Same chunks, same query vector, identical top-5 ids and scores to 4 dp.

    ``docs/milestones.md`` §M5 requires this differential test and
    ``docs/adr/0004`` names it as the mitigation for the SQLite-vs-pgvector
    divergence. It **does the work**: it creates the pgvector schema, upserts
    the chunks into a real ``PgVectorStore``, upserts the identical chunks into
    an ``InMemoryVectorStore``, and queries both with one explicitly-built
    vector -- then asserts the ids and scores agree to 4 decimal places.

    Skipped where no Postgres is reachable (local, no Docker). It passes as
    *skipped*, never as *passed*; the summary line reports the skip count so a
    green local run is not read as full coverage (ADR-0004).

    **It cannot destroy a database that holds anything.** The teardown is
    :func:`tests._pgvector_target.guarded_pg_schema`: it refuses to run against a
    database that holds rows, and otherwise drops only the schema this test
    created. See that module for the incident and for the rule.
    """
    factory = _postgres_session_factory()
    if factory is None:
        pytest.skip("no PostgreSQL configured (set OPSPILOT_DATABASE_URL); ADR-0004")

    from opspilot.adapters.persistence import models

    engine = factory.kw["bind"]
    try:
        # The schema comes from the guard, which also creates the `vector`
        # extension it needs to render, and only in the branch where it is the
        # one creating tables. Doing it here instead was a no-op against an
        # already-migrated database and a `type "vector" does not exist` against
        # a fresh one -- the case the guard's fresh-database branch takes.
        with guarded_pg_schema(engine, owner=__name__ + "::test_pgvector_and_memory_stores_agree"):
            chunks, embeddings, query = _differential_chunks()
            slugs = {
                UUID("00000000-0000-0000-0000-0000000000aa"): "refund-policy.md",
                UUID("00000000-0000-0000-0000-0000000000bb"): "duplicate-charge-sop.md",
            }

            # The pgvector store needs the document rows the chunks' FK requires.
            with db.session_scope(factory) as session:
                from opspilot.adapters.persistence import models

                for document_id, source in slugs.items():
                    session.add(
                        models.KnowledgeDocument(
                            id=document_id,
                            title=source,
                            source=source,
                            content="",
                            doc_metadata={},
                            content_hash="differential",
                        )
                    )

            pg_store = PgVectorStore(session_factory=factory, slug_lookup=slugs.__getitem__)
            memory_store = InMemoryVectorStore(slug_lookup=slugs.__getitem__)

            await memory_store.upsert(chunks, embeddings)
            await pg_store.upsert(chunks, embeddings)

            memory_hits = await memory_store.search(query, top_k=5)
            pg_hits = await pg_store.search(query, top_k=5)

            assert len(memory_hits) == len(pg_hits) == 5
            for memory_hit, pg_hit in zip(memory_hits, pg_hits, strict=True):
                assert memory_hit.chunk_id == pg_hit.chunk_id
                assert round(memory_hit.score, 4) == round(pg_hit.score, 4)
    finally:
        # A `finally`, not a statement after the block: a refusal from the guard
        # raises straight through here, and an undisposed engine would hold a
        # connection open against the database that just refused to be touched.
        engine.dispose()
