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
