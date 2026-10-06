"""Unit tests for the embedding adapters, from the M5 specification.

The local embedder is what CI runs, so these tests are load-bearing: a broken
local embedder makes every retrieval test vacuous. The three properties the
brief requires are each tested separately -- deterministic, unit-norm, and
different for different texts -- plus the shape contract both embedders share
(exactly ``len(texts)`` vectors, each of length ``dim``).
"""

from __future__ import annotations

import math

import pytest

from opspilot.adapters.retrieval.embeddings import (
    Embedder,
    LocalDeterministicEmbedder,
    ProviderEmbedder,
    build_embedder,
)

DIM = 64


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


# --------------------------------------------------------------------------- #
# LocalDeterministicEmbedder
# --------------------------------------------------------------------------- #


async def test_local_embed_returns_one_vector_per_text() -> None:
    embedder = LocalDeterministicEmbedder(dim=DIM)
    vectors = await embedder.embed(["alpha", "beta", "gamma"])
    assert len(vectors) == 3
    assert all(len(vector) == DIM for vector in vectors)


async def test_local_embed_empty_batch_returns_empty() -> None:
    embedder = LocalDeterministicEmbedder(dim=DIM)
    assert await embedder.embed([]) == []


async def test_local_embed_is_deterministic() -> None:
    embedder = LocalDeterministicEmbedder(dim=DIM)
    first = await embedder.embed(["the refund limit is one hundred dollars"])
    second = await embedder.embed(["the refund limit is one hundred dollars"])
    assert first == second


async def test_local_embed_is_deterministic_across_instances() -> None:
    a = await LocalDeterministicEmbedder(dim=DIM).embed(["hello world"])
    b = await LocalDeterministicEmbedder(dim=DIM).embed(["hello world"])
    assert a == b


async def test_local_embed_differs_for_different_texts() -> None:
    embedder = LocalDeterministicEmbedder(dim=DIM)
    a, b = await embedder.embed(["refund policy", "duplicate charge"])
    assert a != b


# --------------------------------------------------------------------------- #
# The lexical properties the retriever actually depends on.
#
# These are new since the M5e fix, and they are the reason the embedder is not a
# hash of the whole text any more. A hash of the text is a perfectly
# deterministic function and scores everything in a 0.04-0.09 band with the
# answerable and unanswerable bands overlapping, so no threshold could
# discriminate. These assert the property that fixes it: the score reflects
# shared *vocabulary*.
# --------------------------------------------------------------------------- #


async def test_local_embed_scores_text_sharing_vocabulary_above_text_that_does_not() -> None:
    """Two texts sharing terms must outscore two that share none.

    This is the load-bearing property. Without it ``RETRIEVAL_MIN_SCORE`` cannot
    mean anything, which is exactly how M5 shipped an abstaining golden path.
    """
    embedder = LocalDeterministicEmbedder(dim=1536)
    query, related, unrelated = await embedder.embed(
        [
            "duplicate charge refund approval threshold",
            "a duplicate charge is refunded after verification and approval",
            "espresso martini recipe for an office party",
        ]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, related) > cosine(query, unrelated), (
        "a related document must score above an unrelated one; the embedder is "
        "not responding to the text's content"
    )


async def test_local_embed_ignores_stopwords_when_scoring() -> None:
    """Stopwords carry no retrieval signal, so they must not change the score.

    ``"what is the refund policy"`` and ``"refund policy"`` differ only in
    closed-class words. If those moved the score, every unanswerable eval
    question -- all of which open with "What is ..." -- would score like a
    policy question, which is precisely how the two bands came to overlap.
    """
    embedder = LocalDeterministicEmbedder(dim=1536)
    with_stopwords, without = await embedder.embed(["what is the refund policy", "refund policy"])

    assert with_stopwords == without, (
        "stopwords must not affect the embedding; the corpus and the query must "
        "be tokenised identically"
    )


