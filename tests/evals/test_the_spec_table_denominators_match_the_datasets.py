"""The §4 table's denominators must be the datasets' denominators.

## Why this file exists

``docs/evals.md`` §4 prints the metric table the runner produces, and
``tests/evals/test_eval_cli.py`` asserts that table **character for character**
against the spec's fenced block. That is a strong guarantee about *format*.

It is not a guarantee about *content*, and the content drifted: the spec's
denominators were 4 for ``abstention correctness``, 15 for
``approval-policy compliance`` and 12 for ``task completion``, while the
datasets say 5, 13 and 9. Three of the nine rows were numbers no run over these
datasets could print.

**Why it survived a character-for-character guard.** The CLI test drives the
real CLI with a *double* for ``runner`` and ``metrics``, and that double is
written "spec-shaped" -- it returns the numbers the spec prints. So the spec
said 4, the double said 4, and the test agreed. A guard that compares the
spec against a copy of the spec cannot notice the spec being wrong; only a
guard that compares the spec against **the datasets** can.

This file is that guard. It reads the datasets and the spec, and nothing else.

## What the denominators are

Each row's ``cases`` column is a property of the *dataset*, not of the run,
except where a run decides it. The rules:

| Row | Denominator |
|---|---|
| ``classification`` | the rows of ``classification.jsonl`` |
| ``retrieval recall@5``, ``retrieval precision@5`` | the rows of ``retrieval.jsonl`` |
| ``abstention correctness`` | the ``retrieval`` rows declaring ``expect_abstention`` |
| ``tool selection`` | the rows of ``tool_selection.jsonl`` |
| ``tool argument validity`` | **calls, not cases**; the count is in the row's detail |
| ``approval-policy compliance`` | the **live** ``safety`` rows (the ones a script did not answer) |
| ``unsafe execution count`` | every ``safety`` row, injections included |
| ``task completion`` | the live ``safety`` rows declaring ``expected_terminal`` |

**A *live* safety row is one no script answered.** The injection cases carry a
``knowledge_injection`` -- a document that orders the run to comply, and that
the harness therefore answers with a scripted provider
(``src/opspilot/evals/runner.py``). ``safe-007`` and ``safe-008`` are the two
such rows today, and the metric that counts them is
``synthetic_approval_policy_compliance``, which the table shows as the
``(2/2 synthetic)`` companion rather than folding into the live figure.

**The two run-dependent rows are excluded on purpose.** The live figure of
``approval-policy compliance`` counts the live rows that *proposed* a
high-risk tool, so a model that proposes none would print a smaller number
than this guard expects. The table's example is a run where every live safety
row proposed one, which is the case the spec is illustrating; the guard
therefore checks the **upper bound** (the live row count) for that row rather
than an exact figure, and checks the exact figure everywhere the dataset
decides it.
"""

from __future__ import annotations

import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASETS = REPO_ROOT / "evals" / "datasets"
SPEC = REPO_ROOT / "docs" / "evals.md"

#: The rows whose ``cases`` column the datasets decide outright.
#: Label -> a callable taking the loaded cases and returning the denominator.
_EXACT: dict[str, str] = {
    "classification": "classification",
    "retrieval recall@5": "retrieval",
    "retrieval precision@5": "retrieval",
    "abstention correctness": "abstention",
    "tool selection": "tool_selection",
    "unsafe execution count": "safety",
    "task completion": "task_completion",
}

#: The live figure of this row is bounded by the live safety rows: a model
#: that proposes no high-risk tool prints a smaller number, and the table's
#: example prints the bound.
_BOUNDED = ("approval-policy compliance",)

#: Rows that are not denominated in cases at all.
_OTHER = ("tool argument validity",)


def _rows(name: str) -> list[dict[str, object]]:
    """One dataset's rows."""
    path = DATASETS / f"{name}.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _is_live(case: dict[str, object]) -> bool:
    """Whether a safety row was answered by the endpoint rather than a script."""
    return not case.get("knowledge_injection")


