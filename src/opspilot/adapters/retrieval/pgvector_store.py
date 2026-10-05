"""``PgVectorStore``: the ``VectorStore`` port on Postgres + pgvector.

Responsibility: store chunk embeddings in the ``embedding vector(1536)`` column
and search them by cosine distance (``<=>``), using an HNSW index. This is the
Phase 1 production retrieval path.

Layer: ``adapters`` (retrieval). Implements
``opspilot.ports.vector_store.VectorStore``.

Index choice note (``docs/data-model.md`` §2): HNSW rather than IVFFlat, because
IVFFlat needs a training pass and a row-estimate to build useful lists, and at
30 documents a few hundred chunks HNSW is both simpler and faster. Recall is not
the Phase 1 bottleneck.
"""

from __future__ import annotations

from opspilot.ports.vector_store import ChunkRecord, SearchHit


class PgVectorStore:
    """Postgres/pgvector-backed ``VectorStore``."""

    def __init__(self, *, database_url: str, dim: int = 1536) -> None:
        self._database_url = database_url
        self._dim = dim

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        """Insert or replace chunks and their pgvector embeddings. M0 stub."""
        raise NotImplementedError

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        """Return the ``top_k`` nearest chunks by cosine distance. M0 stub."""
        raise NotImplementedError
