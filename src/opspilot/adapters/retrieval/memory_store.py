"""``InMemoryVectorStore``: cosine similarity in Python for the SQLite test path.

Responsibility: implement the ``VectorStore`` port without pgvector, so the
whole suite -- including retrieval -- runs on SQLite in-memory with no Postgres.
It computes cosine similarity over a small in-process list.

Layer: ``adapters`` (retrieval). Implements
``opspilot.ports.vector_store.VectorStore``.

Why this exists (``docs/architecture.md`` §9, ``docs/data-model.md`` §6): SQLite
cannot store ``vector(1536)``, and the test configuration must not require a
database service. Because this implements the same port as ``PgVectorStore``,
``retrieval/search.py`` and everything above it is unchanged between the two.
It is a test/CI implementation -- not a scaling story.
"""

from __future__ import annotations

from opspilot.ports.vector_store import ChunkRecord, SearchHit


class InMemoryVectorStore:
    """An in-process cosine-similarity ``VectorStore`` for tests."""

    def __init__(self) -> None:
        self._chunks: list[ChunkRecord] = []
        self._embeddings: list[list[float]] = []

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        """Store chunks and embeddings in memory, replacing by ``(document_id, ordinal)``.

        M0 stub.
        """
        raise NotImplementedError

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        """Return the ``top_k`` nearest chunks by cosine similarity. M0 stub."""
        raise NotImplementedError
