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

import hashlib
import struct
from typing import Protocol, runtime_checkable

# A fixed personalisation/namespace constant for the deterministic local
# embedder. It is part of the embedder's identity: two different constants give
# two different (but each internally consistent) embedding spaces, and changing
# it invalidates every stored local embedding. It is a fixed literal, never a
# per-process random value, or determinism across processes would be lost.
# ``blake2b``'s ``person`` field is capped at 16 bytes.
_LOCAL_HASH_PERSON = b"opspilot.local1"

# The accepted ``EMBEDDING_PROVIDER`` values, named in the error an unknown
# value raises and asserted by ``tests/unit/test_embeddings.py``.
_ACCEPTED_PROVIDERS = ("local", "openai")


class UnknownEmbeddingProvider(ValueError):
    """``EMBEDDING_PROVIDER`` named a provider that is not implemented.

    A ``ValueError`` so a caller may catch the broad type, and so the failure is
    a clear startup error rather than a silent fallback to the local embedder.
    """

    def __init__(self, provider: str) -> None:
        accepted = " or ".join(repr(name) for name in _ACCEPTED_PROVIDERS)
        super().__init__(f"unknown embedding provider {provider!r}; expected {accepted}")


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
    """A dependency-free, deterministic embedder -- plumbing, not quality.

    ``embed`` produces a **unit-norm** vector for every input, so cosine
    similarity and the dot product coincide and a store may use either. The
    vector is derived from a seeded ``blake2b`` digest of the text, expanded
    into floats with a fixed keyed ``struct`` pattern; nothing about it is
    trained, and its retrieval quality is poor by design.

    What it *is* good for: CI and a fresh clone run the whole retrieval suite
    with no API key and no network, and because it is a real deterministic
    function of the text -- not a constant or a stub -- those tests exercise
    real code paths (ordering, tie-breaks, the differential store comparison).
    A broken local embedder would make every retrieval test vacuous, which is
    why the brief treats it as load-bearing rather than a toy.
    """

    def __init__(self, *, dim: int = 1536) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        """The embedding dimension."""
        return self._dim

    def _vector(self, text: str) -> list[float]:
        """Deterministically expand ``text`` into a unit-norm vector.

        The digest is keyed with a fixed person so the mapping is stable across
        processes and Python versions, and it is expanded by re-hashing the
        digest with an incrementing counter until ``dim`` floats are produced.
        Bytes are read as unsigned 32-bit integers and mapped to floats, then
        the vector is scaled to unit length.

        An empty text is hashed the same way as any other input; it still
        produces a well-defined unit vector, so cosine similarity stays defined
        rather than dividing by a zero norm.
        """
        components: list[float] = []
        counter = 0
        while len(components) < self._dim:
            digest = hashlib.blake2b(
                text.encode("utf-8") + counter.to_bytes(8, "big"),
                digest_size=32,
                person=_LOCAL_HASH_PERSON,
            ).digest()
            # Eight 4-byte words per 32-byte digest.
            for offset in range(0, 32, 4):
                (word,) = struct.unpack_from(">I", digest, offset)
                components.append(float(word))
            counter += 1

        components = components[: self._dim]

        # Centre the values so the vector is not dominated by the (always
        # positive) raw hash output; without this every pair of texts has a
        # large positive cosine and retrieval degenerates to "everything is
        # similar". The subtraction is deterministic.
        mean = sum(components) / len(components)
        components = [value - mean for value in components]

        norm = sum(value * value for value in components) ** 0.5
        if norm == 0.0:  # pragma: no cover - astronomically unlikely, kept honest
            # A degenerate all-equal vector would divide by zero. Fall back to a
            # fixed unit basis vector rather than returning NaN.
            return [1.0] + [0.0] * (self._dim - 1)
        return [value / norm for value in components]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash-based deterministic embedding of each text.

        Returns exactly ``len(texts)`` vectors, each of length ``dim`` and of
        unit norm.
        """
        return [self._vector(text) for text in texts]


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
        """Embed a batch of texts via the provider API.

        The ``openai`` import lives here, not at module scope, so importing
        ``opspilot.adapters.retrieval.embeddings`` -- and therefore the whole
        package -- does not require the SDK. The list is sent as one batch
        request, which is the provider's intended usage and the cheaper path.

        Returns exactly ``len(texts)`` vectors, each of length ``dim``. Results
        are ordered by the provider's ``index`` field so the caller can rely on
        positional correspondence with the input.
        """
        from openai import AsyncOpenAI  # imported lazily; see module docstring

        if not texts:
            return []

        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.embeddings.create(model=self._model, input=texts)

        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def build_embedder(provider: str, *, api_key: str, model: str, dim: int) -> Embedder:
    """Select an embedder from config.

    ``"local"`` returns the deterministic, API-key-free embedder the test suite
    and a fresh clone use. ``"openai"`` returns the provider-backed one.

    Args:
        provider: ``"local"`` or ``"openai"``.
        api_key: The provider API key; ignored by the local embedder.
        model: The provider model name; ignored by the local embedder.
        dim: The embedding dimension.

    Returns:
        An ``Embedder``.

    Raises:
        ValueError: If ``provider`` is not one of the accepted values. The
            message names them, so a typo in ``EMBEDDING_PROVIDER`` is a clear
            startup failure rather than a silent fallback to local.
    """
    if provider == "local":
        return LocalDeterministicEmbedder(dim=dim)
    if provider == "openai":
        return ProviderEmbedder(api_key=api_key, model=model, dim=dim)
    raise UnknownEmbeddingProvider(provider)


__all__ = [
    "Embedder",
    "LocalDeterministicEmbedder",
    "ProviderEmbedder",
    "build_embedder",
]
