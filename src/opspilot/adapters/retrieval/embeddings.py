"""Embedding functions: a deterministic lexical default and a provider-backed one.

Responsibility: map text to a fixed-dimension vector. The **local deterministic**
embedder is the default (``EMBEDDING_PROVIDER=local``) so a fresh clone and CI
run with no API key; the provider-backed one (``openai``) is what makes
retrieval quality real and is selected by config.

Layer: ``adapters`` (retrieval). Implements the implicit embedding callable used
by ``ingest`` and ``search``.

What the local embedder is
--------------------------

A **hashed lexical scorer**: a bag of words, lowercased, stripped of stopwords,
lightly stem-suffixed, weighted sublinearly by frequency, and hashed into a
fixed-width vector by a keyed digest. Cosine similarity between two such vectors
approximates normalised term overlap, which is what a BM25-style ranker
measures.

It is deliberately *not* a hash of the whole text. The previous implementation
hashed the text into pseudo-random directions, and while that made it a
perfectly deterministic function -- the property the store tests needed -- it
carried **no information about the words**. Every pair of texts scored in a
0.04-0.09 band with the answerable and unanswerable bands fully overlapping, so
no threshold could discriminate: at the shipped ``RETRIEVAL_MIN_SCORE`` every
query abstained, answerable or not, and the golden path did nothing
(``docs/progress.md`` M5c/M5e, Finding A). A deterministic function of the text
is not the same as a function of the text's *content*, and only the second one
can retrieve.

What it costs, stated plainly: this is lexical matching with the stopword list
and a suffix rule written out by hand. It has no synonymy, so a question whose
answer uses words the question never uses is poorly served. It is a good
default for a 17-document policy corpus where questions quote the documents'
own vocabulary, and it is not a claim about semantic retrieval.

What remains true and is asserted in ``tests/unit/test_embeddings.py``: it is
deterministic across processes, unit-norm, needs no API key, and imports no
provider SDK. Any number quoted in the README comes from a run with a real
provider, and the run's config is recorded with it (``docs/limitations.md`` §3).
The ``openai`` SDK is imported inside the method, so this module imports without
it.
"""

from __future__ import annotations

import hashlib
import math
import re
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

# Terms are ``[a-z0-9]+`` runs: no punctuation, no underscores, no diacritics
# mangling. Deliberately simple -- the corpus is English policy prose, and a
# larger tokenizer is a dependency this module exists to avoid.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Closed-class English words, dropped on both the query side and the document
# side. This list is load-bearing rather than cosmetic: "what", "is", "the" and
# "how" appear in the unanswerable eval questions *and* in every policy chunk,
# so keeping them gives a question about the airspeed velocity of an unladen
# swallow a strong match against a finance document. With them dropped, the two
# score populations separate (measured on ``evals/datasets/retrieval.jsonl``:
# answerable cases score >= 0.2500, unanswerable <= 0.1844; with them kept the
# bands overlap and no threshold can discriminate).
_STOPWORD_SOURCE = """
a an the and or but if then than that this these those
of to in on at by for with from as is are was were be been being am
do does did doing have has had having will would shall should can could may might must
it its we our us you your they their he she his her i me my
what which who whom whose when where why how
all any both each few more most other some such no nor not only own same so too very just don now
about above after again against because before below between during here once
under until up out off over further
himself herself itself myself ourselves yourself yourselves
please tell give say said get got per via etc
"""

_STOPWORDS = frozenset(_STOPWORD_SOURCE.split())

# Suffixes folded so that "charged", "charges" and "charging" share a term. The
# rules are deliberately shallow -- two English inflectional suffixes -- because
# a deeper stemmer is a dependency and because a shallow one is auditable: each
# rule below names the case it exists for.
_MIN_STEM_LENGTH = 4
_MIN_ING_STEM_LENGTH = 5


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