async def test_local_embed_folds_inflections_onto_one_term() -> None:
    """The ticket's word and the document's word must be the same term.

    The golden-path ticket says "charged"; ``refund-policy.md`` writes "duplicate
    charges". Without that fold the one document governing the interaction is
    not retrievable for the question that needs it, which is what kept
    ``refund-policy.md`` out of the golden path's citations before the fix.

    Scoped to that pair deliberately. The stemmer is rule-based, not a Porter
    stemmer, so ``charging`` folds to ``charg`` while ``charged`` folds to
    ``charge`` -- they meet but do not merge. Asserting the full paradigm would
    be asserting a better stemmer than this one has; the honest claim is the one
    the corpus actually depends on.
    """
    embedder = LocalDeterministicEmbedder(dim=1536)
    charged, charges, charge = await embedder.embed(["charged", "charges", "charge"])

    assert charged == charges, (
        "'charged' (the ticket's word) and 'charges' (the document's word) must "
        "fold to one term, or the golden path cannot retrieve refund-policy.md"
    )
    assert charged == charge


async def test_local_embed_keeps_distinct_words_distinct() -> None:
    """Folding must not collapse unrelated words onto one another.

    The guard on the previous test: a stemmer that maps everything to the same
    term would pass it while retrieving nothing.
    """
    embedder = LocalDeterministicEmbedder(dim=1536)
    refund, suspension = await embedder.embed(["refund", "suspension"])
    assert refund != suspension


async def test_local_embed_is_unit_norm() -> None:
    """Cosine similarity and dot product must coincide, so |v| == 1."""
    embedder = LocalDeterministicEmbedder(dim=DIM)
    vectors = await embedder.embed(["some policy text", "another policy text", "x"])
    for vector in vectors:
        assert _norm(vector) == pytest.approx(1.0, abs=1e-9)


async def test_local_embed_handles_empty_string() -> None:
    """An empty string is still a valid input and must not raise or be zero."""
    embedder = LocalDeterministicEmbedder(dim=DIM)
    (vector,) = await embedder.embed([""])
    assert len(vector) == DIM
    # Zero-norm would make cosine undefined; unit-norm is the contract.
    assert _norm(vector) == pytest.approx(1.0, abs=1e-9)


def test_local_embedder_dim_property() -> None:
    assert LocalDeterministicEmbedder(dim=97).dim == 97


def test_local_embedder_satisfies_protocol() -> None:
    assert isinstance(LocalDeterministicEmbedder(dim=DIM), Embedder)


# --------------------------------------------------------------------------- #
# ProviderEmbedder -- no network; only the shape and laziness are testable here
# --------------------------------------------------------------------------- #


def test_provider_embedder_dim_property() -> None:
    assert ProviderEmbedder(api_key="k", model="text-embedding-3-small", dim=128).dim == 128


def test_provider_embedder_satisfies_protocol() -> None:
    assert isinstance(
        ProviderEmbedder(api_key="k", model="text-embedding-3-small", dim=DIM), Embedder
    )


def test_openai_is_not_imported_at_module_scope() -> None:
    """``openai`` must only be imported inside ``ProviderEmbedder.embed``.

    The import is deferred so a fresh clone (and CI, which runs the local
    embedder) needs no provider SDK. Asserted by inspecting the module's global
    namespace: a module-scope ``import openai`` would leave ``openai`` bound as
    a module global.
    """
    import opspilot.adapters.retrieval.embeddings as module

    assert not hasattr(module, "openai"), (
        "openai must not be imported at module scope; import it inside embed()"
    )


# --------------------------------------------------------------------------- #
# build_embedder
# --------------------------------------------------------------------------- #


def test_build_embedder_local() -> None:
    embedder = build_embedder("local", api_key="", model="unused", dim=DIM)
    assert isinstance(embedder, LocalDeterministicEmbedder)
    assert embedder.dim == DIM


def test_build_embedder_openai() -> None:
    embedder = build_embedder("openai", api_key="key", model="text-embedding-3-small", dim=DIM)
    assert isinstance(embedder, ProviderEmbedder)
    assert embedder.dim == DIM


def test_build_embedder_unknown_name_raises_and_names_accepted_values() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_embedder("cohere", api_key="k", model="m", dim=DIM)
    message = str(excinfo.value)
    assert "local" in message
    assert "openai" in message
