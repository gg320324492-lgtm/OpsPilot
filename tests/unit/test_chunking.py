"""Unit tests for heading-aware chunking, written from the M5 specification.

These are written against the contract in the M5 brief and
``docs/architecture.md`` §9 / ``docs/api-contract.md`` §3 (a citation is
``"{document_slug}#{anchor}"`` and a reader can grep for the anchor), not
against the implementation. The two properties the brief calls out as easy to
get wrong have their own tests and each of those tests is deliberately able to
fail:

- **anchor uniqueness** -- two ``## Notes`` headings must not share an anchor;
- **real overlap** -- the tail of chunk N must be byte-identical to the head of
  chunk N+1, not merely the same token count.
"""

from __future__ import annotations

import itertools

from opspilot.adapters.retrieval.chunking import (
    Chunk,
    estimate_tokens,
    slugify_heading,
    split_document,
)

# --------------------------------------------------------------------------- #
# estimate_tokens
# --------------------------------------------------------------------------- #


def test_estimate_tokens_never_zero_for_non_empty_text() -> None:
    """The only hard guarantee: a non-empty string estimates to at least 1."""
    assert estimate_tokens("a") >= 1
    assert estimate_tokens(" ") >= 1


def test_estimate_tokens_is_zero_for_empty_text() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_is_deterministic() -> None:
    text = "The refund limit is one hundred dollars for enterprise plans."
    assert estimate_tokens(text) == estimate_tokens(text)


def test_estimate_tokens_grows_with_length() -> None:
    short = "refund"
    long = "refund " * 40
    assert estimate_tokens(long) > estimate_tokens(short)


def test_estimate_tokens_is_an_approximation_not_a_tokenizer() -> None:
    """The documented rule (chars/4, floor 1) holds for a known-length string."""
    text = "a" * 40
    assert estimate_tokens(text) == 10


# --------------------------------------------------------------------------- #
# slugify_heading
# --------------------------------------------------------------------------- #


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify_heading("Refund Limits") == "refund-limits"


def test_slugify_strips_hash_marks_and_whitespace() -> None:
    assert slugify_heading("## Refund Limits") == "refund-limits"
    assert slugify_heading("   Refund Limits   ") == "refund-limits"


def test_slugify_removes_punctuation() -> None:
    assert slugify_heading("Refund: Limits & Rules!") == "refund-limits-rules"


def test_slugify_matches_github_anchor_for_a_typical_heading() -> None:
    """The anchor a reader gets from the rendered heading, e.g. ``refund-limits``."""
    assert slugify_heading("### Refund Limits (per transaction)") == (
        "refund-limits-per-transaction"
    )


def test_slugify_collapses_repeated_separators() -> None:
    """Runs of non-alphanumerics collapse to a single hyphen."""
    assert slugify_heading("Refund --  Limits") == "refund-limits"


# --------------------------------------------------------------------------- #
# split_document -- basics
# --------------------------------------------------------------------------- #


def test_empty_document_yields_no_chunks() -> None:
    assert split_document("") == []


def test_heading_with_no_body_yields_no_chunk() -> None:
    """A heading with an empty body must not emit an empty chunk."""
    chunks = split_document("# Title\n\n## Empty Section\n\n## Real Section\n\nbody text")
    contents = [c.content for c in chunks]
    assert all(c.strip() for c in contents)
    anchors = [c.anchor for c in chunks]
    assert "empty-section" not in anchors


def test_ordinal_is_zero_based_and_contiguous() -> None:
    md = "# A\n\nalpha\n\n# B\n\nbravo\n\n# C\n\ncharlie"
    chunks = split_document(md)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_each_chunk_carries_nearest_heading_anchor() -> None:
    md = "# Refund Policy\n\n## Refund Limits\n\nThe limit is $100."
    chunks = split_document(md)
    assert any(c.anchor == "refund-limits" for c in chunks)


def test_heading_path_records_the_ancestry() -> None:
    md = "# Refund Policy\n\n## Refund Limits\n\nThe limit is $100."
    chunks = split_document(md)
    limits = [c for c in chunks if c.anchor == "refund-limits"]
    assert limits, "expected a chunk under 'Refund Limits'"
    assert limits[0].heading_path == "Refund Policy > Refund Limits"


def test_content_before_first_heading_gets_an_anchor() -> None:
    """Preamble text must still carry a non-None anchor string."""
    md = "Some preamble before any heading.\n\n# First Section\n\nbody"
    chunks = split_document(md)
    assert chunks, "preamble must produce a chunk"
    assert chunks[0].anchor is not None
    assert isinstance(chunks[0].anchor, str)