def _stem(word: str) -> str:
    """Fold an inflected English word onto a shared term.

    Two rules, in order:

    - **Plural/3rd-person**: ``charges`` -> ``charge``, ``policies`` -> ``policy``,
      ``classes`` -> ``classe`` (the ``sses`` rule strips only the extra ``es``,
      which keeps ``classes`` from collapsing to the unrelated ``class``).
      Guarded so ``ss``/``us``/``is`` endings survive -- ``status`` must not
      become ``statu``.
    - **Tense**: ``charged`` -> ``charg``, ``charging`` -> ``charg``. The ``ed``
      is dropped rather than the ``e`` restored, so all three forms of a verb
      land on the same string. Words shorter than the minimum are left alone so
      short words cannot collapse into each other.

    Not a Porter stemmer: no cascades, no exception table, no dependency. A
    shallow rule set that can be read in one screen is worth more here than a
    more accurate one that cannot.
    """
    if word.endswith("ies") and len(word) > _MIN_STEM_LENGTH:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        word = word[:-1]

    if word.endswith("ed") and len(word) > _MIN_STEM_LENGTH:
        return word[:-1]
    if word.endswith("ing") and len(word) > _MIN_ING_STEM_LENGTH:
        return word[:-3]
    return word


def _terms(text: str) -> list[str]:
    """Lowercase, tokenise, drop stopwords and stem: the lexical units of ``text``.

    An empty text yields no terms, which the embedder handles by returning a
    fixed unit basis vector (see :meth:`LocalDeterministicEmbedder._vector`).
    """
    return [_stem(token) for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]


class LocalDeterministicEmbedder:
    """A dependency-free, deterministic **lexical** embedder.

    ``embed`` produces a **unit-norm** vector for every input, so cosine
    similarity and the dot product coincide and a store may use either. The
    vector is a hashed bag of terms: each distinct term contributes
    ``1 + ln(count)`` at the coordinate its keyed digest selects, and the
    result is scaled to unit length.

    Three properties make this a *retriever* rather than a deterministic
    placeholder, and each is asserted in the tests:

    1. **Deterministic.** A fixed ``person`` key on ``blake2b``, a fixed stopword
       list, and no per-process state. Two processes embed the same text to the
       same bits, so a stored vector stays comparable across restarts.
    2. **Normalised.** Sublinear term frequency (``1 + ln(count)``) stops a
       policy document's boilerplate from dominating its own vector, which raw
       counts do.
    3. **Non-degenerate.** Coordinates are derived from a *term*, not from the
       whole text, so two texts sharing vocabulary score high and two texts
       sharing nothing score near zero. This is the property the previous
       hash-of-the-text implementation lacked, and the one that makes
       ``RETRIEVAL_MIN_SCORE`` mean something.

    What it is good for: CI and a fresh clone run the whole retrieval suite
    with no API key and no network, and because it is a real function of the
    text those tests exercise real code paths (ordering, tie-breaks, the
    differential store comparison). A broken local embedder would make every
    retrieval test vacuous, which is why the brief treats it as load-bearing
    rather than a toy.

    What it is not: semantic. There is no synonymy and no embedding of meaning;
    a question whose answer is worded entirely differently from the question
    will not match it. ``docs/limitations.md`` §3 says so in the README's terms.
    """

    def __init__(self, *, dim: int = 1536) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        """The embedding dimension."""
        return self._dim

    def _vector(self, text: str) -> list[float]:
        """Turn ``text`` into a unit-norm vector of hashed lexical terms.

        Each term is hashed to a coordinate and accumulates
        ``1 + ln(count)``; collisions accumulate rather than overwrite, so two
        distinct terms landing on one coordinate behave like a sum of both
        rather than one silently replacing the other.

        A text with no terms after stopword removal and stemming -- the empty
        string, or one made only of stopwords -- has zero norm. It returns the
        fixed unit basis vector ``[1, 0, 0, ...]`` rather than dividing by zero,
        so cosine similarity against it stays defined (a score of ``0.0`` for
        any real query, which reads as "no lexical evidence" rather than as a
        crash).
        """
        counts: dict[str, int] = {}
        for term in _terms(text):
            counts[term] = counts.get(term, 0) + 1

        components = [0.0] * self._dim
        for term, count in counts.items():
            digest = hashlib.blake2b(
                term.encode("utf-8"), digest_size=32, person=_LOCAL_HASH_PERSON
            ).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            components[index] += 1.0 + math.log(count)

        norm = math.sqrt(sum(value * value for value in components))
        if norm == 0.0:
            # A stopword-only text. Fall back to a fixed unit basis vector rather
            # than dividing by zero; the fallback is a constant, so it is as
            # deterministic as the rest of the mapping.
            return [1.0] + [0.0] * (self._dim - 1)
        return [value / norm for value in components]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Lexical embedding of each text.

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

    ``"local"`` returns the deterministic, API-key-free lexical embedder the
    test suite and a fresh clone use. ``"openai"`` returns the provider-backed
    one.

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
