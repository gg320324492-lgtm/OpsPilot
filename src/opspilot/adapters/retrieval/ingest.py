"""Knowledge ingestion: Markdown files in, embedded chunks out.

Responsibility: read ``knowledge/*.md``, parse front-matter into a
``knowledge_documents`` row, chunk the body, embed each chunk and upsert both
through the vector store. Re-indexing compares ``content_hash`` so unchanged
documents are skipped.

Layer: ``adapters`` (retrieval). Used by the API's reindex endpoint and by the
seed command.

Two decisions that are load-bearing rather than incidental:

- **``README.md`` is excluded by name.** ``knowledge/README.md`` describes the
  corpus; it is not part of it, and it has no front-matter. Indexing it would
  put a titleless, ownerless "policy document" into the corpus where retrieval
  could cite it. It is skipped explicitly (``_EXCLUDED_FILENAMES``), not by a
  pattern that happens to miss it.
- **The document's ``source`` is the filename** (``refund-policy.md``), not the
  title. ``evals/datasets/retrieval.jsonl`` names documents by source, and the
  citation string is ``"{document_slug}#{anchor}"`` (``docs/api-contract.md``
  §3), so the slug must be the filename a reader can grep for in ``knowledge/``.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from opspilot.adapters.retrieval.chunking import split_document
from opspilot.adapters.retrieval.embeddings import Embedder
from opspilot.ports.stores import KnowledgeDocumentStore
from opspilot.ports.vector_store import ChunkRecord, VectorStore

# ``README.md`` describes the corpus and carries no front-matter; it is not a
# policy document. Excluded by name so a future README in a subdirectory is not
# caught, and so the exclusion is a decision a reader can see rather than a
# glob that happens to miss it.
_EXCLUDED_FILENAMES = frozenset({"README.md"})

# The front-matter block is delimited by a line that is exactly ``---``. The
# parser does not support ``...`` as an end marker: the corpus does not use it
# and inventing support would be a silently-different interpretation of a file
# it cannot fully parse.
_FRONT_MATTER_DELIMITER = "---"


class FrontMatterError(ValueError):
    """A document's front-matter could not be parsed.

    A ``ValueError`` because the file is malformed input, and named so a caller
    can tell a content error from an I/O error. The message names the file when
    the caller knows it, so a corpus-wide ingest says *which* document is wrong
    rather than failing anonymously.

    The message is built by the named constructors below rather than at each
    call site, so the wording of each failure lives with the error type.
    """

    @classmethod
    def no_front_matter(cls) -> FrontMatterError:
        """The document does not begin with a ``---`` block."""
        return cls("document has no front-matter: the first line must be '---'")

    @classmethod
    def unterminated(cls) -> FrontMatterError:
        """The opening ``---`` was never closed."""
        return cls("document front-matter is not terminated by a closing '---'")

    @classmethod
    def empty_list_item(cls, key: str) -> FrontMatterError:
        """An inline list contains an empty element."""
        return cls(f"front-matter key {key!r} has an empty list item")

    @classmethod
    def unbalanced_list(cls, key: str, value: str) -> FrontMatterError:
        """A value opens or closes ``[``/``]`` without its pair."""
        return cls(f"front-matter key {key!r} has an unbalanced inline list: {value!r}")

    @classmethod
    def unsupported_construct(cls, key: str, value: str) -> FrontMatterError:
        """A value uses a YAML construct this parser does not implement."""
        return cls(
            f"front-matter key {key!r} uses a YAML construct this parser does not "
            f"support (nested maps, anchors, block scalars): {value!r}"
        )

    @classmethod
    def indented_line(cls, line: str) -> FrontMatterError:
        """A line is indented, implying a nested structure."""
        return cls(f"front-matter line is indented; nested structures are not supported: {line!r}")

    @classmethod
    def block_list(cls, line: str) -> FrontMatterError:
        """A ``- item`` block list, which is not the supported inline form."""
        return cls(f"front-matter block lists are not supported: {line!r}")

    @classmethod
    def not_key_value(cls, line: str) -> FrontMatterError:
        """A line has no ``:`` separating key from value."""
        return cls(f"front-matter line is not ``key: value``: {line!r}")

    @classmethod
    def empty_key(cls, line: str) -> FrontMatterError:
        """A line has a ``:`` but no key before it."""
        return cls(f"front-matter line has an empty key: {line!r}")

    @classmethod
    def duplicate_key(cls, key: str) -> FrontMatterError:
        """A key appears more than once."""
        return cls(f"front-matter key {key!r} is declared more than once")

    @classmethod
    def missing_title(cls, source: str) -> FrontMatterError:
        """The front-matter has no usable ``title``."""
        return cls(f"{source}: front-matter is missing a non-empty 'title'")

    @classmethod
    def in_file(cls, source: str, cause: FrontMatterError) -> FrontMatterError:
        """A parse failure, prefixed with the file it came from."""
        return cls(f"{source}: {cause}")


def _split_inline_list(value: str, *, key: str) -> list[str]:
    """Parse ``[a, b, c]`` into ``["a", "b", "c"]``.

    Only the inline flow form is supported -- the corpus uses ``tags: [billing,
    refunds]`` and nothing else. A block list (``- item`` lines) raises rather
    than being silently misread as a scalar, because a wrong value here becomes
    a wrong ``doc_metadata`` row with no downstream signal.
    """
    inner = value[1:-1].strip()
    if not inner:
        return []
    items = [item.strip().strip("'\"") for item in inner.split(",")]
    for item in items:
        if not item:
            raise FrontMatterError.empty_list_item(key)
    return items


def _parse_scalar(value: str, *, key: str) -> object:
    """Parse one front-matter value: an inline list, or a scalar string.

    Scalars are returned as strings. ``effective`` stays a string
    (``"2026-01-15"``) rather than a ``date``: the model stores it in a JSONB
    ``metadata`` column, the corpus never wants a date object, and keeping the
    text is the honest representation of what was written.
    """
    if value.startswith("[") and value.endswith("]"):
        return _split_inline_list(value, key=key)
    if value.startswith("[") or value.endswith("]"):
        raise FrontMatterError.unbalanced_list(key, value)
    if value.startswith(("{", "&", "*", ">", "|", "@", "`")) or ":" in value:
        raise FrontMatterError.unsupported_construct(key, value)
    return value.strip("'\"")


def _parse_yaml_subset(block: str) -> dict[str, object]:
    """Parse the corpus's front-matter subset: ``key: value`` lines.

    Supported: plain scalars and ``[a, b, c]`` inline lists. Anything else -- a
    nested mapping, a block list, an anchor, duplicated keys -- raises
    :class:`FrontMatterError` rather than being silently ignored or guessed at.
    Guessing would put a wrong value into ``doc_metadata``, which no later stage
    validates.
    """
    values: dict[str, object] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace():
            raise FrontMatterError.indented_line(line)
        if line.lstrip().startswith("-"):
            raise FrontMatterError.block_list(line)
        key, separator, raw = line.partition(":")
        if not separator:
            raise FrontMatterError.not_key_value(line)
        key = key.strip()
        if not key:
            raise FrontMatterError.empty_key(line)
        if key in values:
            raise FrontMatterError.duplicate_key(key)
        values[key] = _parse_scalar(raw.strip(), key=key)
    return values


def parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split YAML front-matter from a document body.

    The front-matter is the block between the first line (``---``) and the next
    ``---`` line. The body is everything after the closing delimiter, with a
    single leading newline removed so the body starts at its first real line.

    A document with **no front-matter is an error, not a default**: the four
    keys the corpus carries (``title``, ``owner``, ``effective``, ``tags``) are
    what ``knowledge_documents`` is built from, and defaulting them would index
    an anonymous document that retrieval could still cite.

    Args:
        text: The whole file, front-matter included.

    Returns:
        A ``(metadata, body)`` pair: the parsed keys, and the Markdown body with
        the front-matter removed.

    Raises:
        FrontMatterError: If the front-matter is missing, unterminated, or uses
            a construct outside the supported subset.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        raise FrontMatterError.no_front_matter()

    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONT_MATTER_DELIMITER:
            metadata = _parse_yaml_subset("\n".join(lines[1:index]))
            body = "\n".join(lines[index + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            return metadata, body

    raise FrontMatterError.unterminated()


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingest run, for the API response and the audit event."""

    documents_seen: int
    documents_indexed: int
    chunks_written: int


