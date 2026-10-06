"""Every dataset label must resolve against the code it is scored against.

``evals/datasets/*.jsonl`` was written in M0, before there was any code to
score it against, and nothing ever checked the two against each other. The
result survived four milestones: ``classification.jsonl`` expects
``billing_dispute``, ``account_access`` and ``technical_issue`` -- values named
in ``README.md``, ``docs/milestones.md`` section M6, ``docs/evals.md`` section 1
and ``docs/api-contract.md`` section 3 -- and **none of them was a member of**
:class:`~opspilot.agents.schemas.TicketCategory`. Seventeen of twenty cases
expected a value the model was structurally incapable of emitting, so the
classification metric could not score above 15% on a correct implementation.

The defect survived because no test *read* the dataset. A fixture nothing
executes cannot disagree with the implementation, which is this project's
recurring failure and the M5d defect one level up: M5d found a dataset whose
document slugs did not exist, this is a dataset whose label vocabulary did not
exist. A guard that checks a dataset against the thing it describes is the only
defence, and there was none.

The datasets are therefore checked against the things they describe, by
*reading* them -- never by transcribing them, so a case added tomorrow is
covered by the same loop:

``classification.jsonl`` labels must be emittable
    ``expected_category`` must be a ``TicketCategory``. This is the guard that
    would have caught the defect, and it is the one that matters.

Every category the *specification* names must be emittable
    The documents name categories in examples rather than in a vocabulary list,
    so this direction is what actually broke: three named categories were not
    members.

Every category the enum can emit must be reachable
    The reverse direction. A member no document, fixture or test ever names is
    dead vocabulary -- see ``test_no_category_is_unreferenced`` for the two
    that exist today and why they are pinned rather than asserted away.

Tool names, run statuses, document slugs and scenario names
    The same class of problem in the other three datasets. All four currently
    resolve; all four would fail loudly the day they stopped.

Category vocabulary is read out of the documents rather than hard-coded, so the
guard is a *relationship* and not a snapshot. A hard-coded list here would pass
forever after the one edit that mattered, which is precisely the test that
would have let the original defect through.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

from opspilot.agents.schemas import TicketCategory
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import TOOL_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
FIXTURES_DIR = DATASETS_DIR / "fixtures"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


def _document(relpath: str) -> str:
    """One specification document's text, asserted to exist."""
    path = REPO_ROOT / relpath
    assert path.is_file(), f"{relpath} is missing; the category guard needs it"
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    """The text between two headings, so a document-wide scan cannot wander."""
    assert start in text, f"heading {start!r} not found"
    tail = text[text.index(start) :]
    return tail[: tail.index(end)] if end in tail else tail


def _categories_named_by_the_specification() -> dict[str, set[str]]:
    """Categories each specification document names, read out of that document.

    Each extractor is anchored to the syntactic position where that document
    names a category -- a JSON value, the trace's ``Classify`` row, a sentence
    that says the word. A free scan for snake_case tokens would return field
    names (``expected_category``, ``must_not_propose``) and step verbs
    (``retrieve``), and would make the agreement test meaningless.
    """
    found: dict[str, set[str]] = {}

    # The README's golden-path trace: ``Classify   billing_dispute   0.94``.
    readme = _document("README.md")
    found["README.md"] = set(re.findall(r"^Classify\s+([a-z][a-z0-9_]*)", readme, re.M))

    # ``docs/milestones.md`` section M6, first criterion: the documented
    # sequence begins with the classification.
    milestones = _document("docs/milestones.md")
    m6 = _section(milestones, "## M6 ", "## M7 ")
    found["docs/milestones.md M6"] = set(
        re.findall(r"exact documented sequence:\s*\n\s*`([a-z][a-z0-9_]*)`", m6)
    )

    # The API contract's example response body.
    api = _document("docs/api-contract.md")
    found["docs/api-contract.md"] = set(re.findall(r'"category"\s*:\s*"([a-z][a-z0-9_]*)"', api))

    # ``docs/evals.md``: the JSON example, plus the prose immediately after it
    # ("a billing dispute that mentions an API outage is still ``billing_
    # dispute``, not ``technical_issue``").
    evals = _document("docs/evals.md")
    classification = _section(evals, "### `classification.jsonl`", "### `retrieval.jsonl`")
    from_json = set(re.findall(r'"expected_category"\s*:\s*"([a-z][a-z0-9_]*)"', classification))
    from_prose = {
        named
        for line in classification.splitlines()
        if "→" in line or "categ" in line.lower()
        for named in re.findall(r"`([a-z][a-z0-9_]*)`", line)
        if named != "expected_category"
    }
    found["docs/evals.md s1"] = from_json | from_prose

    # Two further documents name categories in a position unambiguous enough to
    # read: a parenthetical introduced by "Other categories", and the dataset
    # table row describing what the classification metric covers.
    limitations = _document("docs/limitations.md")
    agent_behaviour = _section(limitations, "## 4. Agent behaviour", "## 5. ")
    found["docs/limitations.md s4"] = {
        token
        for line in agent_behaviour.splitlines()
        if "Other categories" in line
        for token in re.findall(r"`([a-z][a-z0-9_]*)`", line)
    }

    evals_readme = _document("evals/README.md")
    found["evals/README.md"] = {
        token
        for line in evals_readme.splitlines()
        if "Category accuracy" in line
        for token in re.findall(r"`([a-z][a-z0-9_]*)`", line)
    }

    return found


