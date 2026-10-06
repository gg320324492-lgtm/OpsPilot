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

What is stored, and where
-------------------------

Vectors stay **in process memory** -- that is the point, since the SQLite
database has no vector column to put them in. Everything *else* a chunk owns --
its ordinal, anchor, heading path, content and token count -- is also written to
the real ``knowledge_chunks`` table, with the ``embedding`` column left ``NULL``.

That split is what makes the SQLite configuration behave like a deployment:

- ``citations.chunk_id`` is a foreign key onto ``knowledge_chunks.id``, and
  ``SqlCitationStore.create_many`` skips a hit whose chunk row is missing. With
  no rows written, an ingest produced 17 ``knowledge_documents`` rows and **0**
  ``knowledge_chunks`` rows, so a citation had nothing to point at and the
  golden path's Sources panel was empty for a reason that had nothing to do
  with retrieval quality (``docs/progress.md`` M5e, Finding F2).
- ``GET /api/knowledge`` derives ``chunk_count`` by joining that table, so it
  reported ``0`` for every indexed document.

Persisting the rows is therefore a **behaviour** fix, not a bookkeeping one.
The retrieval *results* are unchanged -- search still reads the in-process list,
still computes the same cosine, still returns the same ids and scores -- so the
differential test against ``PgVectorStore`` is unaffected: it compares what the
two stores *return*, and what is written to the table is not part of that.

The session is optional (``session_factory=None`` keeps the store a pure
in-memory double, which is how the differential test and the store unit tests
construct it). Wiring passes one, which is what a deployment does.

Two decisions this store makes, and the same ones ``PgVectorStore`` makes, so
the two agree exactly (the differential test in ``docs/milestones.md`` §M5):

- **Where the document slug comes from.** ``ChunkRecord`` carries
  ``document_id``, not the slug, and the citation is
  ``"{document_slug}#{anchor}"``. The store is therefore constructed with a
  ``slug_lookup`` callable that resolves an id to its document's ``source``.
  The alternative -- widening ``ChunkRecord`` in ``ports`` -- would put a
  denormalised field on the port and force every caller to supply it; the
  lookup keeps the port describing what is stored and lets the caller decide
  how to resolve the slug. ``PgVectorStore`` takes the same keyword.
- **How ``chunk_id`` is derived.** ``ChunkRecord`` has no id, and
  ``SearchHit`` needs one for the ``citations`` table's ``chunk_id`` FK. The id
  is derived deterministically from ``(document_id, ordinal)`` via
  :func:`derive_chunk_id`, which is what makes upsert a genuine replacement and
  what lets both stores agree on ids. A random id per call would make the same
  chunk a different row on every reindex.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from opspilot.ports.vector_store import ChunkRecord, SearchHit

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.orm import Session, sessionmaker

    from opspilot.adapters.persistence.models import KnowledgeChunk

# A fixed namespace UUID for chunk ids. Never change it: stored references
# (``citations.chunk_id``) point at ids derived from it.
_CHUNK_ID_NAMESPACE = UUID("6f60f1c1-3b0b-4d0a-9c1e-3e5a0f9b7c21")


def derive_chunk_id(document_id: UUID, ordinal: int) -> UUID:
    """Derive a chunk id deterministically from ``(document_id, ordinal)``.

    Both vector stores use this so the same chunk has the same id in either, and
    so a re-ingest replaces the row rather than creating a new one. The pair is
    the table's own uniqueness constraint (``UNIQUE (document_id, ordinal)`` --
    ``docs/data-model.md`` §2), so the derivation cannot collide within a
    document.
    """
    return uuid5(_CHUNK_ID_NAMESPACE, f"{document_id}:{ordinal}")


