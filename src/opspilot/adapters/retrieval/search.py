"""Retrieval search: embed the query, get top-k hits, apply the abstention threshold.

Responsibility: the one function the agent calls to retrieve policy. It embeds
the query (or extracts a good query from the ticket), asks the ``VectorStore``
for top-k, and applies ``RETRIEVAL_MIN_SCORE``: below the threshold it reports
*abstention*, which is a supported outcome -- the run escalates rather than
answering from weak evidence.

Layer: ``adapters`` (retrieval). Depends on the ``VectorStore`` port, so it is
byte-identical across pgvector and the in-memory store.

Citations are structural: the returned hits carry ``document_id`` and
``chunk_id``, and the agent's answer carries those ids. The UI renders
"Sources: refund-policy.md, duplicate-charge-sop.md" from ids, never by parsing
the model's prose.
"""

from __future__ import annotations

from dataclasses import dataclass

from opspilot.adapters.retrieval.embeddings import Embedder
from opspilot.ports.vector_store import SearchHit, VectorStore


@dataclass(frozen=True)
class RetrievalOutcome:
    """Hits plus whether the evidence was strong enough to use."""

    hits: list[SearchHit]
    abstained: bool
    top_score: float | None


async def retrieve(
    query: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    top_k: int = 5,
    min_score: float = 0.35,
) -> RetrievalOutcome:
    """Embed ``query`` and return the top-k hits, or abstain.

    The query is embedded **once** and that one vector drives the search: the
    score the threshold is applied to is the score of the same vector that
    produced the ranking, so "best hit" and "the score we compare to
    ``min_score``" can never disagree.

    Args:
        query: The text to retrieve for.
        embedder: Turns the query into a vector.
        store: The ``VectorStore`` holding the corpus.
        top_k: How many hits to ask the store for.
        min_score: The abstention threshold (``RETRIEVAL_MIN_SCORE``).

    Returns:
        A ``RetrievalOutcome``. ``abstained`` is ``True`` when the store returns
        no hits **or** the best score is below ``min_score``; ``top_score`` is
        ``None`` only when there are no hits. The hits are still returned on a
        below-threshold result -- the caller has the evidence and the decision,
        and ``docs/milestones.md`` §M5 makes escalation (not a guess) the
        response.
    """
    vectors = await embedder.embed([query])
    if not vectors:  # pragma: no cover - an embedder returning nothing is broken
        return RetrievalOutcome(hits=[], abstained=True, top_score=None)

    hits = await store.search(vectors[0], top_k=top_k)
    if not hits:
        return RetrievalOutcome(hits=[], abstained=True, top_score=None)

    top_score = hits[0].score
    return RetrievalOutcome(
        hits=hits,
        abstained=top_score < min_score,
        top_score=top_score,
    )