def _dataset_cases(name: str) -> list[dict[str, Any]]:
    """One dataset's cases, read from the file rather than transcribed."""
    path = DATASETS_DIR / name
    assert path.is_file(), f"{name} is missing from {DATASETS_DIR}"
    lines = path.read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    assert cases, f"{name} parsed to zero cases; the guard is inspecting nothing"
    return cases


def _fixture_categories() -> set[str]:
    """Categories the committed replay fixtures actually emit."""
    emitted: set[str] = set()
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for call in payload.get("calls", []):
            if call.get("schema") != "TicketClassification":
                continue
            category = call.get("response", {}).get("value", {}).get("category")
            if isinstance(category, str):
                emitted.add(category)
    return emitted


def _categories_named_under_tests() -> set[str]:
    """Category values the Python tests name as *enum members*.

    A category is referenced in code by ``TicketCategory.DUPLICATE_CHARGE`` --
    the shape the tests use to construct a ``TicketClassification``. Only that
    shape counts here. Scanning for bare string literals anywhere under
    ``tests/`` was tried and rejected in both directions: it reported
    ``billing_other`` as unreferenced when
    ``tests/security/test_prompt_injection.py`` genuinely uses it (a guard that
    invents a dead category is worse than no guard, because the "fix" is to
    delete a member that is in use), and it simultaneously reported
    ``technical`` and ``account`` as referenced, because the English word
    "technical" appears in test docstrings -- prose, not vocabulary.
    """
    named: set[str] = set()
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for member in re.findall(r"TicketCategory\.([A-Z][A-Z0-9_]*)", text):
            try:
                named.add(TicketCategory[member].value)
            except KeyError:
                continue
    return named


# ---------------------------------------------------------------------------
# The guard that would have caught the M6d defect
# ---------------------------------------------------------------------------


def test_every_dataset_category_is_a_member_of_the_enum() -> None:
    """Every ``expected_category`` in ``classification.jsonl`` is emittable.

    The metric is ``predicted_category == expected_category`` over this file
    (``docs/evals.md`` section 1, metric 1). A label the enum cannot hold is a
    label the model cannot produce, so the case is unwinnable and the metric is
    capped below 100% no matter how good the implementation is.
    """
    unknown: list[str] = []
    for case in _dataset_cases("classification.jsonl"):
        assert "expected_category" in case, (
            f"{case.get('id')}: no expected_category. A case that does not "
            "declare what it expects would be scored against nothing, and "
            "would slip past this guard."
        )
        expected = case["expected_category"]
        assert isinstance(expected, str), f"{case['id']}: expected_category is not a string"
        if expected not in set(TicketCategory):
            unknown.append(f"{case['id']}={expected!r}")

    assert not unknown, (
        "classification.jsonl expects categories that TicketCategory cannot "
        f"emit: {unknown}. Either the enum is missing the category or the "
        "dataset is naming a vocabulary the system does not have. Fix the "
        "smaller side and make both agree -- do not delete the cases."
    )


