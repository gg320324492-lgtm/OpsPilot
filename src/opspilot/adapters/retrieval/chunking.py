"""Heading-aware chunking of Markdown policy documents.

Responsibility: split a knowledge document into ~500-token chunks with overlap,
each tagged with the nearest heading's slug (``anchor``) and its full
``heading_path`` (e.g. ``Refund Policy > Limits``). The anchor is what makes a
citation point at a specific section rather than a whole file.

Layer: ``adapters`` (retrieval). Pure text processing -- no I/O, no model.

The anchor is half of the citation string ``"{document_slug}#{anchor}"``
(``docs/api-contract.md`` §3), and the other half is the document's ``source``
slug. That is why anchors are unique *within a document*: two sections with the
same heading text would otherwise produce one ambiguous citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens per character. The rule is "characters / 4, floored at 1", the standard
# rule of thumb for English BPE tokenizers. It is an *approximation*: see
# :func:`estimate_tokens`.
_CHARS_PER_TOKEN = 4

# A Markdown ATX heading: one to six leading ``#`` followed by a space.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# GitHub's anchor algorithm keeps word characters (Unicode letters, digits and
# underscore), hyphens, and whitespace, and drops everything else -- punctuation,
# ``&``, ``(``, ``)``, ``:``. Runs of whitespace or hyphens then collapse to a
# single hyphen and the result is trimmed. ``\w`` in Python is Unicode-aware by
# default, which matches GitHub's handling of accented headings.
_SLUG_DROP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SEPARATOR_RE = re.compile(r"[\s-]+")

# The anchor given to content that appears before any heading. GitBook/GitHub
# render such lead-in text directly under the document title, and the citation
# for it should name the document rather than a section. An empty-string anchor
# makes ``"{slug}#"`` -- and the reader greps the slug. We use the empty string
# rather than a magic slug like ``_preamble`` because no such heading exists to
# grep for, and inventing one produces a citation that does not resolve.
_PREAMBLE_ANCHOR = ""


@dataclass(frozen=True)
class Chunk:
    """One chunk produced from a document, before embedding."""

    ordinal: int
    anchor: str
    heading_path: str
    content: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """Estimate a token count for ``text`` without calling a tokenizer.

    The rule is ``ceil(len(text) / 4)``, i.e. roughly four characters per token,
    floored at 1 for any non-empty string. This is a documented **approximation**
    for budgeting chunk sizes, not a tokenizer: it does not know about
    sub-words, code, or non-English text, and it will disagree with the real
    count for any given provider.

    The floor matters. A one-character chunk estimating to 0 would make
    ``max_tokens`` arithmetic divide by zero and would let an empty-looking
    chunk through a "non-empty" check.

    Args:
        text: The text to estimate.

    Returns:
        A positive integer for non-empty text, and 0 for the empty string.
    """
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def slugify_heading(heading: str) -> str:
    """Turn a heading into its GitHub-style anchor slug.

    ``"## Refund Limits"`` -> ``"refund-limits"``. The algorithm matches what a
    reader gets from the rendered heading: lowercase, drop the leading ``#``
    marks and surrounding whitespace, remove punctuation, collapse runs of
    whitespace and hyphens to a single hyphen, and trim leading/trailing
    hyphens.

    Matching the rendered anchor is load-bearing: a citation is
    ``"{document_slug}#{anchor}"`` and the reader is told they can grep for it,
    so an anchor that does not appear in the rendered page is a broken citation.

    Args:
        heading: The heading text, with or without leading ``#`` marks.

    Returns:
        The anchor slug, which may be the empty string for a heading that is
        entirely punctuation.
    """
    text = heading.strip()
    # Drop any leading ATX ``#`` marks.
    text = text.lstrip("#").strip()
    text = text.lower()
    # Drop punctuation first, keeping whitespace; then collapse whitespace and
    # hyphens to a single hyphen. Doing it the other way around leaves a double
    # hyphen where a punctuation character sat between two spaces
    # (``"Limits & Rules"`` -> ``limits--rules``).
    text = _SLUG_DROP_RE.sub("", text)
    text = _SLUG_SEPARATOR_RE.sub("-", text)
    return text.strip("-")


@dataclass(frozen=True)
class _Section:
    """A heading and the body lines under it, before token-level splitting."""

    level: int
    heading: str
    body: str
    # The heading stack at the point this section starts, outermost first, so
    # ``heading_path`` can be rendered without re-walking the document.
    ancestry: tuple[str, ...]


def _parse_sections(markdown: str) -> list[_Section]:
    """Split ``markdown`` into one ``_Section`` per heading, plus any preamble.

    Content before the first heading becomes a section with an empty heading and
    the preamble anchor. A heading with no body produces a section that
    :func:`split_document` will drop (it must not emit an empty chunk).
    """
    lines = markdown.splitlines()
    sections: list[_Section] = []
    # ``stack`` parallels GitHub's heading nesting: index ``i`` holds the text of
    # the most recent level ``i+1`` heading.
    stack: list[str] = []
    current_heading: str | None = None
    current_level = 0
    current_body: list[str] = []
    preamble: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_level, current_body
        if current_heading is None:
            return
        ancestry = (*stack[: current_level - 1], current_heading)
        sections.append(
            _Section(
                level=current_level,
                heading=current_heading,
                body="\n".join(current_body).strip(),
                ancestry=ancestry,
            )
        )
        current_body = []

    for line in lines:
        match = _HEADING_RE.match(line)
        if match is None:
            if current_heading is None:
                preamble.append(line)
            else:
                current_body.append(line)
            continue

        flush()
        level = len(match.group(1))
        heading = match.group(2).strip()
        # Truncate the stack to the parent levels, then push this heading. A
        # level jump (``#`` then ``###``) leaves an absent parent out of the
        # stack naturally; the path reflects what was actually written.
        if level - 1 < len(stack):
            del stack[level - 1 :]
        stack.append(heading)
        current_heading = heading
        current_level = level
        current_body = []

    flush()

    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        sections.insert(
            0,
            _Section(level=0, heading="", body=preamble_text, ancestry=()),
        )
    return sections


def _split_body(body: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split ``body`` into token-budgeted pieces with genuine text overlap.

    Splitting is done on whitespace into words, so the overlap between
    consecutive pieces is the *same words*, not merely an equal count. This is
    what the brief requires: a differential test that compares the tail of one
    chunk with the head of the next must find them byte-identical.

    Args:
        body: The section body, already stripped.
        max_tokens: The per-piece token ceiling.
        overlap_tokens: How many estimated tokens of trailing text to repeat at
            the start of the next piece.

    Returns:
        One or more pieces. A body that fits in ``max_tokens`` returns a single
        piece unchanged.
    """
    if estimate_tokens(body) <= max_tokens:
        return [body]

    words = body.split()
    if not words:
        return []

    pieces: list[str] = []
    # A piece advances by at least one word per iteration, so this terminates.
    # ``overlap_words`` is capped below ``max_words`` to guarantee progress.
    start = 0
    while start < len(words):
        piece_words: list[str] = []
        # Greedily add words until the estimated token count would exceed the
        # budget. Estimating on the running join is O(n^2) in the worst case but
        # sections are small and this keeps the estimate honest for multi-byte
        # text.
        while start + len(piece_words) < len(words):
            candidate = [*piece_words, words[start + len(piece_words)]]
            if piece_words and estimate_tokens(" ".join(candidate)) > max_tokens:
                break
            piece_words = candidate

        piece = " ".join(piece_words)
        pieces.append(piece)
        if start + len(piece_words) >= len(words):
            break

        # Advance to the overlap boundary: step forward by (consumed - overlap)
        # words. Clamp so at least one new word is consumed each iteration.
        consumed = len(piece_words)
        overlap_words = 0
        if overlap_tokens > 0:
            # Choose the largest word count whose estimate is within the overlap
            # budget but strictly fewer words than the piece just emitted.
            while (
                overlap_words < consumed - 1
                and estimate_tokens(" ".join(piece_words[-overlap_words - 1 :])) <= overlap_tokens
            ):
                overlap_words += 1
        step = max(1, consumed - overlap_words)
        start += step

    return pieces


