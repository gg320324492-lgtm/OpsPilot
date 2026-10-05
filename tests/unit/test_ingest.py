"""Tests for front-matter parsing and knowledge ingestion.

Written from the specification, not from the implementation:

- ``docs/data-model.md`` §2: ``knowledge_documents`` carries ``title``, a unique
  ``source`` (repo-relative path, e.g. ``refund-policy.md``), ``content``,
  ``doc_metadata`` (the front-matter) and ``content_hash`` (sha256; re-indexing
  compares it).
- ``docs/api-contract.md`` §9: ``POST /api/knowledge/reindex`` is idempotent --
  unchanged documents are re-hashed and skipped, so a second run reports
  ``documents_indexed == 0``.
- The brief: ``README.md`` is excluded by name, and the document's ``source`` is
  the filename, not the title.

The store used here is a real ``SqlKnowledgeDocumentStore`` over an in-memory
SQLite schema, wired to a small recording ``VectorStore``, so the hash-skip path
and the counts are exercised end to end rather than mocked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import SqlKnowledgeDocumentStore
from opspilot.adapters.retrieval.embeddings import LocalDeterministicEmbedder
from opspilot.adapters.retrieval.ingest import (
    FrontMatterError,
    ingest_directory,
    ingest_document,
    parse_front_matter,
)
from opspilot.ports.vector_store import ChunkRecord, SearchHit
from opspilot.settings import Settings

# --------------------------------------------------------------------------- #
# parse_front_matter
# --------------------------------------------------------------------------- #

_WELL_FORMED = """\
---
title: Refund Policy
owner: Finance Operations
effective: 2026-01-15
tags: [billing, refunds, policy]
---

# Refund Policy

Body text.
"""


def test_parse_front_matter_returns_keys_and_body() -> None:
    """The four corpus keys parse, and the body is everything after the block.

    The corpus uses exactly ``title``, ``owner``, ``effective``, ``tags``; the
    values are strings and ``tags`` is the inline list form.
    """
    metadata, body = parse_front_matter(_WELL_FORMED)

    assert metadata == {
        "title": "Refund Policy",
        "owner": "Finance Operations",
        "effective": "2026-01-15",
        "tags": ["billing", "refunds", "policy"],
    }
    assert body.startswith("# Refund Policy")


def test_parse_front_matter_body_has_no_front_matter() -> None:
    """The returned body does not contain the ``---`` block or the title line."""
    _metadata, body = parse_front_matter(_WELL_FORMED)
    assert "---" not in body
    assert "owner:" not in body


def test_parse_front_matter_missing_block_is_an_error() -> None:
    """No front-matter is an error, not a default (the brief says so).

    A defaulted document would be indexed titleless and ownerless and could
    still be cited -- the exact failure the exclusion of ``README.md`` guards.
    """
    with pytest.raises(FrontMatterError):
        parse_front_matter("# Just a heading\n\nNo front-matter here.\n")


def test_parse_front_matter_unterminated_block_is_an_error() -> None:
    """An opening ``---`` with no closing ``---`` fails loudly."""
    with pytest.raises(FrontMatterError):
        parse_front_matter("---\ntitle: Broken\nowner: Nobody\n")


def test_parse_front_matter_rejects_nested_mapping() -> None:
    """A construct outside the supported subset raises rather than guessing."""
    with pytest.raises(FrontMatterError):
        parse_front_matter("---\ntitle: X\nowner:\n  name: Y\n---\n\nBody\n")


def test_parse_front_matter_rejects_block_list() -> None:
    """A ``- item`` block list is not the inline form and must not be misread."""
    with pytest.raises(FrontMatterError):
        parse_front_matter("---\ntitle: X\ntags:\n- billing\n- refunds\n---\n\nBody\n")


def test_parse_front_matter_rejects_duplicate_key() -> None:
    """A duplicated key would silently keep the last value; it raises instead."""
    with pytest.raises(FrontMatterError):
        parse_front_matter("---\ntitle: A\ntitle: B\n---\n\nBody\n")


def test_parse_front_matter_rejects_anchor_construct() -> None:
    """An anchor/alias is a YAML feature this parser does not implement."""
    with pytest.raises(FrontMatterError):
        parse_front_matter("---\ntitle: &a Refund\n---\n\nBody\n")


# --------------------------------------------------------------------------- #
# Ingestion fixtures
# --------------------------------------------------------------------------- #


class RecordingStore:
    """A ``VectorStore`` that records upserts and answers ``content_hash``.

    Implements only the vector-store methods (``upsert``); the document row is
    written by the real ``SqlKnowledgeDocumentStore`` the fixture wires in. The
    two are composed for the test via the ``Facade`` below, which is what an
    ingestion caller in production effectively holds.
    """

    def __init__(self) -> None:
        self.upserted: list[ChunkRecord] = []
        self.embeddings: list[list[float]] = []

    async def upsert(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        self.upserted.extend(chunks)
        self.embeddings.extend(embeddings)

    async def search(
        self,
        query_embedding: list[float],  # noqa: ARG002 -- port signature
        *,
        top_k: int,  # noqa: ARG002 -- port signature
    ) -> list[SearchHit]:
        return []


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def documents(factory: sessionmaker[Session]) -> SqlKnowledgeDocumentStore:
    """The real ``knowledge_documents`` store, as production wires it."""
    return SqlKnowledgeDocumentStore(factory)


@pytest.fixture
def store() -> RecordingStore:
    """The bare ``VectorStore`` ingest is handed -- no document capability.

    Deliberately NOT a facade over the document store. An earlier fixture was
    such a facade, and it hid a real defect: ``ingest`` looked for
    ``content_hash`` on the *vector* store, which never has one, so the
    content-hash skip never fired in production while the facade made the test
    pass. Passing the same two objects production passes is what makes this
    suite able to fail.
    """
    return RecordingStore()


@pytest.fixture
def embedder() -> LocalDeterministicEmbedder:
    return LocalDeterministicEmbedder(dim=8)


def _write(directory: Path, name: str, text: str) -> Path:
    """Write a document into ``directory`` and return its path."""
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


_DOC_A = """\
---
title: Refund Policy
owner: Finance Operations
effective: 2026-01-15
tags: [billing, refunds]
---

