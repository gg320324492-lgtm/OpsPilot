"""The ``VectorStore`` port.

Responsibility: the retrieval boundary. ``search`` returns ``(document,
chunk_id, score)`` triples so citations are *structural* -- the UI renders
"Sources: refund-policy.md" from ids, never by parsing the model's sentence --
and the eval's Recall@K metric is a query over the same data.

Layer: ``ports``. Imports only ``typing`` and Pydantic.

Two implementations share this interface without ``retrieval/search.py``
changing: ``pgvector_store`` for Postgres and ``memory_store`` for the SQLite
test path (in-memory cosine over a small fixture set). See ``docs/architecture.md``
§9.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel


class ChunkRecord(BaseModel):
    """A chunk to be embedded and stored."""

    document_id: UUID
    ordinal: int
    anchor: str
    heading_path: str
    content: str
    token_count: int


class SearchHit(BaseModel):
    """One retrieval result: the chunk, its document, and the cosine similarity."""

    chunk_id: UUID
    document_id: UUID
    document_slug: str
    anchor: str
    content: str
    score: float
    rank: int


@runtime_checkable
class VectorStore(Protocol):
    """Stores embeddings and searches them by cosine similarity."""

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        """Insert or replace chunks and their embeddings."""
        ...

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        """Return the ``top_k`` nearest chunks, best first.

        A caller that finds the top score below the configured threshold
        abstains and escalates rather than answering from weak evidence
        (``RETRIEVAL_MIN_SCORE``).
        """
        ...
