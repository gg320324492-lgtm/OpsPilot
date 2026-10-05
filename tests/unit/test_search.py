"""Tests for ``retrieval.search.retrieve``.

Written from the specification:

- ``docs/architecture.md`` §9: "Abstention is a supported outcome. If the top
  score is below a threshold, the run escalates instead of guessing."
- ``docs/milestones.md`` §M5: "Below ``RETRIEVAL_MIN_SCORE`` → the run
  escalates; the retrieval dataset's ``expect_abstention`` cases assert this."
- ``docs/api-contract.md`` §3: a retrieval result is
  ``(document, chunk, score, rank)``; ``rank`` starts at 1 with the best first.
- The brief: ``abstained=True`` when there are no hits OR the best score is
  below ``min_score``; ``top_score`` is ``None`` when there are no hits.

A recording fake embedder is used so a test can prove the query is embedded
**once** and that the threshold is applied to the score the same vector
produced.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from opspilot.adapters.retrieval.search import RetrievalOutcome, retrieve
from opspilot.ports.vector_store import ChunkRecord, SearchHit

_DOC = UUID("00000000-0000-0000-0000-0000000000aa")


def _hit(score: float, rank: int, anchor: str = "limits") -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        document_id=_DOC,
        document_slug="refund-policy.md",
        anchor=anchor,
        content="Refunds above $100 need approval.",
        score=score,
        rank=rank,
    )


class RecordingEmbedder:
    """An ``Embedder`` that records every call and returns a fixed vector."""

    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector if vector is not None else [1.0, 0.0, 0.0, 0.0]
        self.calls: list[list[str]] = []

    @property
    def dim(self) -> int:
        return len(self._vector)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self._vector) for _ in texts]


class FixedStore:
    """A ``VectorStore`` returning a fixed hit list and recording the query."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.queries: list[tuple[list[float], int]] = []

    async def upsert(
        self,
        chunks: list[ChunkRecord],  # noqa: ARG002 -- port signature
        embeddings: list[list[float]],  # noqa: ARG002 -- port signature
    ) -> None:
        return None

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        self.queries.append((list(query_embedding), top_k))
        return list(self._hits[:top_k])


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_retrieve_returns_hits_above_threshold() -> None:
    """A best score at or above ``min_score`` is not an abstention."""
    store = FixedStore([_hit(0.9, 1), _hit(0.8, 2)])
    outcome = await retrieve("duplicate charge", embedder=RecordingEmbedder(), store=store)

    assert isinstance(outcome, RetrievalOutcome)
    assert outcome.abstained is False
    assert [hit.rank for hit in outcome.hits] == [1, 2]
    assert outcome.top_score == pytest.approx(0.9)


async def test_retrieve_passes_top_k_through_to_the_store() -> None:
    """``top_k`` reaches the store unchanged."""
    store = FixedStore([_hit(0.9, 1)])
    await retrieve("q", embedder=RecordingEmbedder(), store=store, top_k=3)
    assert store.queries[0][1] == 3


async def test_retrieve_embeds_the_query_exactly_once() -> None:
    """The query is embedded once; the same vector drives search and threshold.

    Embedding twice -- once to rank, once to score -- would let the two drift
    apart. The spec's outcome is a single decision, so there is a single
    embedding.
    """
    embedder = RecordingEmbedder()
    store = FixedStore([_hit(0.9, 1)])
    await retrieve("duplicate charge", embedder=embedder, store=store)

    assert embedder.calls == [["duplicate charge"]]
    assert store.queries[0][0] == embedder._vector


async def test_retrieve_top_score_is_the_best_hit_score() -> None:
    """``top_score`` is the score of rank 1, not an average or a max by accident."""
    store = FixedStore([_hit(0.7, 1, "a"), _hit(0.95, 2, "b")])
    # The store is the authority on ordering; rank 1 is the best. This fixture
    # puts a lower first deliberately to prove top_score follows *rank 1*.
    outcome = await retrieve("q", embedder=RecordingEmbedder(), store=store, min_score=0.0)
    assert outcome.top_score == pytest.approx(0.7)


# --------------------------------------------------------------------------- #
# Abstention
# --------------------------------------------------------------------------- #


async def test_retrieve_abstains_when_no_hits() -> None:
    """No hits: abstain, and ``top_score`` is ``None`` (the brief)."""
    outcome = await retrieve("nothing matches", embedder=RecordingEmbedder(), store=FixedStore([]))

    assert outcome.abstained is True
    assert outcome.hits == []
    assert outcome.top_score is None


async def test_retrieve_abstains_below_threshold() -> None:
    """A best score below ``min_score`` abstains; the hits are still returned.

    ``docs/architecture.md`` §9: below the threshold the run escalates. The
    hits are carried so the caller can see what weak evidence existed, but
    ``abstained`` is ``True`` so no answer is built from it.
    """
    store = FixedStore([_hit(0.20, 1), _hit(0.10, 2)])
    outcome = await retrieve(
        "unrelated question", embedder=RecordingEmbedder(), store=store, min_score=0.35
    )

    assert outcome.abstained is True
    assert outcome.top_score == pytest.approx(0.20)
    assert len(outcome.hits) == 2


async def test_retrieve_threshold_is_inclusive_at_min_score() -> None:
    """A score exactly at ``min_score`` is *not* below it, so it does not abstain.

    ``docs/architecture.md`` §9 says "below a threshold"; a score equal to the
    threshold is not below it.
    """
    store = FixedStore([_hit(0.35, 1)])
    outcome = await retrieve("edge", embedder=RecordingEmbedder(), store=store, min_score=0.35)
    assert outcome.abstained is False


async def test_retrieve_default_min_score_is_0_35() -> None:
    """The default threshold is ``0.35`` (the brief's signature).

    A hit just below the default abstains; a hit just above does not -- both
    with no ``min_score`` argument passed.
    """
    below = await retrieve("q", embedder=RecordingEmbedder(), store=FixedStore([_hit(0.34, 1)]))
    above = await retrieve("q", embedder=RecordingEmbedder(), store=FixedStore([_hit(0.36, 1)]))

    assert below.abstained is True
    assert above.abstained is False


async def test_retrieve_abstention_is_not_an_exception() -> None:
    """Abstention is a supported outcome, not a failure (architecture §9)."""
    outcome = await retrieve(
        "espresso martini recipe", embedder=RecordingEmbedder(), store=FixedStore([])
    )
    # No raise; the caller escalates on ``abstained``.
    assert outcome.abstained is True
