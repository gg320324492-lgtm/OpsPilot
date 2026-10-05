"""Embedding functions: a deterministic local default and a provider-backed one.

Responsibility: map text to a fixed-dimension vector. The **local deterministic**
embedder is the default (``EMBEDDING_PROVIDER=local``) so a fresh clone and CI
run with no API key; the provider-backed one (``openai``) is what makes
retrieval quality real and is selected by config.

Layer: ``adapters`` (retrieval). Implements the implicit embedding callable used
by ``ingest`` and ``search``.

Honesty note (stated in ``.env.example``): the local embedder's retrieval
quality is poor and it exists as plumbing, not as a good embedder. Any number
quoted in the README comes from a run with a real provider, and the run's
config is recorded with it. The ``openai`` SDK is imported inside the method, so
this module imports without it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors."""

    @property
    def dim(self) -> int:
        """The embedding dimension."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        ...


class LocalDeterministicEmbedder:
    """A dependency-free, deterministic embedder -- plumbing, not quality."""

    def __init__(self, *, dim: int = 1536) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        """The embedding dimension."""
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash-based deterministic embedding of each text. M0 stub."""
        raise NotImplementedError


class ProviderEmbedder:
    """A provider-backed embedder (OpenAI). Imports the SDK lazily."""

    def __init__(self, *, api_key: str, model: str, dim: int = 1536) -> None:
        self._api_key = api_key
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        """The embedding dimension."""
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the provider API. M0 stub."""
        raise NotImplementedError


def build_embedder(provider: str, *, api_key: str, model: str, dim: int) -> Embedder:
    """Select an embedder from config (``local`` | ``openai``). M0 stub."""
    raise NotImplementedError
