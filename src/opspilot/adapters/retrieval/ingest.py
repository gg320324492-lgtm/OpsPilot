"""Knowledge ingestion: Markdown files in, embedded chunks out.

Responsibility: read ``knowledge/*.md``, parse front-matter into a
``knowledge_documents`` row, chunk the body, embed each chunk and upsert both
through the vector store. Re-indexing compares ``content_hash`` so unchanged
documents are skipped.

Layer: ``adapters`` (retrieval). Used by the API's reindex endpoint and by the
seed command.

M0: signatures only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opspilot.adapters.retrieval.embeddings import Embedder
from opspilot.ports.vector_store import VectorStore


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingest run, for the API response and the audit event."""

    documents_seen: int
    documents_indexed: int
    chunks_written: int


async def ingest_directory(path: Path, *, embedder: Embedder, store: VectorStore) -> IngestResult:
    """Ingest every Markdown document under ``path``. M0 stub."""
    raise NotImplementedError


async def ingest_document(path: Path, *, embedder: Embedder, store: VectorStore) -> IngestResult:
    """Ingest one document, replacing its chunks. M0 stub."""
    raise NotImplementedError


def parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split YAML front-matter from a document body. M0 stub."""
    raise NotImplementedError