def test_the_classification_dataset_exercises_the_vocabulary() -> None:
    """The dataset is not vacuous, and its cases are not all one label.

    A guard that reads one case would report the defect above only if that case
    happened to be the broken one. Two assertions: the dataset has the number of
    cases section 2 specifies, and it uses more than one category -- a dataset
    of twenty ``duplicate_charge`` rows would pass the guard above while
    measuring nothing about the vocabulary at all.
    """
    cases = _dataset_cases("classification.jsonl")
    assert len(cases) >= 20, (
        f"classification.jsonl has {len(cases)} cases, not the ~20 "
        "docs/evals.md section 2 specifies"
    )
    used = {str(case["expected_category"]) for case in cases}
    assert len(used) >= 4, (
        f"classification.jsonl uses only {len(used)} categories ({sorted(used)}); "
        "the dataset cannot measure a vocabulary it does not exercise"
    )


# ---------------------------------------------------------------------------
# The enum and the specification
# ---------------------------------------------------------------------------


def test_every_category_the_specification_names_is_emittable() -> None:
    """A category named in a document must be a member of ``TicketCategory``.

    ``README.md``, ``docs/milestones.md`` section M6, ``docs/evals.md`` section 1
    and ``docs/api-contract.md`` section 3 all show ``billing_dispute`` as the
    golden path's classification, and ``docs/evals.md`` also shows
    ``account_access`` and ``technical_issue``. A document can name a value the
    system cannot produce -- that is exactly the M6d defect, seen from the
    document side rather than the dataset side.
    """
    not_emittable: dict[str, list[str]] = {}
    for source, categories in _categories_named_by_the_specification().items():
        missing = sorted(c for c in categories if c not in set(TicketCategory))
        if missing:
            not_emittable[source] = missing

    assert not not_emittable, (
        "these documents name categories the model cannot emit: "
        f"{not_emittable}. Either the documents name a vocabulary that does "
        "not exist, or TicketCategory is missing members it should have."
    )


def test_the_specification_guards_are_reading_something() -> None:
    """Each document extractor returns at least one category.

    An extractor whose pattern stops matching returns an empty set, and an empty
    set passes ``test_every_category_the_specification_names_is_emittable``
    vacuously. That is this project's "guard that inspects nothing" failure, and
    the fix is to make it fail rather than to trust it.
    """
    empty = sorted(
        source
        for source, categories in _categories_named_by_the_specification().items()
        if not categories
    )
    assert not empty, (
        f"these document extractors returned no categories: {empty}. Either a "
        "document changed or a regex stopped matching, and an empty result "
        "would make the agreement test pass without inspecting anything."
    )


def test_no_category_is_unreferenced() -> None:
    """Every enum member is named by a document, a fixture or a test.

    The reverse of the agreement test. Widening an enum is easy to do for the
    wrong reason, and the failure mode is a category nothing can ever produce
    and nothing is ever scored against -- invisible, because an unused enum
    member breaks no assertion.

    **Two members are pinned rather than asserted away.** ``technical`` and
    ``account`` are named by no document, no fixture and no test: they are M0
    vocabulary that the four documents and all seventy dataset cases stepped
    over when they adopted the wider labels ``technical_issue`` and
    ``account_access``. They are listed here so that a *new* member added
    without a name still fails, and so the two dead ones stay visible in front
    of whoever decides whether to document or remove them. Widening the enum is
    not a licence to add an unused one.
    """
    documented: set[str] = set()
    for categories in _categories_named_by_the_specification().values():
        documented |= categories

    known_dead = {"technical", "account"}
    reachable = documented | _fixture_categories() | _categories_named_under_tests()
    unreferenced = sorted(set(TicketCategory) - reachable)

    assert unreferenced == sorted(known_dead), (
        "TicketCategory has members no document, fixture or test names: "
        f"{unreferenced}. A category nothing can produce and nothing is scored "
        "against is invisible -- it breaks no assertion and measures nothing. "
        f"Only {sorted(known_dead)} are known to be unreferenced; anything else "
        "here is either a dead category or a rename that has not propagated."
    )


# ---------------------------------------------------------------------------
# The same class of problem in the other three datasets
# ---------------------------------------------------------------------------


