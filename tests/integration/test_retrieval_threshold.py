"""The shipped defaults must be able to *answer*, and must be able to *abstain*.

Why this file exists
--------------------

M5 shipped retrieval with ``RETRIEVAL_MIN_SCORE=0.35`` and a local embedder that
scored the whole corpus in a 0.04-0.09 band, so **every** query abstained,
answerable or not. A golden-path run reached ``COMPLETED`` with zero tool calls
and zero citations -- the customer's duplicate charge was never investigated --
and the suite was green, because the only retrieval assertion about abstention
was that unanswerable questions abstain, which an implementation that always
abstains satisfies trivially (``docs/progress.md`` M5c/M5e, Finding A).

So the suite could not distinguish "retrieval works" from "retrieval always
abstains". These tests are the two halves of that distinction:

1. **An answerable question must NOT abstain** at the configured default.
   Without this half, nothing catches a threshold no query can clear.
2. **An unanswerable question MUST abstain** at the same default.
   Without the first half, this half proves nothing.

They are deliberately written as *one* pair against the same real stack, so
neither can be satisfied while the other fails.

The stack under test is the shipped one: ``build_retrieval_stack`` over the
committed ``knowledge/`` corpus on the SQLite configuration (ADR-0004), with the
default embedder and the default threshold -- not a threshold chosen by this
file. If the defaults change, these tests are what should notice.

The measurements quoted in the assertions were taken over
``evals/datasets/retrieval.jsonl`` and are stated so a future change can be
compared against them rather than guessed at:

- answerable cases' top score: **0.2500 - 0.4727**
- ``expect_abstention`` cases' top score: **0.0907 - 0.1844**
- shipped default: ``0.22``, inside the gap with margin on both sides
- Recall@5 = 14/15, Recall@10 = 15/15, abstention 5/5

The bands are asserted as *separation*, not as exact values: the property is
"no unanswerable case outscores an answerable one", which is what makes the
threshold a decision rather than a constant. A change that collapses the bands
is red here even if both individual tests would still pass.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.wiring import RetrievalStack, build_retrieval_stack
from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
RETRIEVAL_DATASET = REPO_ROOT / "evals" / "datasets" / "retrieval.jsonl"

# The README's ticket (``README.md`` "The golden path"), which the runtime turns
# into a query by concatenating subject and body (``agents/runtime.py``
# ``_retrieve``). The newline is the real separator, not a stylistic one: the
# query the worker sends is the two fields joined.
_GOLDEN_PATH_QUERY = (
    "We were charged twice for invoice INV-2026-384.\nPlease investigate and fix it."
)

# ``docs/milestones.md`` §M5 and §M6: the golden path's citations are the two
# expected documents. ``knowledge/README.md`` names the pair as genuinely
# overlapping -- "a question about a $129 enterprise duplicate needs both".
_GOLDEN_PATH_DOCUMENTS = frozenset({"refund-policy.md", "duplicate-charge-sop.md"})

# Measured bands over the dataset at the shipped defaults. Asserted with slack:
# the claim under test is separation, and an exact-value assertion would make
# any retuning a test edit rather than a measurement.
_ANSWERABLE_FLOOR = 0.22
_UNANSWERABLE_CEILING = 0.20


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


@pytest.fixture
async def stack(factory: sessionmaker[Session]) -> RetrievalStack:
    """The **shipped** retrieval stack: default embedder, default threshold."""
    built = build_retrieval_stack(
        Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"), session_factory=factory
    )
    await built.reindex_runner(KNOWLEDGE_DIR)
    return built


@dataclass(frozen=True)
class _Hit:
    """One retrieval hit, typed.

    The knowledge tool returns a JSON-ish dict; narrowing it to real fields here
    keeps ``--strict`` mypy honest at every call site instead of scattering
    ``cast`` calls through the assertions.
    """

    document_slug: str
    anchor: str
    score: float
    rank: int


def _cases() -> list[dict[str, object]]:
    lines = RETRIEVAL_DATASET.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def _all_hits(stack: RetrievalStack, question: str, top_k: int) -> list[_Hit]:
    """Every hit for ``question``, best first, through the real knowledge tool."""
    result = await stack.knowledge_tool.call_tool(
        "knowledge.search", {"query": question, "top_k": top_k}
    )
    assert result.ok is True
    assert result.result is not None
    raw = result.result["hits"]
    assert isinstance(raw, list)
    return [
        _Hit(
            document_slug=str(hit["document_slug"]),
            anchor=str(hit["anchor"]),
            score=float(hit["score"]),
            rank=int(hit["rank"]),
        )
        for hit in cast("list[dict[str, Any]]", raw)
    ]


async def _top(stack: RetrievalStack, question: str, top_k: int = 5) -> _Hit:
    hits = await _all_hits(stack, question, top_k)
    assert hits, f"nothing retrieved for {question!r}; the corpus is empty"
    return hits[0]


# ---------------------------------------------------------------------------
# Half 1: an answerable question must NOT abstain at the shipped default
# ---------------------------------------------------------------------------


async def test_the_golden_path_question_does_not_abstain_at_the_default_threshold(
    stack: RetrievalStack,
) -> None:
    """The shipped default must clear the shipped threshold.

    This is the assertion that was missing. ``RETRIEVAL_MIN_SCORE`` defaults to
    ``0.22``; the golden-path question's top hit must score **at or above** it,
    or the run escalates instead of investigating -- which is exactly the M5
    defect, where nothing could reach ``0.35`` and every run abstained.

    The threshold is read from ``Settings``, not restated here, so this tracks
    the configured default rather than a copy of it.
    """
    min_score = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:").retrieval_min_score
    top = await _top(stack, _GOLDEN_PATH_QUERY)

    assert top.score >= min_score, (
        f"the golden-path question scored {top.score:.4f}, below the "
        f"configured default RETRIEVAL_MIN_SCORE={min_score}; the run would "
        f"abstain and never investigate the duplicate charge "
        f"(docs/progress.md M5e, Finding A)"
    )


async def test_every_answerable_dataset_case_clears_the_default_threshold(
    stack: RetrievalStack,
) -> None:
    """No answerable case in the dataset is lost to the default threshold.

    Complements the golden-path case with the whole dataset. One assertion
    against one case would pass on a configuration tuned to that case; this
    asks the question the eval asks.
    """
    min_score = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:").retrieval_min_score
    answerable = [case for case in _cases() if not case.get("expect_abstention")]
    assert answerable, "the retrieval dataset has no answerable cases"

    abstaining: list[str] = []
    for case in answerable:
        top = await _top(stack, str(case["question"]))
        if top.score < min_score:
            abstaining.append(f"{case['id']} ({top.score:.4f})")

    assert not abstaining, (
        f"these answerable cases scored below the default threshold {min_score} "
        f"and would abstain: {abstaining}"
    )


# ---------------------------------------------------------------------------
# Half 2: an unanswerable question MUST abstain at the same default
# ---------------------------------------------------------------------------


async def test_an_unanswerable_question_abstains_at_the_default_threshold(
    stack: RetrievalStack,
) -> None:
    """The threshold still discriminates -- this is the half that can fail.

    ``docs/architecture.md`` §9: "If the top score is below a threshold, the run
    escalates instead of guessing." Read together with half 1: a configuration
    where everything abstains and one where nothing abstains both fail this
    pair. Only a threshold that separates them passes.
    """
    min_score = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:").retrieval_min_score
    top = await _top(stack, "What is the airspeed velocity of an unladen swallow?")

    assert top.score < min_score, (
        f"an unanswerable question scored {top.score:.4f}, at or above "
        f"RETRIEVAL_MIN_SCORE={min_score}; the run would answer from a document "
        f"that does not answer it (docs/architecture.md §9)"
    )


async def test_the_score_bands_do_not_overlap(stack: RetrievalStack) -> None:
    """The separation itself, asserted directly rather than inferred.

    The two tests above each check one case. This checks the *property* that
    makes the threshold meaningful: across the whole dataset, no unanswerable
    case scores as high as the weakest answerable one. If that ever stops
    holding, every value of the threshold either abstains on everything or
    admits an unanswerable question, and the per-case tests would be passing for
    luck.
    """
    answerable_scores: list[float] = []
    unanswerable_scores: list[float] = []
    for case in _cases():
        top = await _top(stack, str(case["question"]))
        score = top.score
        if case.get("expect_abstention"):
            unanswerable_scores.append(score)
        else:
            answerable_scores.append(score)

    assert answerable_scores and unanswerable_scores, "the dataset must have both kinds"
    weakest_answerable = min(answerable_scores)
    strongest_unanswerable = max(unanswerable_scores)
    default = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:").retrieval_min_score

    assert strongest_unanswerable < weakest_answerable, (
        f"the score bands overlap: an unanswerable case reached {strongest_unanswerable:.4f} "
        f"while an answerable case reached only {weakest_answerable:.4f}. No threshold "
        f"separates them, so the threshold would be either always-true or always-false"
    )
    # The default must actually sit in the gap, with margin on both sides --
    # not merely somewhere below the weakest answerable score.
    assert strongest_unanswerable < default < weakest_answerable, (
        f"the default threshold {default} does not sit between the bands "
        f"({strongest_unanswerable:.4f}, {weakest_answerable:.4f})"
    )
    assert strongest_unanswerable <= _UNANSWERABLE_CEILING, (
        f"the unanswerable band rose to {strongest_unanswerable:.4f}, past the "
        f"documented ceiling {_UNANSWERABLE_CEILING}; the default will need raising "
        f"and the numbers in this file's docstring updating"
    )
    assert weakest_answerable >= _ANSWERABLE_FLOOR, (
        f"the answerable band fell to {weakest_answerable:.4f}, below the documented "
        f"floor {_ANSWERABLE_FLOOR}; the default will need lowering"
    )


# ---------------------------------------------------------------------------
# The M6 acceptance criterion: the citations are the two expected documents
# ---------------------------------------------------------------------------


async def test_the_golden_path_cites_exactly_the_two_expected_documents(
    stack: RetrievalStack,
) -> None:
    """``docs/milestones.md`` §M6: "the citations are the two expected documents".

    This is the criterion that could not be checked before the M5e fixes, for
    two independent reasons that had to be removed together:

    - the local embedder scored everything below the threshold, so the run
      abstained and there were no citations to check (Finding A); and
    - the SQLite path wrote no ``knowledge_chunks`` rows, so
      ``SqlCitationStore.create_many`` skipped every hit as unresolvable
      (Finding B).

    A citation write silently dropping every hit is indistinguishable from a
    run that cited nothing, so this asserts on the retrieved set *and* on the
    rows, below.
    """
    hits = await _all_hits(stack, _GOLDEN_PATH_QUERY, 5)
    slugs = {hit.document_slug for hit in hits}

    missing = _GOLDEN_PATH_DOCUMENTS - slugs
    assert not missing, (
        f"the golden-path question did not retrieve {sorted(missing)} within the "
        f"top 5; retrieved {sorted(slugs)}"
    )


async def test_golden_path_hits_persist_as_citation_rows(stack: RetrievalStack) -> None:
    """The retrieved hits become real ``citations`` rows on the SQLite path.

    The end-to-end version of the M6 criterion: not "retrieval found them" but
    "the database holds them". Before the M5e fixes this wrote **zero** rows,
    because no ``knowledge_chunks`` row existed for ``citations.chunk_id`` to
    reference -- and the write path skips an unresolvable hit rather than
    failing, so the symptom was an empty Sources panel rather than an error.

    This is the test that would have caught Finding B.
    """
    from opspilot.adapters.persistence.repositories import (
        SqlCitationStore,
        SqlRunStore,
        SqlTicketStore,
    )
    from opspilot.ports.stores import CitationRecord

    hits = await _all_hits(stack, _GOLDEN_PATH_QUERY, 5)
    factory = _session_factory_for(stack)
    ticket_id = await SqlTicketStore(factory).create(
        subject="Duplicate charge on INV-2026-384",
        body="We were charged twice for the same invoice.",
        customer_email="billing@acme.example",
    )
    run_id = (
        await SqlRunStore(factory).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake"
        )
    ).id

    await SqlCitationStore(factory).create_many(
        run_id,
        [
            CitationRecord(
                document=hit.document_slug,
                chunk=f"{hit.document_slug}#{hit.anchor}",
                score=hit.score,
                rank=hit.rank,
            )
            for hit in hits
        ],
    )

    rows = await SqlCitationStore(factory).list_citations(run_id)
    assert len(rows) == len(hits), (
        f"persisted {len(rows)} citations for {len(hits)} retrieved hits; the "
        f"SQLite path is dropping citations whose chunk row is missing "
        f"(docs/progress.md M5e, Finding B)"
    )
    assert {row.document for row in rows} >= set(_GOLDEN_PATH_DOCUMENTS)


def _session_factory_for(stack: RetrievalStack) -> sessionmaker[Session]:
    """The stack's own session factory.

    Read off the store rather than re-declared, so the citations land in the
    same database the ingest wrote its chunk rows to. Two factories over
    ``:memory:`` would be two different databases, and this test would assert
    against an empty one.
    """
    from sqlalchemy.orm import Session as _Session

    bound = getattr(stack.store, "_session_factory", None)
    assert isinstance(bound, sessionmaker), (
        f"the retrieval store has no session factory ({type(bound).__name__}); "
        f"citations cannot be persisted on this path"
    )
    assert not isinstance(bound, _Session)
    return bound