class ChunkEmbeddingMismatch(ValueError):
    """``upsert`` was given a different number of chunks and embeddings.

    Shared by both stores so the failure is the same shape in either. A
    ``ValueError`` because it is a caller error, and the caller may catch the
    broad type.
    """

    def __init__(self, chunks: int, embeddings: int) -> None:
        super().__init__(
            f"chunks and embeddings must be the same length "
            f"({chunks} chunks, {embeddings} embeddings)"
        )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    A zero-magnitude input yields ``0.0`` rather than a ``ZeroDivisionError``;
    the local embedder never produces one, but a caller-supplied query vector
    might, and a search should return no strong match rather than raise.
    """
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class InMemoryVectorStore:
    """An in-process cosine-similarity ``VectorStore`` for the SQLite path.

    Constructed with a ``slug_lookup`` that resolves a ``document_id`` to the
    document's slug -- see the module docstring for why the slug is resolved by
    the store rather than carried on ``ChunkRecord``.

    ``session_factory`` is optional. Given one (which is what
    :func:`opspilot.adapters.wiring.build_retrieval_stack` does), ``upsert``
    also writes the chunk's metadata to ``knowledge_chunks`` so the citation
    and knowledge-listing queries have rows to read. Without one the store keeps
    chunks purely in memory, which is what the differential test and the unit
    tests want: a double with no database attached.
    """

    def __init__(
        self,
        *,
        slug_lookup: Callable[[UUID], str],
        session_factory: Session | sessionmaker[Session] | None = None,
    ) -> None:
        self._slug_lookup = slug_lookup
        self._session_factory = session_factory
        self._chunks: list[ChunkRecord] = []
        self._embeddings: list[list[float]] = []

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        """Store chunks and embeddings in memory, replacing by ``(document_id, ordinal)``.

        A chunk whose ``(document_id, ordinal)`` already exists replaces the
        stored row's embedding and content in place, so re-ingesting a document
        does not grow the store.

        When a ``session_factory`` was supplied the chunk's metadata is also
        written to ``knowledge_chunks`` under the same ``derive_chunk_id`` the
        hit reports, keyed the same way ``PgVectorStore`` keys it -- so the row
        a citation points at is the row retrieval came from. The ``embedding``
        column is left ``NULL``: SQLite has no vector column (ADR-0004) and the
        vectors live in this process.
        """
        if len(chunks) != len(embeddings):
            raise ChunkEmbeddingMismatch(len(chunks), len(embeddings))
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            key = (chunk.document_id, chunk.ordinal)
            index = next(
                (
                    i
                    for i, existing in enumerate(self._chunks)
                    if (existing.document_id, existing.ordinal) == key
                ),
                None,
            )
            if index is None:
                self._chunks.append(chunk)
                self._embeddings.append(list(embedding))
            else:
                self._chunks[index] = chunk
                self._embeddings[index] = list(embedding)

        self._persist_chunks(chunks)

    def _persist_chunks(self, chunks: list[ChunkRecord]) -> None:
        """Write chunk metadata rows, or do nothing without a session factory.

        Follows the house session-binding rule (``repositories._SessionBound``):
        a bound ``Session`` is used as-is and never committed, while a
        ``sessionmaker`` gets its own committed transaction per call. That is
        the same rule ``PgVectorStore`` follows, which is what lets the two
        stores be swapped without the caller changing how it manages
        transactions.
        """
        if self._session_factory is None or not chunks:
            return

        from sqlalchemy.orm import Session

        from opspilot.adapters.persistence import db
        from opspilot.adapters.persistence.models import KnowledgeChunk

        bound = self._session_factory
        if isinstance(bound, Session):
            self._write_rows(KnowledgeChunk, bound, chunks)
            return
        with db.session_scope(bound) as session:
            self._write_rows(KnowledgeChunk, session, chunks)

    def _write_rows(
        self,
        chunk_model: type[KnowledgeChunk],
        session: Session,
        chunks: list[ChunkRecord],
    ) -> None:
        """Insert or update one ``knowledge_chunks`` row per chunk.

        Replacement is keyed on the primary key, exactly as
        ``PgVectorStore.upsert`` does, so re-ingesting a document rewrites its
        rows in place and the ids -- which ``citations`` points at -- stay
        stable across a reindex.
        """
        for chunk in chunks:
            chunk_id = derive_chunk_id(chunk.document_id, chunk.ordinal)
            existing = session.get(chunk_model, chunk_id)
            if existing is None:
                session.add(
                    chunk_model(
                        id=chunk_id,
                        document_id=chunk.document_id,
                        ordinal=chunk.ordinal,
                        anchor=chunk.anchor,
                        heading_path=chunk.heading_path,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding=None,
                    )
                )
            else:
                existing.document_id = chunk.document_id
                existing.ordinal = chunk.ordinal
                existing.anchor = chunk.anchor
                existing.heading_path = chunk.heading_path
                existing.content = chunk.content
                existing.token_count = chunk.token_count
        session.flush()

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        """Return the ``top_k`` nearest chunks by cosine similarity.

        Sorted by score descending; ties break on ``(-score, document_slug,
        anchor)`` so the result is deterministic. That tie-break is not
        cosmetic: without it, two chunks with equal scores could be returned in
        either order and the differential test against pgvector would flake.
        ``rank`` starts at 1 with the best hit first.
        """
        scored: list[tuple[float, str, str, int]] = []
        for index, chunk in enumerate(self._chunks):
            score = _cosine_similarity(query_embedding, self._embeddings[index])
            scored.append((score, self._slug_lookup(chunk.document_id), chunk.anchor, index))

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))

        hits: list[SearchHit] = []
        for rank, (score, slug, _anchor, index) in enumerate(scored[:top_k], start=1):
            chunk = self._chunks[index]
            hits.append(
                SearchHit(
                    chunk_id=derive_chunk_id(chunk.document_id, chunk.ordinal),
                    document_id=chunk.document_id,
                    document_slug=slug,
                    anchor=chunk.anchor,
                    content=chunk.content,
                    score=score,
                    rank=rank,
                )
            )
        return hits


__all__ = ["ChunkEmbeddingMismatch", "InMemoryVectorStore", "derive_chunk_id"]
