"""The corpus's size must be what the documents say it is.

## Why this file exists

The corpus's size is quoted in **fifteen** places: the README (seven, one of
them in its architecture diagram), the data model, the limitations, the
installer's walk-through (three, one of them in its verifier's checklist) and
its Chinese translation (three). It is also on the screenshot the README
points at, and in the installer's list of literals -- the things a verifier
compares against their screen, character for character.

So a change to the corpus that moves the chunk count silently invalidates a
screenshot and a list of literals, and nothing notices, because no test
compared the number to the number.

This file measures the corpus the way the product does -- through
``ingest_directory``, with the front matter stripped and the same splitter the
ingest uses -- and then checks two things:

1. **the size is what the documents say** (17 documents, 98 chunks), and
2. **every document that quotes the size quotes the measured one**, so a
   corpus edit that changes the count says so by turning this file red, and
   the failure names the places to fix.

## The second half is the one that earns its keep

A guard that only pins a number says "the number changed" without saying
where the number was written down. This one reads the documents, so the
failure lists them.

**A note on how the count can move.** A new ``##`` heading in a document is a
new chunk, and a section that grows past the splitter's ceiling is split in
two. Both are ordinary edits, and both change the number. The corpus was
designed to be hard to retrieve correctly, and the local embedder is
lexical, so a chunk that grows also *loses* score against its neighbours --
which is why the walk-through's golden-path question must still retrieve both
of the documents it needs.
"""

from __future__ import annotations

import pathlib
import re

from opspilot.adapters.retrieval.chunking import split_document
from opspilot.adapters.retrieval.ingest import parse_front_matter

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "knowledge"

#: The size the documents quote. The README's screenshot shows the same
#: numbers, so they are not free to move.
EXPECTED_DOCUMENTS = 17
EXPECTED_CHUNKS = 98

#: The documents that quote the size, and where in them. The progress log is
#: **not** in this list: it is an append-only record of what a run reported at
#: the time, and rewriting it would be rewriting history. Neither is
#: ``docs/milestones.md``, whose acceptance-criteria outcome is the same kind
#: of record.
_QUOTING: tuple[tuple[str, str], ...] = (
    ("README.md", "17 policy documents → 98 chunks"),
    ("README.md", "17 documents → 98 chunks"),
    ("README.md", "17 documents seen, 17 indexed, 98 chunks written"),
    ("README.md", "17 documents, 98 chunks"),
    ("README.md", "17 policy documents (98 chunks)"),
    ("README.md", "17 seen · 17 indexed ·"),
    ("docs/data-model.md", "17 documents and 98 chunks"),
    ("docs/limitations.md", "17 documents, 98"),
    ("docs/installer-verification.md", "98 chunks"),
    ("docs/installer-verification.md", "17/17/98"),
    ("docs/installer-verification.zh-CN.md", "98 chunks"),
    ("docs/installer-verification.zh-CN.md", "17/17/98"),
)

#: Places that quote only the **document** count. The rule above wants both
#: numbers near a quote, and a diagram has no room for the chunk count -- so
#: these are a separate list, checked with the same "the phrase must still be
#: there" rule and nothing more. They are separate because a place that states
#: one of the two numbers is a place the other rule cannot judge, and folding
#: it in would either weaken the rule for everybody or exempt it by name.
_DOCUMENT_COUNT_ONLY: tuple[tuple[str, str], ...] = (
    # The README's architecture diagram: "17 documents" beside "pgvector".
    ("README.md", "17 documents             │"),
)


def _corpus_size() -> tuple[int, int]:
    """The documents and chunks the product's own ingest path produces."""
    documents = 0
    chunks = 0
    for path in sorted(CORPUS.glob("*.md")):
        if path.name == "README.md":
            # Excluded by the ingest, by name (`ingest.py`).
            continue
        _metadata, body = parse_front_matter(path.read_text(encoding="utf-8"))
        documents += 1
        chunks += len(split_document(body))
    return documents, chunks


def test_the_corpus_is_the_size_the_documents_say() -> None:
    """17 documents, 98 chunks -- measured, not transcribed."""
    documents, chunks = _corpus_size()
    assert documents == EXPECTED_DOCUMENTS, (
        f"the corpus holds {documents} documents, not {EXPECTED_DOCUMENTS}. The "
        "README, the data model, the limitations and the installer's "
        "walk-through all quote the count, and the walk-through's list of "
        "literals is compared against a screen."
    )
    assert chunks == EXPECTED_CHUNKS, (
        f"the corpus splits into {chunks} chunks, not {EXPECTED_CHUNKS}. A new "
        "heading in a document is a new chunk, and a section that grows past "
        "the splitter's ceiling is split in two -- so an ordinary edit moves "
        "this number, and the documents that quote it (and the screenshot "
        "that shows it) have to be updated with it."
    )