# Refund Policy

## Limits

Refunds above $100 need approval.
"""

_DOC_B = """\
---
title: Duplicate Charge SOP
owner: Billing Operations
effective: 2026-02-01
tags: [billing, sop]
---

# Duplicate Charge Handling

## Detection

Two transactions with the same invoice id are a candidate duplicate.
"""


# --------------------------------------------------------------------------- #
# ingest_document
# --------------------------------------------------------------------------- #


async def test_ingest_document_uses_filename_as_source(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """``source`` is the filename, not the title.

    The brief and ``docs/data-model.md`` §2: the slug is the file name
    (``refund-policy.md``), and the citation is ``"{document_slug}#{anchor}"``.
    The title is "Refund Policy"; the source must not be.
    """
    path = _write(tmp_path, "refund-policy.md", _DOC_A)
    await ingest_document(path, embedder=embedder, store=store, documents=documents)

    rows = await documents.list_documents()
    assert [row.source for row in rows] == ["refund-policy.md"]
    assert rows[0].title == "Refund Policy"


async def test_ingest_document_metadata_carries_front_matter(
    tmp_path: Path,
    factory: sessionmaker[Session],
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """The parsed front-matter is the document's ``doc_metadata``."""
    path = _write(tmp_path, "refund-policy.md", _DOC_A)
    await ingest_document(path, embedder=embedder, store=store, documents=documents)

    with db.session_scope(factory) as session:
        row = session.execute(select(models.KnowledgeDocument)).scalars().one()
        assert row.doc_metadata["owner"] == "Finance Operations"
        assert row.doc_metadata["tags"] == ["billing", "refunds"]


