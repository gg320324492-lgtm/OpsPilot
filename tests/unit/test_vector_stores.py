"""Tests for the two ``VectorStore`` implementations and their shared contract.

The two stores must agree exactly: same chunks, same query vector, identical
top-k ids and scores. That is what ``docs/adr/0004`` names as the mitigation for
the SQLite-vs-pgvector divergence, and it is why the ordering and tie-breaking
rules here are asserted rather than left to the implementation.

``InMemoryVectorStore`` runs everywhere. ``PgVectorStore`` is split in two:

* **Construction guards.** They fail on a misconfiguration rather than on I/O,
  so they run on every machine and carry no marker.
* **Behaviour on a real pgvector.** Marked ``@pytest.mark.postgres`` and driven
  by ``OPSPILOT_DATABASE_URL``, the same variable
  ``tests/integration/test_citations.py`` reads, so one setting configures every
  Postgres-marked test in the suite: with a Postgres URL the test connects and
  runs, without one it skips and names the variable to set. Locally that is the
  normal case (no Docker -- ADR-0004); in CI the ``verify`` job sets it and
  asserts the test *PASSED*, so there the skip is the failure being caught.

  **Where that URL points is now checked, not assumed.** Both postgres-marked
  tests used to tear down with ``Base.metadata.drop_all(engine)`` -- every table
  the ORM declares, in whatever database the variable named, with no warning in
  either file. A session pointed it at the stack's own ``opspilot`` database and
  deleted that database's application tables; the worker crash-looped with
  ``relation "agent_runs" does not exist`` until the schema was rebuilt. Both
  tests now run inside :func:`tests._pgvector_target.guarded_pg_schema`, which
  refuses to run against a database that holds rows, and ``tests/integration/
  test_pgvector_target_is_never_destroyed.py`` asserts the refusal -- by running
  these tests against a scratch database and checking what survived.

The *differential* claim -- the two stores return the same ids and scores -- is
asserted in exactly one place, ``tests/integration/test_citations.py::
test_pgvector_and_memory_stores_agree``. A second copy of that test used to sit
at the bottom of this module as an unconditional ``pytest.skip``: it was
collected on every machine and executed on none, CI included, which read as
coverage while providing zero. It is deleted (``docs/adr/0004`` records the
decision). Two tests asserting one property drift apart about what "the same
chunks" means; the shared-contract test below covers what the differential
cannot -- dense ranks, and upsert-as-replacement against the real store.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.retrieval.memory_store import InMemoryVectorStore
from opspilot.adapters.retrieval.pgvector_store import PgVectorStore
from opspilot.ports.vector_store import ChunkRecord, SearchHit
from opspilot.settings import Settings
from tests._pgvector_target import guarded_pg_schema

# The dimension the pgvector column is declared at (docs/data-model.md §2).
COLUMN_DIM = 1536


def _unit(vector: list[float]) -> list[float]:
    """Scale a vector to unit length so dot product equals cosine similarity."""
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return [1.0] + [0.0] * (len(vector) - 1)
    return [component / norm for component in vector]


def _one_hot(index: int, dim: int) -> list[float]:
    """A unit vector that is 1.0 at ``index`` and 0.0 elsewhere."""
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


def _record(document_id: UUID, ordinal: int, anchor: str, content: str) -> ChunkRecord:
    return ChunkRecord(
        document_id=document_id,
        ordinal=ordinal,
        anchor=anchor,
        heading_path="Doc > " + anchor,
        content=content,
        token_count=len(content.split()),
    )


# --------------------------------------------------------------------------- #
# InMemoryVectorStore
# --------------------------------------------------------------------------- #


async def test_memory_search_returns_top_k_best_first() -> None:
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 8
    doc = uuid4()
    chunks = [_record(doc, i, f"a{i}", f"chunk {i}") for i in range(5)]
    embeddings = [_one_hot(i, dim) for i in range(5)]
    await store.upsert(chunks, embeddings)

    hits = await store.search(_one_hot(3, dim), top_k=3)

    assert len(hits) == 3
    assert hits[0].anchor == "a3"
    assert hits[0].score == pytest.approx(1.0)
    assert [hit.rank for hit in hits] == [1, 2, 3]


async def test_memory_search_scores_are_cosine_similarity() -> None:
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    doc = uuid4()
    await store.upsert([_record(doc, 0, "a", "x")], [_unit([1.0, 1.0, 0.0, 0.0])])
    hits = await store.search(_unit([1.0, 0.0, 0.0, 0.0]), top_k=1)
    assert hits[0].score == pytest.approx(1.0 / math.sqrt(2.0), abs=1e-9)


async def test_memory_upsert_replaces_by_document_and_ordinal() -> None:
    """Re-upserting ``(document_id, ordinal)`` replaces, never duplicates."""
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 4
    doc = uuid4()
    await store.upsert([_record(doc, 0, "old", "original")], [_one_hot(0, dim)])
    await store.upsert([_record(doc, 0, "new", "updated")], [_one_hot(1, dim)])

    all_hits = await store.search(_one_hot(1, dim), top_k=10)
    assert len(all_hits) == 1
    assert all_hits[0].anchor == "new"
    assert all_hits[0].content == "updated"


async def test_memory_upsert_does_not_replace_across_documents() -> None:
    """The same ordinal in two documents is two distinct chunks."""
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 4
    doc_a, doc_b = uuid4(), uuid4()
    await store.upsert(
        [_record(doc_a, 0, "a", "x"), _record(doc_b, 0, "b", "y")],
        [_one_hot(0, dim), _one_hot(1, dim)],
    )
    hits = await store.search(_one_hot(0, dim), top_k=10)
    assert len(hits) == 2


async def test_memory_search_ranks_start_at_one_and_are_dense() -> None:
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 4
    doc = uuid4()
    await store.upsert(
        [_record(doc, i, f"a{i}", "x") for i in range(3)],
        [_one_hot(i, dim) for i in range(3)],
    )
    hits = await store.search(_one_hot(0, dim), top_k=3)
    assert [hit.rank for hit in hits] == [1, 2, 3]


async def test_memory_search_top_k_larger_than_store_returns_all() -> None:
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 4
    doc = uuid4()
    await store.upsert([_record(doc, 0, "a", "x")], [_one_hot(0, dim)])
    hits = await store.search(_one_hot(0, dim), top_k=10)
    assert len(hits) == 1


async def test_memory_search_ties_break_deterministically() -> None:
    """Equal scores sort by ``(-score, document_slug, anchor)``.

    Without a defined tie-break the differential test against pgvector flakes,
    because two chunks with identical scores would come back in arbitrary order.
    """
    store = InMemoryVectorStore(slug_lookup=lambda doc: {_DOC_B: "zebra", _DOC_A: "alpha"}[doc])
    dim = 4
    # Two chunks with identical embeddings -> identical scores -> a tie.
    await store.upsert(
        [
            _record(_DOC_B, 0, "bbb", "b"),
            _record(_DOC_A, 0, "aaa", "a"),
        ],
        [_one_hot(0, dim), _one_hot(0, dim)],
    )
    hits = await store.search(_one_hot(0, dim), top_k=2)
    assert [hit.document_slug for hit in hits] == ["alpha", "zebra"]


async def test_memory_search_resolves_document_slug_via_lookup() -> None:
    """The store holds chunk records with a ``document_id``, not a slug.

    The slug comes from an injected lookup, and the hit must carry the resolved
    value -- the citation is ``"{document_slug}#{anchor}"``.
    """
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "refund-policy.md")
    dim = 4
    doc = uuid4()
    await store.upsert([_record(doc, 0, "limits", "x")], [_one_hot(0, dim)])
    hits = await store.search(_one_hot(0, dim), top_k=1)
    assert hits[0].document_slug == "refund-policy.md"


async def test_memory_search_hit_chunk_id_is_deterministic() -> None:
    """Re-inserting the same chunk yields the same ``chunk_id``.

    ``ChunkRecord`` carries no id, so the store must derive one; deriving it
    deterministically (from ``document_id`` and ``ordinal``) is what lets both
    stores agree on ids and what makes upsert a replacement rather than an
    ever-growing pile.
    """
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    dim = 4
    doc = uuid4()
    await store.upsert([_record(doc, 0, "a", "x")], [_one_hot(0, dim)])
    first = (await store.search(_one_hot(0, dim), top_k=1))[0].chunk_id
    await store.upsert([_record(doc, 0, "a", "x")], [_one_hot(0, dim)])
    second = (await store.search(_one_hot(0, dim), top_k=1))[0].chunk_id
    assert first == second


async def test_memory_upsert_rejects_mismatched_lengths() -> None:
    store = InMemoryVectorStore(slug_lookup=lambda _doc: "doc")
    doc = uuid4()
    with pytest.raises(ValueError):
        await store.upsert([_record(doc, 0, "a", "x")], [])


# --------------------------------------------------------------------------- #
# The shared contract, expressed once and run against both stores.
# --------------------------------------------------------------------------- #

_DOC_A = UUID("00000000-0000-0000-0000-0000000000aa")
_DOC_B = UUID("00000000-0000-0000-0000-0000000000bb")


def _contract_chunks(dim: int = COLUMN_DIM) -> tuple[list[ChunkRecord], list[list[float]]]:
    """A small fixture: 3 chunks in doc A, 2 in doc B, on one-hot axes.

    ``dim`` defaults to the pgvector column's declared dimension so one fixture
    drives both stores. ``PgVectorStore`` rejects any other dimension at
    construction, so an 8-dimensional fixture -- which only the in-memory store
    accepts -- would make "the shared contract" a claim about one store with the
    other arguing past it.
    """
    chunks = [
        _record(_DOC_A, 0, "intro", "alpha intro"),
        _record(_DOC_A, 1, "limits", "beta limits"),
        _record(_DOC_A, 2, "notes", "gamma notes"),
        _record(_DOC_B, 0, "detection", "delta detection"),
        _record(_DOC_B, 1, "remedy", "epsilon remedy"),
    ]
    embeddings = [_one_hot(i, dim) for i in range(len(chunks))]
    return chunks, embeddings


async def _assert_shared_contract(
    store_factory: Callable[[], object], dim: int = COLUMN_DIM
) -> None:
    """The behaviours both stores must share, asserted identically."""
    store = store_factory()
    chunks, embeddings = _contract_chunks(dim)
    await store.upsert(chunks, embeddings)  # type: ignore[attr-defined]

    hits: list[SearchHit] = await store.search(_one_hot(0, dim), top_k=5)  # type: ignore[attr-defined]
    assert [hit.rank for hit in hits] == [1, 2, 3, 4, 5]
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)

    # Upsert is a replacement keyed on (document_id, ordinal).
    await store.upsert(  # type: ignore[attr-defined]
        [_record(_DOC_A, 0, "intro", "alpha intro v2")], [_one_hot(0, dim)]
    )
    after = await store.search(_one_hot(0, dim), top_k=5)  # type: ignore[attr-defined]
    assert len(after) == 5
    assert after[0].content == "alpha intro v2"


async def test_memory_store_satisfies_shared_contract() -> None:
    await _assert_shared_contract(lambda: InMemoryVectorStore(slug_lookup=lambda _doc: "doc"))


# --------------------------------------------------------------------------- #
# PgVectorStore -- construction-time guards (runnable without a database)
# --------------------------------------------------------------------------- #


def _slug_lookup(_doc: UUID) -> str:
    return "doc"


def test_pgvector_store_default_dim_is_column_dim() -> None:
    """The store's default dimension must match the column's ``vector(1536)``."""
    assert PgVectorStore.DEFAULT_DIM == COLUMN_DIM


def test_pgvector_store_rejects_dim_mismatch_loudly() -> None:
    """A dimension other than the column's is a configuration error, not silent.

    The column is ``vector(1536)`` (data-model.md §2). A store constructed at a
    different dimension could never satisfy the column, and inserting a
    wrong-width vector would fail deep inside pgvector. This fails at
    construction instead, naming both numbers.
    """
    with pytest.raises(ValueError) as excinfo:
        PgVectorStore(
            session_factory=lambda: None,  # type: ignore[arg-type]
            slug_lookup=_slug_lookup,
            dim=768,
        )
    message = str(excinfo.value)
    assert "768" in message
    assert str(COLUMN_DIM) in message


def test_pgvector_store_accepts_the_column_dimension() -> None:
    store = PgVectorStore(
        session_factory=lambda: None,  # type: ignore[arg-type]
        slug_lookup=_slug_lookup,
        dim=COLUMN_DIM,
    )
    assert store.dim == COLUMN_DIM


# --------------------------------------------------------------------------- #
# PgVectorStore -- real Postgres, where a Postgres is configured
# --------------------------------------------------------------------------- #


def _postgres_session_factory() -> sessionmaker[Session] | None:
    """A sessionmaker over ``OPSPILOT_DATABASE_URL``, if it names a Postgres.

    Returns ``None`` when the variable is unset or is not a ``postgresql`` URL,
    which is the local case (no Docker -- ADR-0004). It is the same variable, and
    the same "skip when absent" contract, that
    ``tests/integration/test_citations.py`` uses for its differential test: one
    environment setting configures every Postgres-marked test in the suite, so a
    developer with a server points one variable at it and the whole marked set
    runs.
    """
    url = os.environ.get("OPSPILOT_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    return db.session_factory(Settings(DATABASE_URL=url))


async def _run_pgvector_shared_contract(factory: sessionmaker[Session]) -> None:
    """Assert the shared contract on a real ``PgVectorStore``.

    Writes the document rows the ``search`` join and the chunks' foreign key
    require, then runs the very same assertions the in-memory store is held to.

    What it no longer does is drop the schema on the way out. It used to finish
    with ``Base.metadata.drop_all(engine)``, which drops every table the ORM
    declares in *whatever* database ``OPSPILOT_DATABASE_URL`` named -- which is
    how a session pointed at the stack's own ``opspilot`` database deleted that
    database's application tables and crash-looped the worker. The teardown is
    now :func:`tests._pgvector_target.guarded_pg_schema`: it refuses to run
    against a database that holds rows, and on a database that already has the
    schema it deletes only the rows this function wrote, leaving the schema for
    whoever else is using it.

    Note what is no longer in here: the unconditional ``DELETE FROM citations /
    knowledge_chunks / knowledge_documents`` that preceded the inserts. They
    existed so a leftover row from an earlier run could not make "5 chunks" read
    as "6" -- but they were also, in a populated database, a silent deletion of
    somebody's data before any assertion ran. The guard refuses in that case
    instead, and the teardown cleans up after itself, so there is nothing left to
    defend against. Removing them also means that if the guard were ever deleted,
    this test would fail on a duplicate key rather than quietly emptying a table.
    """
    engine = factory.kw["bind"]
    try:
        with guarded_pg_schema(engine, owner=__name__ + "::_run_pgvector_shared_contract"):
            # The schema comes from the guard, extension included: it creates
            # `vector` where it is the one creating tables, which it must do
            # before `create_all` renders the vector(1536) column natively.
            slugs = {_DOC_A: "refund-policy.md", _DOC_B: "duplicate-charge-sop.md"}
            with db.session_scope(factory) as session:
                for document_id, source in slugs.items():
                    session.add(
                        models.KnowledgeDocument(
                            id=document_id,
                            title=source,
                            source=source,
                            content="",
                            doc_metadata={},
                            content_hash="shared-contract",
                        )
                    )

            store = PgVectorStore(session_factory=factory, slug_lookup=slugs.__getitem__)
            await _assert_shared_contract(lambda: store)
    finally:
        engine.dispose()


@pytest.mark.postgres
async def test_pgvector_store_satisfies_shared_contract() -> None:
    """The shared contract, against the real store on a reachable Postgres.

    This is the half of the contract the differential test in
    ``tests/integration/test_citations.py`` cannot see: ranks that are dense
    from one, a perfect score first, and ``upsert`` as a *replacement* keyed on
    ``(document_id, ordinal)`` -- a second ingest rewriting the row rather than
    appending a duplicate of the same chunk. ``InMemoryVectorStore`` is held to
    exactly these assertions by
    ``test_memory_store_satisfies_shared_contract``; this is pgvector's turn.

    It reads ``OPSPILOT_DATABASE_URL`` and skips where that does not name a
    Postgres. The CI ``verify`` job sets it and asserts this test PASSED
    (see ``.github/workflows/ci.yml``), so there a skip is the failure the job
    exists to catch rather than a green light.

    Where that URL points, this test used to be a permission slip: it dropped
    every table the ORM declares, there. It now runs inside
    :func:`tests._pgvector_target.guarded_pg_schema`, which refuses to run
    against a database that holds rows and otherwise leaves the database with
    the tables it had and none of the data.
    """
    factory = _postgres_session_factory()
    if factory is None:
        pytest.skip("no PostgreSQL configured (set OPSPILOT_DATABASE_URL); ADR-0004")
    await _run_pgvector_shared_contract(factory)


# The differential claim -- same chunks, same query, identical top-5 ids and
# scores -- has one home: the ``test_pgvector_and_memory_stores_agree`` test in
# ``tests/integration/test_citations.py``. A second copy of it lived here as an
# unconditional ``pytest.skip``, collected on every machine and executed on none,
# CI included; it was deleted rather than left to read as coverage (ADR-0004
# records the decision). Nothing in this module compares the two stores, and
# nothing else should: asserting one property in two places is how the two
# places come to disagree about what the property is.
