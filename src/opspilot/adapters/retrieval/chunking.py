"""Heading-aware chunking of Markdown policy documents.

Responsibility: split a knowledge document into ~500-token chunks with overlap,
each tagged with the nearest heading's slug (``anchor``) and its full
``heading_path`` (e.g. ``Refund Policy > Limits``). The anchor is what makes a
citation point at a specific section rather than a whole file.

Layer: ``adapters`` (retrieval). Pure text processing -- no I/O, no model.

M0: signatures only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One chunk produced from a document, before embedding."""

    ordinal: int
    anchor: str
    heading_path: str
    content: str
    token_count: int


def split_document(
    markdown: str, *, max_tokens: int = 500, overlap_tokens: int = 50
) -> list[Chunk]:
    """Chunk ``markdown`` by heading, respecting the token budget. M0 stub."""
    raise NotImplementedError


def estimate_tokens(text: str) -> int:
    """Estimate a token count for ``text`` without calling a tokenizer. M0 stub."""
    raise NotImplementedError


def slugify_heading(heading: str) -> str:
    """Turn a heading into its anchor slug (``## Refund Limits`` -> ``refund-limits``). M0 stub."""
    raise NotImplementedError
