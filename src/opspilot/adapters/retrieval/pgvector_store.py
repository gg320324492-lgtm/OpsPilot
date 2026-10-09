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

Session binding follows the house style in
``adapters/persistence/repositories.py``: the store is constructed with a
``Session`` or a ``sessionmaker`` and uses ``db.session_scope`` when it owns the
transaction (``_SessionBound``/``_scope``). It never commits a caller's session.

Score convention: pgvector's ``<=>`` is cosine *distance*, so the score is
``1 - distance``, which is cosine similarity -- exactly the number
``InMemoryVectorStore`` computes. Ordering is by ascending distance, i.e. best
first, with the same ``(distance, document_slug, anchor)`` tie-break the
in-memory store uses on ``(-score, slug, anchor)``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Float, select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.retrieval.memory_store import ChunkEmbeddingMismatch, derive_chunk_id
from opspilot.ports.vector_store import ChunkRecord, SearchHit

type SessionLike = Session | sessionmaker[Session]


class DimensionMismatch(ValueError):
    """The store's dimension does not match the column's ``vector(1536)``.

    A ``ValueError`` so the failure at construction is catchable as the broad
    type, and raised rather than silently behaving as if the column were
    untyped.
    """

    def __init__(self, dim: int, column_dim: int) -> None:
        super().__init__(
            f"PgVectorStore dim={dim} does not match the knowledge_chunks.embedding "
            f"column dimension {column_dim}. The column is declared "
            f"vector({column_dim}) in migrations/versions/0001_initial_schema.py; "
            f"changing EMBEDDING_DIM requires a migration that alters the column, "
            f"which Phase 1 does not have."
        )


class PgVectorStore:
    """Postgres/pgvector-backed ``VectorStore``."""

    #: The dimension the ``knowledge_chunks.embedding`` column is declared at,
    #: kept in sync with ``migrations/versions/0001_initial_schema.py``'s
    #: ``ALTER COLUMN embedding TYPE vector(1536)``. See the class docstring.
    DEFAULT_DIM = 1536

    def __init__(
        self,
        *,
        session_factory: SessionLike,
        slug_lookup: Callable[[UUID], str],
        dim: int = DEFAULT_DIM,
    ) -> None:
        """Bind the store to a session source and a slug resolver.

        Args:
            session_factory: A live ``Session`` (the store then uses it and does
                not commit) or a ``sessionmaker`` (the store opens and commits a
                transaction per call). The same two forms the repository classes
                accept.
            slug_lookup: Resolves a ``document_id`` to its document's slug. The
                store shares this keyword with ``InMemoryVectorStore`` so a
                caller can wire both identically -- see that module's docstring
                for why the slug is resolved here rather than carried on
                ``ChunkRecord``.
            dim: The embedding dimension. Must equal the column's declared
                dimension.

        Raises:
            ValueError: If ``dim`` is not the column's dimension. The column is
                ``vector(1536)``; a store at any other width could never insert
                a row, and pgvector would reject the value deep inside the
                driver. Failing at construction names the mismatch instead.
        """
        if dim != self.DEFAULT_DIM:
            raise DimensionMismatch(dim, self.DEFAULT_DIM)
        self._bound = session_factory
        self._slug_lookup = slug_lookup
        self._dim = dim

    @property
    def dim(self) -> int:
        """The embedding dimension (always the column's dimension)."""
        return self._dim

    @contextmanager
    def _scope(self) -> Iterator[Session]:
        """Yield the store's session, committing only if the store owns it.

        Mirrors ``repositories._SessionBound._scope``: a bound ``Session`` is
        used as-is and never committed (the surrounding transaction owns it);
        a ``sessionmaker`` gets its own committed transaction per call.
        """
        if isinstance(self._bound, Session):
            yield self._bound
            return
        with db.session_scope(self._bound) as session:
            yield session

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        """Insert or replace chunks and their pgvector embeddings.

        Replacement is keyed on ``(document_id, ordinal)`` -- the table's
        ``UNIQUE`` constraint -- via an upsert, so re-ingesting a document
        rewrites its chunks in place and the ``chunk_id`` (derived from the same
        pair) stays stable.
        """
        if len(chunks) != len(embeddings):
            raise ChunkEmbeddingMismatch(len(chunks), len(embeddings))
        if not chunks:
            return

        with self._scope() as session:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                chunk_id = derive_chunk_id(chunk.document_id, chunk.ordinal)
                existing = session.get(models.KnowledgeChunk, chunk_id)
                if existing is None:
                    session.add(
                        models.KnowledgeChunk(
                            id=chunk_id,
                            document_id=chunk.document_id,
                            ordinal=chunk.ordinal,
                            anchor=chunk.anchor,
                            heading_path=chunk.heading_path,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            embedding=list(embedding),
                        )
                    )
                else:
                    existing.document_id = chunk.document_id
                    existing.ordinal = chunk.ordinal
                    existing.anchor = chunk.anchor
                    existing.heading_path = chunk.heading_path
                    existing.content = chunk.content
                    existing.token_count = chunk.token_count
                    existing.embedding = list(embedding)
            session.flush()

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[SearchHit]:
        """Return the ``top_k`` nearest chunks by cosine distance.

        Orders by the ``<=>`` cosine-distance operator ascending (nearest
        first) and reports ``score = 1 - distance``, so the score is cosine
        similarity and matches ``InMemoryVectorStore`` exactly. Within a
        distance, ordering falls back to ``(document_slug, anchor)`` so the
        result is deterministic and the two stores' top-k agree.
        """
        with self._scope() as session:
            # ``return_type=Float`` is load-bearing, not decoration. A ``.op()``
            # binary expression infers its type from its left side, so without it
            # the `d` label inherits the column's `_Vector` type and SQLAlchemy
            # runs pgvector's *result* processor over the distance -- which
            # pgvector parses as a vector literal and crashes on
            # `TypeError: 'float' object is not subscriptable` in
            # `Vector._from_text`. pgvector's own `cosine_distance()` comparator
            # supplies this type; it is unreachable here because `_Vector` is a
            # TypeDecorator over `JSON` and does not carry pgvector's
            # comparator_factory. Declaring the result type is the equivalent.
            distance = models.KnowledgeChunk.embedding.op("<=>", return_type=Float)(query_embedding)
            statement = (
                select(models.KnowledgeChunk, models.KnowledgeDocument.source, distance.label("d"))
                .join(
                    models.KnowledgeDocument,
                    models.KnowledgeDocument.id == models.KnowledgeChunk.document_id,
                )
                .order_by(
                    distance.asc(),
                    models.KnowledgeDocument.source,
                    models.KnowledgeChunk.anchor,
                )
                .limit(top_k)
            )
            rows = session.execute(statement).all()

            hits: list[SearchHit] = []
            for rank, (chunk, _source, dist) in enumerate(rows, start=1):
                hits.append(
                    SearchHit(
                        chunk_id=chunk.id,
                        document_id=chunk.document_id,
                        document_slug=self._slug_lookup(chunk.document_id),
                        anchor=chunk.anchor,
                        content=chunk.content,
                        score=1.0 - float(dist),
                        rank=rank,
                    )
                )
            return hits


__all__ = ["DimensionMismatch", "PgVectorStore"]