def _content_hash(text: str) -> str:
    """``sha256`` of a document's full text, as ``knowledge_documents.content_hash``.

    The hash covers the whole file including front-matter: a change to the
    title, owner or tags is a change to the document, and re-indexing must pick
    it up rather than skip the file because only the body was compared.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def ingest_directory(
    path: Path,
    *,
    embedder: Embedder,
    store: VectorStore,
    documents: KnowledgeDocumentStore,
) -> IngestResult:
    """Ingest every Markdown document directly under ``path``.

    ``README.md`` is skipped (see the module docstring). Files are processed in
    sorted name order so a run's counts and any chunk ordering are deterministic.

    Args:
        path: The directory holding ``*.md`` documents.
        embedder: Produces one vector per chunk.
        store: The ``VectorStore`` the chunks are upserted into.

    Returns:
        The run's counts. ``documents_indexed`` counts only documents whose
        content hash changed, so re-ingesting an unchanged tree reports ``0``
        (``docs/api-contract.md`` §9's idempotency contract).
    """
    documents_seen = 0
    documents_indexed = 0
    chunks_written = 0

    # ``glob`` is a blocking filesystem call; run it off the event loop so a
    # slow disk does not stall every other task in the worker process.
    document_paths = await asyncio.to_thread(lambda: sorted(path.glob("*.md")))
    for document_path in document_paths:
        if document_path.name in _EXCLUDED_FILENAMES:
            continue
        documents_seen += 1
        result = await ingest_document(
            document_path, embedder=embedder, store=store, documents=documents
        )
        documents_indexed += result.documents_indexed
        chunks_written += result.chunks_written

    return IngestResult(
        documents_seen=documents_seen,
        documents_indexed=documents_indexed,
        chunks_written=chunks_written,
    )


async def ingest_document(
    path: Path,
    *,
    embedder: Embedder,
    store: VectorStore,
    documents: KnowledgeDocumentStore,
) -> IngestResult:
    """Ingest one document, replacing its chunks.

    The document is hashed first; an unchanged hash short-circuits with zero
    indexed and zero chunks written, which is the idempotency contract. On a
    change (or first index) the front-matter is parsed, the body chunked, every
    chunk embedded in one batch, and the chunk records upserted. Re-ingesting a
    changed document replaces its chunks because the store's upsert keys on
    ``(document_id, ordinal)`` and ``derive_chunk_id`` makes the id stable.

    The document row itself is written through ``store.upsert_document`` when the
    store offers it; a bare ``VectorStore`` (the differential-test fixture) does
    not, so the chunks are still keyed by a deterministic ``document_id`` derived
    from the source slug and the run proceeds.

    Args:
        path: The Markdown file to ingest.
        embedder: Produces one vector per chunk.
        store: The ``VectorStore`` the chunks are upserted into.

    Returns:
        ``(documents_seen=1, documents_indexed=<0 or 1>, chunks_written=<n>)``.

    Raises:
        FrontMatterError: If the file has no parseable front-matter. The message
            names the file.
    """
    # Read off the event loop: a blocking read in an async function stalls the
    # worker's other tasks (ASYNC240).
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    content_hash = _content_hash(text)

    # The hash lives on the *document* store -- the `content_hash` column of
    # `knowledge_documents` -- so the lookup goes there and not to the vector
    # store. An earlier version probed the vector store for `content_hash`,
    # which never has one: the vector store's job is chunks and embeddings, and
    # the document row is a different table behind a different store. The
    # consequence was that the idempotency check silently never fired on the
    # real wiring -- re-indexing an unchanged tree re-embedded all 17 documents
    # and reported `documents_indexed == 17` where `docs/api-contract.md` §9
    # promises 0. A test passed because its fixture handed ingest a composite
    # object that *did* answer `content_hash`; production hands it a bare
    # `VectorStore`. The parameter is therefore required and typed, so the
    # caller cannot omit the store the check depends on.
    existing_hash = await documents.content_hash(path.name)
    if existing_hash == content_hash:
        return IngestResult(documents_seen=1, documents_indexed=0, chunks_written=0)

    try:
        metadata, body = parse_front_matter(text)
    except FrontMatterError as exc:
        raise FrontMatterError.in_file(path.name, exc) from exc

    document_id = await documents.upsert_document(
        source=path.name,
        title=_require_title(path.name, metadata),
        content=text,
        metadata=metadata,
        content_hash=content_hash,
    )

    chunks = split_document(body)
    records = [
        ChunkRecord(
            document_id=document_id,
            ordinal=chunk.ordinal,
            anchor=chunk.anchor,
            heading_path=chunk.heading_path,
            content=chunk.content,
            token_count=chunk.token_count,
        )
        for chunk in chunks
    ]
    embeddings = await embedder.embed([record.content for record in records])
    await store.upsert(records, embeddings)

    return IngestResult(documents_seen=1, documents_indexed=1, chunks_written=len(records))


def _require_title(source: str, metadata: dict[str, object]) -> str:
    """Return the document's ``title``, or raise naming the file.

    ``title`` is ``NOT NULL`` in ``knowledge_documents`` and is what the
    knowledge screen shows; a front-matter block that omits it is a content
    error, and failing here names the file rather than hitting a NOT NULL
    violation with no document in the message.
    """
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise FrontMatterError.missing_title(source)
    return title


__all__ = [
    "FrontMatterError",
    "IngestResult",
    "ingest_directory",
    "ingest_document",
    "parse_front_matter",
]