def test_every_document_that_quotes_the_size_quotes_the_measured_one() -> None:
    """The number in the prose is the number the corpus has."""
    _documents, chunks = _corpus_size()
    # The list is the substance of this test: an empty one would make the
    # loop below run zero times and the guard would read green while reading
    # no document at all.
    assert _QUOTING, (
        "no document is listed as quoting the corpus size. The loop below "
        "would run zero times, and 'every document that quotes the size "
        "quotes the measured one' would be true of no documents."
    )
    # The count is pinned as well as the emptiness, because a list that had
    # quietly *lost* a place would check the remaining ones and read green:
    # the quote it no longer covers would go stale with nothing to say so.
    # Two of the entries cover two places each, so twelve entries are
    # fourteen places; the diagram is the fifteenth and is listed separately.
    assert len(_QUOTING) == 12, (
        f"{len(_QUOTING)} places are listed as quoting the corpus size, not "
        "the twelve this file covers. One of them has dropped out, and that "
        "quote would no longer be checked."
    )
    wrong: list[str] = []
    for relative, phrase in _QUOTING:
        path = REPO_ROOT / relative
        text = path.read_text(encoding="utf-8")
        if phrase not in text:
            wrong.append(f"{relative}: no longer says {phrase!r}")
            continue
        # The sentence that quotes the size has to quote *this* size. The
        # window is a paragraph rather than a line, because a table row puts
        # the document count and the chunk count in one cell split over two
        # lines.
        for match in re.finditer(re.escape(phrase), text):
            window = text[match.start() : match.start() + 240]
            if str(chunks) not in window:
                wrong.append(f"{relative}: {phrase!r} is not accompanied by {chunks}")
                break
    assert not wrong, (
        "these documents quote a corpus size that is not the measured one:\n  "
        + "\n  ".join(wrong)
        + f"\n\nThe corpus currently splits into {chunks} chunks."
    )


def test_every_place_that_quotes_the_document_count_quotes_the_measured_one() -> None:
    """A place that states only the document count still states the right one.

    The README's architecture diagram says ``17 documents`` beside
    ``pgvector`` and has no room for the chunk count, so the rule above --
    which wants both numbers near a quote -- cannot judge it. But it is still
    a place the corpus's size is written down, and a corpus that grew would
    leave it stale with nothing to say so.

    So it is checked with the same "the phrase must still be there" rule and
    nothing more, in its own list: a place that states one of the two numbers
    is a place the other rule cannot judge, and folding it in would either
    weaken the rule for everybody or exempt it by name.
    """
    _documents, documents = _corpus_size()
    assert _DOCUMENT_COUNT_ONLY, (
        "no place is listed as quoting the document count alone. The loop "
        "below would run zero times, and a diagram that kept a stale count "
        "would go unnoticed."
    )
    assert len(_DOCUMENT_COUNT_ONLY) == 1, (
        f"{len(_DOCUMENT_COUNT_ONLY)} places are listed as quoting the "
        "document count alone, not the one this file knows about."
    )
    wrong: list[str] = []
    for relative, phrase in _DOCUMENT_COUNT_ONLY:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        if phrase not in text:
            wrong.append(
                f"{relative}: no longer says {phrase!r} -- either the corpus "
                f"now holds {documents} documents, or the diagram was redrawn"
            )
    assert not wrong, (
        "these places quote a document count that is not the measured one:\n  "
        + "\n  ".join(wrong)
        + f"\n\nThe corpus currently holds {documents} documents."
    )


def test_the_installer_walkthrough_quotes_the_measured_size() -> None:
    """The installer's literal list is compared against a screen.

    The list is a list of things that must not change when the document is
    translated, so a number in it that the corpus no longer has would send a
    verifier looking for something the dashboard will not show.
    """
    _documents, chunks = _corpus_size()
    for relative in ("docs/installer-verification.md", "docs/installer-verification.zh-CN.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert re.search(rf"`{chunks} chunks`", text), (
            f"{relative} does not list `{chunks} chunks` among the literals "
            "that must not change, and the corpus no longer has that many"
        )
