"""The eval harness runs end to end on the fake provider, and prints a table.

## Why this file exists

``docs/evals.md`` §4 prints the table the runner produces. Two guards already
hold that table up against other things:

* ``tests/evals/test_eval_cli.py`` drives the real CLI and asserts the printed
  table **character for character** against the spec's fenced block. That is a
  strong guarantee about *format*.
* ``tests/evals/test_the_spec_table_denominators_match_the_datasets.py``
  compares the spec's denominators against the **datasets** -- it is the guard
  that catches a denominator no run over these datasets could print.

Neither of them compares the spec against **the code that computes the table**.
``test_eval_cli.py`` does not, because it substitutes doubles for ``runner`` and
``metrics`` (its own docstring says so) and those doubles are *spec-shaped*: they
return the numbers the spec prints. A guard that compares the spec against a
copy of the spec cannot notice the spec being wrong, and a double that returns
the spec's numbers cannot notice the code being wrong.

The link that was missing is the third one: **the denominators the real harness
computes are the ones the spec prints.** This file closes it, and it closes it
with the real ``runner.run_dataset`` and the real ``metrics.summarize`` -- no
doubles, no stand-ins, and the ``fake`` provider so that the run needs no
network, no key and no Docker.

That is why this file runs all four committed datasets rather than a slice of
one. A denominator is a property of the *dataset* (except where a run decides
it), so measuring it means measuring the dataset the run was over.

## What the fake provider can and cannot decide

The fake provider is scripted, so what it *proposes* is fixed and the
denominators below are the committed datasets' own. What it *scores* is not
worth reading -- a scripted model has no opinion about retrieval quality -- so
this file asserts no score, only denominators, the presence of every metric the
table names, and the one number that is a fact about the system rather than
about the model: ``unsafe execution count`` must be 0.

One row is deliberately **not** an equality. ``approval-policy compliance``
counts the live safety rows that *proposed* a high-risk tool, so a provider
that proposed none would print a smaller number than the spec's example does.
The spec's number is therefore an upper bound for that row and an exact figure
everywhere else -- the same distinction
``test_the_spec_table_denominators_match_the_datasets.py`` draws, and for the
same reason.
"""

from __future__ import annotations

import asyncio

import pytest

from opspilot.evals.metrics import MetricResult, summarize
from opspilot.evals.runner import REPO_ROOT, run_dataset

DATASETS = REPO_ROOT / "evals" / "datasets"
SPEC = REPO_ROOT / "docs" / "evals.md"

#: The four committed datasets, in the order §2 lists them.
DATASET_NAMES = ("classification", "retrieval", "tool_selection", "safety")

#: Every row label the spec's §4 table prints, in the order it prints them.
#: The list is the whole table: a row dropped from the spec has to drop out of
#: here too, and the comparison below says so when it does.
SPEC_ROW_LABELS = (
    "classification",
    "retrieval recall@5",
    "retrieval precision@5",
    "abstention correctness",
    "tool selection",
    "tool argument validity",
    "approval-policy compliance",
    "unsafe execution count",
    "task completion",
)

#: The metric each spec row is computed from. Hand-written, because the labels
#: and the metric names do not correspond one-to-one by any rule -- and the
#: comparison is self-checking anyway: a row wired to the wrong metric has a
#: different denominator, so it fails rather than passing.
ROW_METRIC = {
    "classification": "classification_accuracy",
    "retrieval recall@5": "retrieval_recall_at_k",
    "retrieval precision@5": "retrieval_precision_at_k",
    "abstention correctness": "abstention_correctness",
    "tool selection": "tool_selection_accuracy",
    "approval-policy compliance": "approval_policy_compliance",
    "unsafe execution count": "unsafe_execution_count",
    "task completion": "task_completion_rate",
}

#: The row whose denominator a *run* decides rather than the dataset: it counts
#: the live safety rows that proposed a high-risk tool. The spec's figure is
#: the run it illustrates, so it is an upper bound here.
BOUNDED_ROW = "approval-policy compliance"

#: The row that is not denominated in cases at all: the spec prints ``--`` and
#: counts *calls*.
CALL_ROW = "tool argument validity"

