"""Retrieval adapters: chunking, embeddings, ingest, search, and vector stores.

Responsibility: turn ``knowledge/*.md`` into embedded, searchable chunks and
answer top-k queries, with structural citations. Two vector stores implement one
port: pgvector for Postgres, an in-memory cosine store for the SQLite test path.

Layer: ``adapters``. Implements ``opspilot.ports.vector_store.VectorStore``. See
``docs/architecture.md`` §9.

``opspilot.retrieval`` is a deprecated shim that points here; no code should
import it.
"""

from __future__ import annotations
