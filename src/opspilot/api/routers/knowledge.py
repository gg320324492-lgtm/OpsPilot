"""Knowledge routes.

Responsibility: trigger (re)indexing of ``knowledge/*.md`` and expose what is
indexed. Reindexing compares ``content_hash`` per document and replaces only
what changed.

Layer: ``api`` (router).

Note: ``knowledge.search`` is *also* a tool the agent calls through the tool
gateway, because retrieval is something the model may request. This router is
the operator-facing surface for ingestion, not the agent's retrieval path.

Idempotency (contract §9): re-hashing means an unchanged tree reports
``documents_indexed == 0`` on the second call, so a retried reindex is safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from opspilot.api.auth import require_operator
from opspilot.api.schemas import (
    KnowledgeDocumentSummary,
    KnowledgeListResponse,
    KnowledgeReindexResponse,
)

router = APIRouter(prefix="/api", tags=["knowledge"], dependencies=[Depends(require_operator)])

# ``knowledge/`` sits beside ``src/`` in the repository root. Resolved from this
# file rather than from the process CWD, so the endpoint works under uvicorn from
# any directory.
_KNOWLEDGE_DIR = Path(__file__).resolve().parents[4] / "knowledge"


@router.get("/knowledge", response_model=KnowledgeListResponse)
async def list_documents(request: Request) -> KnowledgeListResponse:
    """List indexed documents and their chunk counts (contract §1).

    Reads through the optional ``knowledge_store`` bound on ``app.state`` when the
    app factory provides one; otherwise returns an empty page rather than a 500,
    because the operator surface should not be the thing that takes the API down.
    """
    store = getattr(request.app.state, "knowledge_store", None)
    if store is None:
        return KnowledgeListResponse(items=[], total=0)
    rows = await store.list_documents()
    items = [
        KnowledgeDocumentSummary(
            source=row.source,
            title=row.title,
            chunk_count=int(getattr(row, "chunk_count", 0)),
            indexed_at=row.indexed_at,
            content_hash=row.content_hash,
        )
        for row in rows
    ]
    return KnowledgeListResponse(items=items, total=len(items))


@router.post("/knowledge/reindex", response_model=KnowledgeReindexResponse)
async def reindex(request: Request) -> KnowledgeReindexResponse:
    """Re-ingest ``knowledge/`` -- idempotent, returns counts (contract §9).

    The work goes through an ingestion callable bound on ``app.state`` when the
    app factory has one (it holds the embedder and the vector store, which are the
    expensive and deployment-specific pieces). When nothing is bound, the endpoint
    reports the documents it can see with zero indexed, rather than pretending a
    reindex happened.
    """
    runner: Any = getattr(request.app.state, "reindex_runner", None)
    if runner is None:
        seen = len(list(_KNOWLEDGE_DIR.glob("*.md"))) if _KNOWLEDGE_DIR.is_dir() else 0
        return KnowledgeReindexResponse(documents_seen=seen, documents_indexed=0, chunks_written=0)
    result = await runner(_KNOWLEDGE_DIR)
    return KnowledgeReindexResponse(
        documents_seen=int(result.documents_seen),
        documents_indexed=int(result.documents_indexed),
        chunks_written=int(result.chunks_written),
    )
