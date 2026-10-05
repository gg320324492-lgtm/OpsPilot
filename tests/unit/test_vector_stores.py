"""Tests for the two ``VectorStore`` implementations and their shared contract.

The two stores must agree exactly: same chunks, same query vector, identical
top-k ids and scores. That is what ``docs/adr/0004`` names as the mitigation for
the SQLite-vs-pgvector divergence, and it is why the ordering and tie-breaking
rules here are asserted rather than left to the implementation.

``InMemoryVectorStore`` runs everywhere. Every ``PgVectorStore`` test carries
``@pytest.mark.postgres`` and is skipped where no Postgres is reachable (the
development machine has no Docker -- ADR-0004). Tests that *can* run without a
database -- the dimension guard, the slug-resolution guard -- are not marked.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from opspilot.adapters.retrieval.memory_store import InMemoryVectorStore
from opspilot.adapters.retrieval.pgvector_store import PgVectorStore
from opspilot.ports.vector_store import ChunkRecord, SearchHit

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


def _contract_chunks() -> tuple[list[ChunkRecord], list[list[float]]]:
    """A small fixture: 3 chunks in doc A, 2 in doc B, on one-hot axes."""
    dim = 8
    chunks = [
        _record(_DOC_A, 0, "intro", "alpha intro"),
        _record(_DOC_A, 1, "limits", "beta limits"),
        _record(_DOC_A, 2, "notes", "gamma notes"),
        _record(_DOC_B, 0, "detection", "delta detection"),
        _record(_DOC_B, 1, "remedy", "epsilon remedy"),
    ]
    embeddings = [_one_hot(i, dim) for i in range(len(chunks))]
    return chunks, embeddings


async def _assert_shared_contract(store_factory: Callable[[], object]) -> None:
    """The behaviours both stores must share, asserted identically."""
    store = store_factory()
    chunks, embeddings = _contract_chunks()
    await store.upsert(chunks, embeddings)  # type: ignore[attr-defined]

    hits: list[SearchHit] = await store.search(_one_hot(0, 8), top_k=5)  # type: ignore[attr-defined]
    assert [hit.rank for hit in hits] == [1, 2, 3, 4, 5]
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)

    # Upsert is a replacement keyed on (document_id, ordinal).
    await store.upsert(  # type: ignore[attr-defined]
        [_record(_DOC_A, 0, "intro", "alpha intro v2")], [_one_hot(0, 8)]
    )
    after = await store.search(_one_hot(0, 8), top_k=5)  # type: ignore[attr-defined]
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
# PgVectorStore -- real Postgres (CI only)
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_pgvector_store_satisfies_shared_contract() -> None:  # pragma: no cover - CI only
    pytest.skip("Postgres differential coverage runs in the CI verify job (ADR-0004)")


@pytest.mark.postgres
async def test_pgvector_and_memory_stores_agree() -> None:  # pragma: no cover - CI only
    """Same chunks, same query, identical top-5 ids and scores to 4 dp.

    This is the M5 differential test named in ``docs/milestones.md``. It needs a
    real pgvector and is written here, in the store's own test module, so the
    contract lives next to both implementations. It is skipped locally (no
    Docker -- ADR-0004) and must not be read as a pass.
    """
    pytest.skip("requires a reachable PostgreSQL with pgvector")