#: The header line of the spec's §4 block, used to find the block.
_SPEC_HEADER = "OpsPilot evaluation — provider=anthropic model=claude-sonnet-5-5"


def _spec_block() -> list[str]:
    """The fenced block of ``docs/evals.md`` that holds the §4 table."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append(current)
                current = None
            continue
        if current is not None:
            current.append(line)
    for block in blocks:
        if any(line.startswith(_SPEC_HEADER) for line in block):
            return block
    raise AssertionError(f"docs/evals.md has no §4 block starting with {_SPEC_HEADER!r}")


def _spec_table() -> dict[str, str]:
    """The ``cases`` column of each row of the spec's §4 table.

    The label is padded to 29 columns and the count is right-aligned in the
    next five, so the count is the first field after the label.
    """
    rows: dict[str, str] = {}
    for line in _spec_block():
        if line.startswith("raw:"):
            break
        for label in SPEC_ROW_LABELS:
            if not line.startswith(label):
                continue
            fields = line[len(label) :].split()
            rows[label] = fields[0] if fields else ""
            break
    return rows


async def _run_every_dataset() -> list[dict[str, object]]:
    """Run every committed dataset on the fake provider, in one pass."""
    results: list[dict[str, object]] = []
    for name in DATASET_NAMES:
        results.extend(await run_dataset(DATASETS / f"{name}.jsonl", provider_name="fake"))
    return results


@pytest.fixture(scope="module")
def harness_table() -> dict[str, MetricResult]:
    """The metric table the real harness computes, keyed by metric name.

    Module-scoped so the four datasets are run once for the whole file: the run
    is the expensive part, and every test here reads the same table.
    """
    results = asyncio.run(_run_every_dataset())
    return {metric.name: metric for metric in summarize(results)}


def test_fake_provider_run_produces_a_metric_table(harness_table: dict[str, MetricResult]) -> None:
    """The harness runs end to end on the fake provider and reduces to a table.

    Every metric carries an integer ``cases``: a metric that reports a score
    with no denominator behind it is the shape this repository has been burned
    by, and the harness's own docstring records a run that measured nothing and
    printed a table anyway.

    The table must also name every metric the spec's §4 rows are computed from.
    A renamed metric would leave the row in the spec with nothing behind it --
    and the spec's rows are what a reader divides by.
    """
    assert harness_table, "the harness produced no metrics at all"

    missing = sorted(name for name in ROW_METRIC.values() if name not in harness_table)
    assert not missing, (
        f"the metric table does not contain {missing}. §4 prints rows computed "
        "from them, and a row whose metric is not in the table is a row with "
        "nothing behind it."
    )

    for name, metric in sorted(harness_table.items()):
        assert isinstance(metric.cases, int), f"{name} carries a non-integer case count"
        assert metric.cases >= 0, f"{name} carries a negative case count"

    # The footer lines of §4 are metrics too, and they are the ones a reader
    # treats as facts about the run rather than about the model.
    for footer in ("latency_p50_ms", "latency_p95_ms", "tokens_input_mean", "estimated_cost_mean"):
        assert footer in harness_table, f"the table has no {footer}; §4 prints a footer line for it"

    # The gate is the one number that is about the system rather than the model.
    gate = harness_table["unsafe_execution_count"]
    assert gate.value == 0, (
        f"the fake-provider run executed {gate.value} calls in violation of the "
        "policy invariants. The gate exists so this is 0 or the run failed."
    )
    # And it is not a gate over nothing: every safety row, injections included.
    assert gate.cases == 15, f"the gate counted {gate.cases} cases; §4 counts every safety row"


def test_the_real_harness_computes_the_denominators_the_spec_prints(
    harness_table: dict[str, MetricResult],
) -> None:
    """The table the harness computes and the table the spec prints agree.

    This is the link the CLI test cannot make. That test drives the CLI with
    doubles for ``runner`` and ``metrics``, so it proves the *format* and
    nothing about the *content*; this one drives the real runner and the real
    metrics, and compares the denominators they produce with the ones the spec
    prints.

    ``approval-policy compliance`` is compared as an upper bound, because its
    denominator is the live safety rows that proposed a high-risk tool, which
    is a fact about the run and not about the dataset. ``tool argument
    validity`` is not compared at all: the spec prints ``--`` for it and
    counts calls, not cases.
    """
    printed = _spec_table()
    assert set(printed) == set(SPEC_ROW_LABELS), (
        "the §4 table no longer prints exactly the rows this guard knows about: "
        f"the table has {sorted(printed)}, the guard knows {sorted(SPEC_ROW_LABELS)}. "
        "A row renamed or dropped on either side makes the comparison a no-op."
    )

    assert printed[CALL_ROW] == "--", (
        f"§4 prints {printed[CALL_ROW]!r} for {CALL_ROW!r}, not '--'. That row is "
        "denominated in calls, and this guard does not compare it."
    )

    wrong: list[str] = []
    compared = 0
    for label, metric_name in sorted(ROW_METRIC.items()):
        want = printed[label]
        assert want.isdigit(), f"§4 prints {want!r} for {label!r}, which is not a count"
        got = harness_table[metric_name].cases
        if label == BOUNDED_ROW:
            if got > int(want):
                wrong.append(f"{label}: the harness counted {got}, the spec prints at most {want}")
            compared += 1
            continue
        if got != int(want):
            wrong.append(f"{label}: the harness counted {got}, the spec prints {want}")
        compared += 1

    assert not wrong, (
        "the metric table the harness computes and the table docs/evals.md §4 "
        "prints disagree on their denominators:\n  " + "\n  ".join(wrong)
    )
    assert compared == len(ROW_METRIC), (
        f"only {compared} of the {len(ROW_METRIC)} rows were compared; a row that "
        "is skipped is a row that can drift"
    )

    # The call-counted row is a real number in the harness's table even though
    # the spec prints no count for it.
    calls = harness_table["tool_argument_validity"].cases
    assert calls > 0, "the harness counted no tool calls at all, so it measured nothing"


def test_the_synthetic_injection_cases_are_counted_on_their_own(
    harness_table: dict[str, MetricResult],
) -> None:
    """The two scripted injection rows are reported apart from the live ones.

    §4 prints ``approval-policy compliance`` with a ``(2/2 synthetic)``
    companion, and the split is the whole point: a reader must be able to tell
    which rows carried a scripted answer, because a script's compliance is not
    an endpoint's. The harness therefore reports the two figures separately
    rather than averaging them, and the live figure excludes the scripted rows
    entirely.

    Both halves are asserted: the companion exists and holds the scripted rows,
    and the live figure does not count them.
    """
    live = harness_table["approval_policy_compliance"]
    synthetic = harness_table["synthetic_approval_policy_compliance"]

    assert synthetic.cases == 2, (
        f"the synthetic figure counts {synthetic.cases} cases; the injection "
        "dataset carries two rows a scripted provider answers"
    )
    # Together the two figures account for every safety row: the live ones and
    # the scripted ones. A row counted by neither would be a row no figure
    # describes.
    assert live.cases + synthetic.cases == 15, (
        f"the live figure ({live.cases}) and the synthetic figure "
        f"({synthetic.cases}) do not add up to the 15 safety rows the dataset "
        "holds"
    )
    # And the live figure never counts a scripted row: the split is what lets a
    # reader tell an endpoint's compliance from a script's.
    assert live.cases <= 13, (
        f"the live figure counts {live.cases} cases, more than the 13 live "
        "safety rows; a scripted injection row has been counted as live"
    )

    printed = _spec_table()
    assert printed[BOUNDED_ROW].isdigit()
    companion = next(line for line in _spec_block() if line.startswith(BOUNDED_ROW))
    assert "(2/2 synthetic)" in companion, (
        f"§4 prints {companion!r}, without the synthetic companion. The split is "
        "the point: a reader has to be able to tell which rows carried a "
        "scripted answer."
    )
    assert synthetic.cases == 2, (
        f"§4 prints '(2/2 synthetic)' but the harness counted {synthetic.cases} synthetic cases"
    )
