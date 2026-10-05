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
    """Embed ``query`` and return the top-k hits, or abstain. M0 stub."""
    raise NotImplementedError