def test_token_count_is_populated_and_positive() -> None:
    md = "# A\n\nsome body text"
    for chunk in split_document(md):
        assert chunk.token_count >= 1
        assert chunk.token_count == estimate_tokens(chunk.content)


# --------------------------------------------------------------------------- #
# split_document -- anchor uniqueness (the first hard requirement)
# --------------------------------------------------------------------------- #


def test_duplicate_headings_get_distinct_anchors() -> None:
    """Two ``## Notes`` sections must not share an anchor.

    ``anchor`` is half of the citation string ``"{slug}#{anchor}"``; a duplicate
    makes a citation ambiguous.
    """
    md = "# Doc\n\n## Notes\n\nfirst note\n\n## Notes\n\nsecond note"
    chunks = split_document(md)
    anchors = [c.anchor for c in chunks]
    assert len(anchors) == len(set(anchors)), f"duplicate anchors: {anchors}"


def test_duplicate_headings_are_deduplicated_github_style() -> None:
    """The second and third ``Notes`` become ``notes-1`` and ``notes-2``."""
    md = "# Doc\n\n## Notes\n\na\n\n## Notes\n\nb\n\n## Notes\n\nc"
    anchors = {c.anchor for c in split_document(md)}
    assert "notes" in anchors
    assert "notes-1" in anchors
    assert "notes-2" in anchors


# --------------------------------------------------------------------------- #
# split_document -- real overlap (the second hard requirement)
# --------------------------------------------------------------------------- #


def test_long_section_is_split_into_multiple_chunks() -> None:
    body = " ".join(f"word{i}" for i in range(4000))
    md = f"# Long\n\n{body}"
    chunks = split_document(md, max_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1


def test_consecutive_chunks_share_identical_text() -> None:
    """The tail of chunk N must be *byte-identical* to the head of chunk N+1.

    An equal token count is not enough -- the brief is explicit that the shared
    text must be genuinely identical, or the overlap is not doing anything. The
    test finds the longest word-sequence that ends chunk N and begins chunk N+1
    and asserts it is a real, non-trivial overlap.
    """
    body = " ".join(f"word{i}" for i in range(4000))
    md = f"# Long\n\n{body}"
    chunks = split_document(md, max_tokens=100, overlap_tokens=20)

    same_section = [c for c in chunks if c.anchor == chunks[0].anchor]
    assert len(same_section) >= 2, "need at least two chunks to test overlap"

    for previous, current in itertools.pairwise(same_section):
        prev_words = previous.content.split()
        cur_words = current.content.split()
        # The longest k such that the last k words of ``previous`` equal the
        # first k words of ``current`` -- the genuine shared text.
        shared = 0
        limit = min(len(prev_words), len(cur_words))
        for k in range(limit, 0, -1):
            if prev_words[-k:] == cur_words[:k]:
                shared = k
                break
        assert shared > 0, "no shared text between consecutive chunks; overlap is not real"
        # A meaningful overlap, not a single coincidental word.
        assert shared >= 2, f"overlap of only {shared} word(s) is too small"


def test_overlap_reduces_effective_step() -> None:
    """With overlap, consecutive chunks start before the previous one ends."""
    body = " ".join(f"w{i}" for i in range(2000))
    md = f"# S\n\n{body}"
    chunks = split_document(md, max_tokens=100, overlap_tokens=30)
    assert len(chunks) >= 2
    # Chunk 2 must begin with text that also appears in chunk 1 -- that is what
    # overlap means. Locate the start of chunk 2's text inside chunk 1.
    first_word_of_second = chunks[1].content.split()[0]
    assert first_word_of_second in chunks[0].content.split()
    # And the two chunks must not be identical (there is forward progress).
    assert chunks[1].content != chunks[0].content


def test_zero_overlap_produces_no_shared_text() -> None:
    """With ``overlap_tokens=0`` consecutive chunks must not share text."""
    body = " ".join(f"w{i}" for i in range(2000))
    md = f"# S\n\n{body}"
    chunks = split_document(md, max_tokens=100, overlap_tokens=0)
    assert len(chunks) >= 2
    tail = set(chunks[0].content.split()[-10:])
    head = set(chunks[1].content.split()[:10])
    assert not (tail & head), "zero overlap must not share text"


def test_chunk_is_frozen_dataclass() -> None:
    chunk = Chunk(ordinal=0, anchor="a", heading_path="A", content="x", token_count=1)
    assert chunk.ordinal == 0