def test_every_tool_named_in_a_dataset_is_in_the_tool_registry() -> None:
    """Every tool name a dataset expects is a real, registered tool.

    ``docs/evals.md`` section 1 metric 4 is "set equality of proposed tool names
    vs expected". An expected name outside ``TOOL_REGISTRY`` can never be
    proposed -- gate 2 rejects it -- so the case is unwinnable in the same way
    an unemittable category is. The same check covers ``safety.jsonl``'s
    ``expected_write``.
    """
    registered = set(TOOL_REGISTRY)
    unknown: list[str] = []
    for name in ("tool_selection.jsonl", "safety.jsonl"):
        for case in _dataset_cases(name):
            for field in ("expected_tools", "must_not_propose", "expected_write"):
                value = case.get(field)
                if not value:
                    continue
                names = [value] if isinstance(value, str) else list(value)
                for tool in names:
                    if tool not in registered:
                        unknown.append(f"{case['id']}.{field}={tool!r}")

    assert not unknown, (
        f"datasets name tools that are not in TOOL_REGISTRY: {unknown}. Gate 2 "
        "would reject every proposal for them, so the metric is capped."
    )


def test_every_expected_terminal_status_is_a_real_run_status() -> None:
    """``expected_terminal`` in ``safety.jsonl`` names a ``RunStatus`` value.

    Metric 8 in ``docs/evals.md`` section 1 counts "runs reaching ``COMPLETED``
    with the expected terminal outcome". The expected outcome is compared
    against ``AgentRun.status``, whose values come from ``RunStatus`` -- a typo
    here is a case that fails forever for a reason no run can act on.
    """
    statuses = {status.value for status in RunStatus}
    unknown = [
        f"{case['id']}={case['expected_terminal']!r}"
        for case in _dataset_cases("safety.jsonl")
        if case.get("expected_terminal") is not None and case["expected_terminal"] not in statuses
    ]
    assert not unknown, f"safety.jsonl expects run statuses that do not exist: {unknown}"


@pytest.mark.parametrize(
    ("dataset", "field"),
    [
        ("retrieval.jsonl", "expected_documents"),
        ("safety.jsonl", "knowledge_injection"),
    ],
)
def test_every_document_slug_named_in_a_dataset_exists(dataset: str, field: str) -> None:
    """Every ``.md`` slug a dataset names is a file in the committed corpus.

    This is the M5d defect's shape: a dataset naming documents that do not
    exist. ``retrieval.jsonl``'s slugs were covered incidentally, because
    Recall@K can never retrieve a file that is not there and the retrieval
    integration test fails. ``safety.jsonl``'s ``knowledge_injection`` slugs
    were **not** covered by anything -- the injection tests synthesise their
    own ``SearchHit``, so a slug that does not exist costs nothing, while the
    "retrieved injection" premise silently never occurs (M5e's F3).
    """
    corpus = {path.name for path in KNOWLEDGE_DIR.glob("*.md")}
    assert corpus, f"no documents found in {KNOWLEDGE_DIR}"

    missing: list[str] = []
    for case in _dataset_cases(dataset):
        value = case.get(field)
        if not value:
            continue
        slugs = [value] if isinstance(value, str) else list(value)
        for slug in slugs:
            if slug not in corpus:
                missing.append(f"{case['id']}.{field}={slug!r}")

    assert not missing, (
        f"{dataset} names documents that are not in {KNOWLEDGE_DIR.name}/: "
        f"{missing}. A case expecting a document that does not exist can never "
        "be satisfied, and an injection case naming one never exercises the "
        "path it claims to."
    )


def test_the_safety_dataset_setup_keys_name_real_scenarios() -> None:
    """``setup`` keys name scenarios something knows how to arrange.

    ``safe-010`` asks for ``{"already_refunded": true}``. That is a claim about
    the eval harness: it says a precondition can be created before the run. A
    key nothing honours makes the case a no-op that still reports a pass.
    """
    scenarios = {path.stem for path in FIXTURES_DIR.glob("*.json")}
    unknown = [
        f"{case['id']}.setup.{key}"
        for case in _dataset_cases("safety.jsonl")
        for key in case.get("setup", {})
        if key not in scenarios
    ]
    assert not unknown, (
        f"safety.jsonl asks for setup scenarios with no fixture: {unknown}. "
        f"Every setup key must name a scenario under "
        f"{FIXTURES_DIR.relative_to(REPO_ROOT)}/, or the case silently does not "
        "test what it claims."
    )
