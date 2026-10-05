"""Knowledge routes.

Responsibility: trigger (re)indexing of ``knowledge/*.md`` and expose what is
indexed. Reindexing compares ``content_hash`` per document and replaces only
what changed.

Layer: ``api`` (router).

Note: ``knowledge.search`` is *also* a tool the agent calls through the tool
gateway, because retrieval is something the model may request. This router is
the operator-facing surface for ingestion, not the agent's retrieval path.
"""

from __future__ import annotations

from opspilot.api.schemas import KnowledgeReindexResponse


async def reindex() -> KnowledgeReindexResponse:
    """Re-ingest the knowledge directory. M0 stub."""
    raise NotImplementedError


async def list_documents() -> list[dict[str, object]]:
    """List indexed knowledge documents. M0 stub."""
    raise NotImplementedError