def split_document(
    markdown: str, *, max_tokens: int = 500, overlap_tokens: int = 50
) -> list[Chunk]:
    """Chunk ``markdown`` by heading, respecting the token budget.

    Each heading starts a new section; a section body longer than ``max_tokens``
    is split further, with ``overlap_tokens`` of identical text repeated between
    consecutive pieces so a rule that straddles a split appears whole in at
    least one chunk.

    Every chunk carries the slug of its *nearest* heading as ``anchor`` and the
    full ancestry as ``heading_path`` (e.g. ``Refund Policy > Refund Limits``).
    Anchors are unique within the document: repeated heading text is
    deduplicated GitHub-style (``notes``, ``notes-1``, ``notes-2``), because a
    duplicate anchor would make the citation ``"{slug}#{anchor}"`` ambiguous.

    Content before the first heading is emitted under the preamble anchor
    (``""``), which cites the document itself -- see ``_PREAMBLE_ANCHOR``. A
    heading with no body emits no chunk.

    Args:
        markdown: The document body, front-matter already removed.
        max_tokens: The per-chunk token ceiling (approximate).
        overlap_tokens: Tokens of identical text repeated across a split.

    Returns:
        The document's chunks, ``ordinal`` 0-based and contiguous.
    """
    sections = _parse_sections(markdown)
    anchor_counts: dict[str, int] = {}
    chunks: list[Chunk] = []

    for section in sections:
        body = section.body
        if not body.strip():
            # A heading with no body yields no chunk -- an empty chunk would be
            # embedded, returned by search, and cited as an empty section.
            continue

        base = slugify_heading(section.heading) if section.heading else _PREAMBLE_ANCHOR

        if base in anchor_counts:
            # GitHub deduplicates repeated anchors with a ``-N`` suffix on the
            # *second* occurrence onward, starting at 1.
            anchor_counts[base] += 1
            anchor = f"{base}-{anchor_counts[base]}"
        else:
            anchor_counts[base] = 0
            anchor = base

        heading_path = " > ".join(section.ancestry) if section.ancestry else ""

        for piece in _split_body(body, max_tokens, overlap_tokens):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    anchor=anchor,
                    heading_path=heading_path,
                    content=piece,
                    token_count=estimate_tokens(piece),
                )
            )

    return chunks


__all__ = ["Chunk", "estimate_tokens", "slugify_heading", "split_document"]