async def test_ingest_document_writes_one_record_per_chunk(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """A body with two headings yields chunks, and each is embedded once."""
    path = _write(tmp_path, "refund-policy.md", _DOC_A)
    result = await ingest_document(path, embedder=embedder, store=store, documents=documents)

    assert result.documents_seen == 1
    assert result.documents_indexed == 1
    assert result.chunks_written == len(store.upserted)
    assert result.chunks_written > 0
    assert len(store.embeddings) == result.chunks_written


async def test_ingest_document_requires_a_title(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """Front-matter without ``title`` fails naming the file.

    ``title`` is ``NOT NULL`` on ``knowledge_documents`` (data-model §2); a
    missing one must be a content error that names the document, not a driver
    error with no context.
    """
    path = _write(tmp_path, "no-title.md", "---\nowner: X\ntags: [a]\n---\n\n# H\n\nbody\n")
    with pytest.raises(FrontMatterError) as excinfo:
        await ingest_document(path, embedder=embedder, store=store, documents=documents)
    assert "no-title.md" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# content_hash idempotency (api-contract §9)
# --------------------------------------------------------------------------- #


async def test_reingesting_unchanged_document_indexes_nothing(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """Re-ingest reports ``documents_indexed == 0`` and writes no chunks.

    ``docs/api-contract.md`` §9: "Idempotent -- documents are re-hashed and
    unchanged ones are skipped. Returns counts." The second run must write
    nothing at all, not merely report zero.
    """
    path = _write(tmp_path, "refund-policy.md", _DOC_A)
    first = await ingest_document(path, embedder=embedder, store=store, documents=documents)
    written_after_first = len(store.upserted)

    second = await ingest_document(path, embedder=embedder, store=store, documents=documents)

    assert first.documents_indexed == 1
    assert second.documents_indexed == 0
    assert second.chunks_written == 0
    assert len(store.upserted) == written_after_first


async def test_changed_document_is_reindexed(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """A changed body changes the hash, so the document is re-indexed.

    The changed document replaces its chunks: the store's upsert keys on
    ``(document_id, ordinal)``, so the second write is not a growing pile.
    """
    path = _write(tmp_path, "refund-policy.md", _DOC_A)
    await ingest_document(path, embedder=embedder, store=store, documents=documents)

    changed = _DOC_A.replace("$100 need approval", "$250 need approval")
    _write(tmp_path, "refund-policy.md", changed)
    result = await ingest_document(path, embedder=embedder, store=store, documents=documents)

    assert result.documents_indexed == 1
    # The document row is updated in place -- still exactly one document.
    rows = await documents.list_documents()
    assert [row.source for row in rows] == ["refund-policy.md"]


# --------------------------------------------------------------------------- #
# ingest_directory
# --------------------------------------------------------------------------- #


async def test_ingest_directory_skips_readme(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """``README.md`` is excluded by name.

    The brief: ``knowledge/README.md`` describes the corpus and has no
    front-matter; indexing it would put a titleless, ownerless policy document
    into the corpus where retrieval could cite it. It is excluded explicitly.
    """
    _write(tmp_path, "README.md", "# Knowledge Base\n\nNot a policy document.\n")
    _write(tmp_path, "refund-policy.md", _DOC_A)
    _write(tmp_path, "duplicate-charge-sop.md", _DOC_B)

    result = await ingest_directory(tmp_path, embedder=embedder, store=store, documents=documents)

    assert result.documents_seen == 2
    rows = await documents.list_documents()
    assert "README.md" not in {row.source for row in rows}


async def test_ingest_directory_counts_unchanged_run_as_zero(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """Ingesting the same tree twice reports ``documents_indexed == 0`` the second.

    This is the exact counts contract: ``documents_seen`` stays 2,
    ``documents_indexed`` falls to 0, ``chunks_written`` falls to 0.
    """
    _write(tmp_path, "refund-policy.md", _DOC_A)
    _write(tmp_path, "duplicate-charge-sop.md", _DOC_B)

    first = await ingest_directory(tmp_path, embedder=embedder, store=store, documents=documents)
    second = await ingest_directory(tmp_path, embedder=embedder, store=store, documents=documents)

    assert first.documents_seen == 2
    assert first.documents_indexed == 2
    assert first.chunks_written > 0
    assert second.documents_seen == 2
    assert second.documents_indexed == 0
    assert second.chunks_written == 0


async def test_ingest_directory_is_deterministic_in_order(
    tmp_path: Path,
    store: RecordingStore,
    embedder: LocalDeterministicEmbedder,
    documents: SqlKnowledgeDocumentStore,
) -> None:
    """Files are processed in sorted order, so chunk order is stable."""
    _write(tmp_path, "zebra.md", _DOC_B)
    _write(tmp_path, "alpha.md", _DOC_A)

    await ingest_directory(tmp_path, embedder=embedder, store=store, documents=documents)
    rows = await documents.list_documents()
    assert [row.source for row in rows] == ["alpha.md", "zebra.md"]


def test_ingest_result_shape_carries_three_counts() -> None:
    """``IngestResult`` is the shape the reindex endpoint reads.

    ``KnowledgeReindexResponse`` reads ``documents_seen``, ``documents_indexed``
    and ``chunks_written`` (``knowledge.py``).
    """
    from opspilot.adapters.retrieval.ingest import IngestResult

    result = IngestResult(documents_seen=3, documents_indexed=1, chunks_written=7)
    assert (result.documents_seen, result.documents_indexed, result.chunks_written) == (3, 1, 7)
