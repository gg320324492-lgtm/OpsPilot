"""Retrieval composition root: settings in, wired collaborators out.

Responsibility: build the one retrieval stack (embedder, vector store, the
``RetrievalCallable`` the runtime accepts, the ``KnowledgeTool`` that dispatches
``knowledge.search``, and the reindex runner the API exposes) from
:class:`opspilot.settings.Settings`. It is the single place that decides *which*
``VectorStore`` the deployment gets, so neither the worker nor the API repeats
that choice.

Layer: ``adapters`` (composition). It imports the retrieval adapters, the
persistence session factory, and the ports -- and it is imported *only* by the
process entry points (``worker``, ``api``), never by ``agents`` or ``domain``.

Why SQLite gets the in-memory store
------------------------------------

SQLite has no ``vector(1536)`` column (ADR-0004), so the on-disk database cannot
hold embeddings. When ``DATABASE_URL`` names SQLite the vector store is
``InMemoryVectorStore`` -- an in-process cosine store -- and the ``knowledge``
document/chunk rows still live in the SQLite database behind
``SqlKnowledgeDocumentStore``. That split is why the citation rows and the
``GET /api/knowledge`` listing work under SQLite even though the vectors do not:
the *table* is real in both, only the embedding column differs.

The differential test (``tests/integration/test_citations.py``) asserts the two
vector stores agree, which is what makes swapping them here safe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from opspilot.adapters.retrieval.embeddings import Embedder, build_embedder
from opspilot.adapters.retrieval.ingest import ingest_directory
from opspilot.adapters.retrieval.memory_store import InMemoryVectorStore
from opspilot.adapters.retrieval.search import RetrievalOutcome, retrieve
from opspilot.adapters.tools.knowledge_tool import KnowledgeTool
from opspilot.ports.stores import CitationStore, KnowledgeDocumentStore
from opspilot.ports.vector_store import SearchHit, VectorStore
from opspilot.settings import Settings

__all__ = [
    "RetrievalStack",
    "build_citation_store",
    "build_retrieval_callable",
    "build_retrieval_stack",
]


def _slug_lookup_for(session_factory: object) -> Callable[[UUID], str]:
    """Build a synchronous ``document_id`` -> ``source`` resolver over a session.

    The vector stores resolve a chunk's document slug through this callable
    because ``ChunkRecord`` carries no slug (``memory_store``'s module docstring
    explains why). The lookup opens a short session and reads the ``source``
    column; a document that is absent -- which would mean a chunk row whose
    document row was deleted -- returns the empty string rather than raising, so
    a search degrades to an unnamed citation instead of a 500.
    """
    from opspilot.adapters.persistence import models

    def _lookup(document_id: UUID) -> str:
        with session_factory() as session:  # type: ignore[operator]
            source = session.execute(
                select(models.KnowledgeDocument.source).where(
                    models.KnowledgeDocument.id == document_id
                )
            ).scalar()
        return source or ""

    return _lookup


@dataclass(frozen=True)
class RetrievalStack:
    """The wired pieces one process needs for the retrieval path.

    Attributes:
        retrieval: The ``(query) -> list[SearchHit]`` callable the runtime's
            ``run_loop`` accepts. Binds ``top_k`` and ``min_score`` from settings
            behind the published ``RetrievalCallable`` shape.
        knowledge_tool: The ``ToolGateway`` that dispatches ``knowledge.search``
            in-process (``docs/mcp-contracts.md`` §4) for a caller that wants it
            as a gateway -- the runtime itself does not go through it, because
            retrieval feeds prompt assembly, not the action step.
        reindex_runner: The callable ``POST /api/knowledge/reindex`` invokes. It
            runs ``ingest_directory`` and returns its ``IngestResult``; unchanged
            documents are skipped by content hash, so it is idempotent
            (``docs/api-contract.md`` §9).
        knowledge_store: The ``KnowledgeDocumentStore`` ``GET /api/knowledge``
            reads.
        embedder: The configured embedder. Carried so a caller can query the same
            vector space ``retrieval`` uses (a test, a diagnostic) without
            rebuilding it.
        store: The configured vector store. Carried for the same reason.
    """

    retrieval: Callable[[str], Awaitable[list[SearchHit]]]
    knowledge_tool: KnowledgeTool
    reindex_runner: Callable[[Path], Awaitable[object]]
    knowledge_store: KnowledgeDocumentStore
    embedder: Embedder
    store: VectorStore


def build_retrieval_callable(
    *,
    embedder: Embedder,
    store: VectorStore,
    top_k: int,
    min_score: float,
) -> Callable[[str], Awaitable[list[SearchHit]]]:
    """Build the runtime's ``RetrievalCallable`` from a configured backend.

    The returned callable has the published ``(query) -> list[SearchHit]`` shape
    (``agents/runtime.py``); ``top_k`` and ``min_score`` are bound here so the
    runtime does not need them. It returns ``outcome.hits`` regardless of
    ``outcome.abstained`` -- the runtime applies the threshold itself from its own
    ``retrieval_min_score``, and the hits carry their scores, so the decision
    stays with the caller that owns the context.
    """

    async def _retrieve(query: str) -> list[SearchHit]:
        outcome: RetrievalOutcome = await retrieve(
            query, embedder=embedder, store=store, top_k=top_k, min_score=min_score
        )
        return outcome.hits

    return _retrieve


def build_retrieval_stack(
    settings: Settings,
    *,
    session_factory: object | None = None,
) -> RetrievalStack:
    """Build the whole retrieval path from ``settings``.

    Chooses the embedder and the vector store from config, wires the retriever
    into both the runtime callable and the ``KnowledgeTool``, and returns a
    reindex runner that ingests a directory through the same embedder and store.

    Args:
        settings: The process configuration (embedding provider, dimensions,
            top-k, min-score, database URL).
        session_factory: An optional SQLAlchemy ``sessionmaker`` to share with the
            caller. ``None`` (the production path) builds one from
            ``settings.database_url``. A test passes its own so the retrieval
            stack reads the same in-memory schema it created -- two factories
            over ``:memory:`` would otherwise open two different databases.
    """
    from opspilot.adapters.persistence import db
    from opspilot.adapters.persistence.repositories import SqlKnowledgeDocumentStore

    factory = session_factory if session_factory is not None else db.session_factory(settings)
    documents = SqlKnowledgeDocumentStore(factory)  # type: ignore[arg-type]

    embedder = build_embedder(
        settings.embedding_provider,
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
    store = _build_vector_store(settings, factory, documents)

    retrieval = build_retrieval_callable(
        embedder=embedder,
        store=store,
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
    )

    # The knowledge tool's retriever is the same shared search, with ``top_k``
    # bound to the setting's value but overridable by the call's argument -- so a
    # model that passes ``top_k`` gets what it asked for, bounded, and a model
    # that omits it gets the configured default.
    async def _retriever(query: str, top_k: int) -> RetrievalOutcome:
        return await retrieve(
            query,
            embedder=embedder,
            store=store,
            top_k=top_k,
            min_score=settings.retrieval_min_score,
        )

    async def _reindex(path: Path) -> object:
        return await ingest_directory(path, embedder=embedder, store=store, documents=documents)

    return RetrievalStack(
        retrieval=retrieval,
        knowledge_tool=KnowledgeTool(retriever=_retriever),
        reindex_runner=_reindex,
        knowledge_store=documents,
        embedder=embedder,
        store=store,
    )


def _build_vector_store(
    settings: Settings,
    session_factory: object,
    documents: KnowledgeDocumentStore,
) -> VectorStore:
    """Choose the ``VectorStore`` the deployment gets.

    SQLite cannot store ``vector(1536)`` (ADR-0004), so it gets the in-memory
    cosine store; Postgres gets ``PgVectorStore``. Both take the same
    ``slug_lookup`` keyword, which is what lets this be a one-line swap rather
    than a branch above the port.
    """
    _ = documents
    slug_lookup = _slug_lookup_for(session_factory)
    if settings.is_sqlite:
        return InMemoryVectorStore(slug_lookup=slug_lookup)

    from opspilot.adapters.retrieval.pgvector_store import PgVectorStore

    return PgVectorStore(
        session_factory=session_factory,  # type: ignore[arg-type]
        slug_lookup=slug_lookup,
        dim=settings.embedding_dim,
    )


def build_citation_store(session_factory: object) -> CitationStore:
    """Build the ``CitationStore`` the runtime persists a run's hits into."""
    from opspilot.adapters.persistence.repositories import SqlCitationStore

    return SqlCitationStore(session_factory)  # type: ignore[arg-type]