def _expected() -> dict[str, int]:
    """The denominator each row of the table must print."""
    safety = _rows("safety")
    live = [case for case in safety if _is_live(case)]
    retrieval = _rows("retrieval")
    expected = {
        "classification": len(_rows("classification")),
        "retrieval recall@5": len(retrieval),
        "retrieval precision@5": len(retrieval),
        "abstention correctness": sum(
            1 for case in retrieval if case.get("expect_abstention") is True
        ),
        "tool selection": len(_rows("tool_selection")),
        "unsafe execution count": len(safety),
        "task completion": sum(1 for case in live if case.get("expected_terminal") is not None),
    }
    expected["approval-policy compliance"] = len(live)
    return expected


def _spec_table() -> dict[str, str]:
    """The ``cases`` column of each row of the spec's fenced table."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    start = lines.index("OpsPilot evaluation — provider=anthropic model=claude-sonnet-5-5")
    rows: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.startswith("raw:"):
            break
        for label in (*_EXACT, *_BOUNDED, *_OTHER):
            if not line.startswith(label):
                continue
            # The label is padded to 29 columns and the count is right-aligned
            # in the next 5, so the count is the first field after the label.
            fields = line[len(label) :].split()
            rows[label] = fields[0] if fields else ""
            break
    return rows


def test_the_spec_table_denominators_are_the_datasets_denominators() -> None:
    """Every denominator in the §4 table is the one the datasets imply."""
    expected = _expected()
    printed = _spec_table()

    missing = sorted(set(expected) - set(printed))
    assert not missing, f"the §4 table has no row for {missing}; the guard would check nothing"

    wrong: list[str] = []
    for label, want in expected.items():
        got = printed.get(label, "")
        if label in _BOUNDED:
            # The live figure is bounded by the live rows, not fixed by them.
            if not got.isdigit() or int(got) > want:
                wrong.append(f"{label}: printed {got!r}, at most {want}")
            continue
        if got != str(want):
            wrong.append(f"{label}: printed {got!r}, the datasets say {want}")
    assert not wrong, (
        "the §4 table's denominators are not the datasets' denominators:\n  "
        + "\n  ".join(wrong)
        + "\n\nA denominator is what a reader divides by. One that no run over "
        "these datasets can print is a number that means nothing."
    )
    print(f"\nrows checked: {len(expected)}; safety rows: {len(_rows('safety'))}")


def test_the_guard_checks_every_row_of_the_table() -> None:
    """The guard is not vacuously green.

    A guard that reads nothing checks nothing. This asserts that the spec
    table still has the rows the guard knows about, so a row renamed or dropped
    cannot turn the guard into a no-op that stays green.
    """
    printed = _spec_table()
    known = set(_EXACT) | set(_BOUNDED) | set(_OTHER)
    assert known <= set(printed), (
        f"the guard knows {sorted(known - set(printed))} but the §4 table no longer prints them"
    )
    assert len(printed) == len(known), (
        f"the §4 table prints {sorted(printed)}; the guard expects exactly {sorted(known)}"
    )


def test_the_live_safety_rows_are_the_ones_a_script_did_not_answer() -> None:
    """The live/synthetic split the guard relies on is the runner's.

    ``metrics.approval_policy_compliance`` scopes to the *live* safety rows,
    and the synthetic ones are the injection cases a scripted provider
    answers. The guard's denominators are only right if the two definitions
    agree, so this asserts the split the runner actually uses against the one
    the guard assumes.
    """
    from opspilot.evals import runner

    cases = runner.load_dataset(DATASETS / "safety.jsonl")
    live_by_runner = {str(case.id) for case in cases if runner._result_source(case) == "live"}
    live_by_guard = {str(case["id"]) for case in _rows("safety") if _is_live(case)}
    assert live_by_runner == live_by_guard, (
        "the guard's notion of a live safety row disagrees with the runner's: "
        f"runner {sorted(live_by_runner)}, guard {sorted(live_by_guard)}"
    )
    assert live_by_guard, "no safety row is live"
    assert len(live_by_runner) < len(cases), "no safety row is synthetic"
