"""The README's eval figures must be the numbers in the committed result files.

WHY THIS FILE EXISTS
--------------------
The README's figures table has a third column, "Source -- verify it", and
every eval row of it points at a file under ``evals/results/``:
``2026-10-11T15-39-42.json`` for the baseline, the two ``2026-10-13`` files for
the tool-selection repeats. That column is a claim about those files: *the
number printed in the README is the number in that file*, and a reader who
follows it lands on the file and expects to find the figure there.

Nothing checked it. Two guards already bound the two ends of the sentence --
``tests/evals/test_committed_results_are_recomputable.py`` binds a committed
result file to today's ``metrics.py``, and the metrics' own tests bind the
metric *definitions* to hand-computed values -- but between them sat a third
party, the README, and it was free to drift without anything going red. A
README figure that stopped matching its cited file would have been published
with a "Source -- verify it" column pointing at a file that does not contain
it, which is the specific failure the column exists to prevent.

WHAT IT ASSERTS
---------------
Every eval row of the README's figures table, read out of the table as text,
against the committed file it cites:

* the case and dataset counts, against the file's ``results[]``;
* each metric row, against the file's own ``metrics[]`` row;
* the tool-call split and the rejection reason, against the file's
  ``results[].executed_tool_calls[]``;
* the latency, token and cost figures, against today's metrics computed over
  the file's recorded values;
* the two tool-selection repeat runs, against the two ``2026-10-13`` files.

WHAT IT DELIBERATELY DOES NOT ASSERT
-------------------------------------
That the README reproduces the whole figures table. Only the rows that carry an
eval figure are read here; the rest of that table -- CI jobs, test counts, the
tool registry -- belongs to other guards, and a guard that read the whole table
would be a guard that went red for somebody else's drift.

The banner block further down the README (the printed metric table) is not
read either. It is the same numbers in a second format, and a second copy of a
table is a second thing to keep in step; the figures table is the one the
"Source -- verify it" column makes a promise about.

Offline by construction
-----------------------
The only thing outside this process is the README and the four committed
result files. No network, no model, no database, no Docker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from opspilot.evals.metrics import summarize

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_RESULTS = _REPO_ROOT / "evals" / "results"

#: The run the README's baseline figures are quoted from. Named in the
#: README's own "Source -- verify it" column.
_BASELINE = "2026-10-11T15-39-42.json"

#: The two tool-selection repeat runs, in the order the README names them.
_REPEATS = ("2026-10-13T18-24-25.json", "2026-10-13T18-40-10.json")


# ---------------------------------------------------------------------------
# Reading the README's figures table
# ---------------------------------------------------------------------------


def _figures_table() -> dict[str, tuple[str, str]]:
    """The README's figures table, as ``label -> (label cell, value cell)``.

    The table is found by its header rather than by scanning for a label, so a
    row of the same name somewhere else in a 70 KB README cannot be picked up
    instead. The first two lines after the header are the separator and the
    first row, so the scan starts below them and stops at the first line that
    is not a table row.

    Both cells are kept because one figure -- the rejection row -- states its
    count in the *label* ("All 90 rejections") and the rest in the value cell.
    """
    lines = _README.read_text(encoding="utf-8").splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("| Figure |")), None)
    assert header is not None, (
        "the README's figures table header (`| Figure | Value | Source -- verify it |`) "
        "was not found, so the eval figures in it cannot be checked"
    )
    rows: dict[str, tuple[str, str]] = {}
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 3:
            continue
        rows[cells[1]] = (cells[1], cells[2])
    return rows


def _cell(label: str) -> str:
    """The value cell of the figures table row with this label."""
    rows = _figures_table()
    assert label in rows, (
        f"the README's figures table has no row labelled {label!r}. The rows it does "
        f"have are: {sorted(rows)}. A renamed row means the figures moved, and this "
        "guard has to be told where they went -- not deleted."
    )
    return rows[label][1]


def _plain(text: str) -> str:
    """The cell text without the markdown emphasis the table uses."""
    return text.replace("**", "").replace("`", "").strip()


# ---------------------------------------------------------------------------
# Reading the committed result files
# ---------------------------------------------------------------------------


def _raw(name: str) -> dict[str, Any]:
    """A committed result file, parsed."""
    path = _RESULTS / name
    assert path.is_file(), f"the committed result file {name} is missing from the tree"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _metric_row(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """The ``metrics[]`` row of a result file, by its display name."""
    for row in raw["metrics"]:
        if row["name"] == name:
            return cast("dict[str, Any]", row)
    raise AssertionError(
        f"the result file records no metric row named {name!r}; it records "
        f"{[row['name'] for row in raw['metrics']]}"
    )


def _results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``results[]`` array of a result file."""
    return list(raw["results"])


def _calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Every attempted tool call in a result file, whatever its status."""
    return [
        call
        for result in _results(raw)
        for call in (result.get("executed_tool_calls") or [])
        if isinstance(call, dict)
    ]


# ---------------------------------------------------------------------------
# The counts
# ---------------------------------------------------------------------------


def test_the_eval_case_and_dataset_counts_are_the_baseline_file_s_own() -> None:
    """``70 across 4 datasets`` is the length and the spread of ``results[]``."""
    raw = _raw(_BASELINE)
    cell = _plain(_cell("Eval cases"))
    match = re.fullmatch(r"(\d+) across (\d+) datasets", cell)
    assert match is not None, (
        f"the README's eval-case cell is {cell!r}, not a count of cases and datasets"
    )

    results = _results(raw)
    assert int(match.group(1)) == len(results), (
        f"the README says {match.group(1)} eval cases but "
        f"{_file_name(_BASELINE)} records {len(results)}"
    )
    datasets = {str(result.get("dataset")) for result in results}
    assert int(match.group(2)) == len(datasets), (
        f"the README says {match.group(2)} datasets but {_file_name(_BASELINE)} records "
        f"{len(datasets)}: {sorted(datasets)}"
    )


def _file_name(filename: str) -> str:
    """The result file's name, for a failure message."""
    return filename


# ---------------------------------------------------------------------------
# The metric rows
# ---------------------------------------------------------------------------


def _score_and_fraction(cell: str) -> tuple[float, int, int]:
    """``0.800 (16/20)`` as ``(value, numerator, denominator)``.

    The unit word is allowed (``(80/170 calls)``) because the table states the
    unit in one row and not in another, and the unit is prose rather than part
    of the figure.
    """
    match = re.fullmatch(r"([0-9.]+)\s*\((\d+)/(\d+)(?: calls)?\)", cell)
    assert match is not None, f"the README's cell is {cell!r}, not a score with a fraction"
    return float(match.group(1)), int(match.group(2)), int(match.group(3))


@pytest.mark.parametrize(
    ("label", "metric"),
    [
        ("classification", "classification"),
        ("retrieval recall@5", "retrieval recall@5"),
        ("abstention correctness", "abstention correctness"),
        ("tool selection", "tool selection"),
        ("task completion", "task completion"),
    ],
)
def test_each_scored_row_is_the_metric_row_of_the_baseline_file(label: str, metric: str) -> None:
    """The score and its fraction are the file's own ``metrics[]`` row.

    The value is compared at the precision the README prints, so a metric that
    moved by a hundredth is a failure and a metric that moved by a rounding
    artefact is not.
    """
    raw = _raw(_BASELINE)
    printed, numerator, denominator = _score_and_fraction(_plain(_cell(label)))
    row = _metric_row(raw, metric)

    assert round(float(row["value"]), 3) == printed, (
        f"the README prints {printed} for {label!r} but {_file_name(_BASELINE)} records "
        f"{row['value']}"
    )
    assert denominator == row["cases"], (
        f"the README prints {label!r} over {denominator} cases but {_file_name(_BASELINE)} "
        f"records the metric over {row['cases']}"
    )
    assert numerator == round(float(row["value"]) * int(row["cases"])), (
        f"the README prints {numerator}/{denominator} for {label!r} but "
        f"{_file_name(_BASELINE)} records {row['value']} over {row['cases']} cases"
    )


def test_the_precision_row_is_the_metric_row_of_the_baseline_file() -> None:
    """``retrieval precision@5`` is printed without a fraction, and is the file's."""
    raw = _raw(_BASELINE)
    printed = float(_plain(_cell("retrieval precision@5")))
    row = _metric_row(raw, "retrieval precision@5")
    assert round(float(row["value"]), 3) == printed, (
        f"the README prints {printed} for retrieval precision@5 but "
        f"{_file_name(_BASELINE)} records {row['value']}"
    )


def test_the_tool_argument_validity_row_counts_the_calls_the_file_records() -> None:
    """``0.471 (80/170 calls)`` -- the denominator is the file's call count."""
    raw = _raw(_BASELINE)
    printed, numerator, denominator = _score_and_fraction(_plain(_cell("tool argument validity")))
    row = _metric_row(raw, "tool argument validity")

    attempted = len(_calls(raw))
    assert denominator == attempted, (
        f"the README prints tool-argument validity over {denominator} calls but "
        f"{_file_name(_BASELINE)} records {attempted} attempted calls"
    )
    assert round(float(row["value"]), 3) == printed, (
        f"the README prints {printed} for tool argument validity but "
        f"{_file_name(_BASELINE)} records {row['value']}"
    )
    assert numerator == round(float(row["value"]) * denominator), (
        f"the README prints {numerator}/{denominator} valid calls but "
        f"{_file_name(_BASELINE)} records {row['value']} over {attempted} calls"
    )


def test_the_approval_policy_row_is_the_two_populations_the_file_records() -> None:
    """``1.000 (1/1 live, 2/2 synthetic)``.

    The live half is the file's own ``metrics[]`` row. The synthetic half is
    not one -- the file was written before the synthetic metric was split out
    -- so it is computed by today's metrics over the file's recorded cases,
    which is the same operation the file performed when it was written.
    """
    raw = _raw(_BASELINE)
    cell = _plain(_cell("approval-policy compliance"))
    match = re.fullmatch(r"([0-9.]+)\s*\((\d+)/(\d+) live,\s*(\d+)/(\d+) synthetic\)", cell)
    assert match is not None, (
        f"the README's approval-policy cell is {cell!r}, not a score with a live and a "
        "synthetic fraction"
    )
    printed = float(match.group(1))
    live_ok, live_n = int(match.group(2)), int(match.group(3))
    synth_ok, synth_n = int(match.group(4)), int(match.group(5))

    live_row = _metric_row(raw, "approval-policy compliance")
    assert round(float(live_row["value"]), 3) == printed, (
        f"the README prints {printed} for approval-policy compliance but "
        f"{_file_name(_BASELINE)} records {live_row['value']}"
    )
    assert live_n == live_row["cases"], (
        f"the README prints the live half over {live_n} cases but {_file_name(_BASELINE)} "
        f"records {live_row['cases']}"
    )
    assert live_ok == round(float(live_row["value"]) * live_n)

    recomputed = {metric.name: metric for metric in summarize(_results(raw))}
    synthetic = recomputed["synthetic_approval_policy_compliance"]
    assert synthetic.cases == synth_n, (
        f"the README prints the synthetic half over {synth_n} cases but the cases "
        f"{_file_name(_BASELINE)} records give {synthetic.cases}"
    )
    assert synthetic.value * synthetic.cases == synth_ok, (
        f"the README prints {synth_ok}/{synth_n} synthetic approvals but the cases "
        f"{_file_name(_BASELINE)} records give {synthetic.value} over {synthetic.cases}"
    )


def test_the_unsafe_execution_count_is_zero_in_the_baseline_file() -> None:
    """The gate row: the file's own number, and it is the gate's zero."""
    raw = _raw(_BASELINE)
    printed = _plain(_cell("unsafe execution count"))
    row = _metric_row(raw, "unsafe execution count")
    assert row["gate"] is True, (
        f"the README's unsafe-execution row is a gate, but {_file_name(_BASELINE)} records "
        f"gate={row['gate']}"
    )
    assert float(printed) == float(row["value"]) == 0.0, (
        f"the README prints {printed} for the unsafe-execution gate but "
        f"{_file_name(_BASELINE)} records {row['value']}"
    )


# ---------------------------------------------------------------------------
# The composition rows
# ---------------------------------------------------------------------------


def test_the_tool_call_split_is_the_baseline_file_s_own_split() -> None:
    """``170 attempted: 77 executed, 90 rejected, 3 failed``."""
    raw = _raw(_BASELINE)
    cell = _plain(_cell("Tool-call split"))
    match = re.fullmatch(
        r"(\d+) attempted:\s*(\d+) executed,\s*(\d+) rejected,\s*(\d+) failed", cell
    )
    assert match is not None, f"the README's tool-call split is {cell!r}, not a split"

    counts = {"executed": 0, "rejected": 0, "failed": 0}
    for call in _calls(raw):
        status = call.get("status")
        counts[str(status)] = counts.get(str(status), 0) + 1
    total = sum(counts.values())

    assert int(match.group(1)) == total, (
        f"the README says {match.group(1)} attempted calls but {_file_name(_BASELINE)} "
        f"records {total}"
    )
    assert int(match.group(2)) == counts["executed"], (
        f"the README says {match.group(2)} executed calls but {_file_name(_BASELINE)} "
        f"records {counts['executed']}"
    )
    assert int(match.group(3)) == counts["rejected"], (
        f"the README says {match.group(3)} rejected calls but {_file_name(_BASELINE)} "
        f"records {counts['rejected']}"
    )
    assert int(match.group(4)) == counts["failed"], (
        f"the README says {match.group(4)} failed calls but {_file_name(_BASELINE)} "
        f"records {counts['failed']}"
    )


def test_every_rejection_is_the_reason_the_readme_names() -> None:
    """``All 90 rejections`` are ``arguments: {}`` and one gate's reason."""
    raw = _raw(_BASELINE)
    cell = _plain(_cell("All 90 rejections"))
    label = _figures_table()["All 90 rejections"][0]
    match = re.search(r"All (\d+) rejections", label)
    assert match is not None, f"the README's row is titled {label!r}"
    count = int(match.group(1))

    rejected = [call for call in _calls(raw) if call.get("status") == "rejected"]
    assert len(rejected) == count, (
        f"the README's row is titled `All {count} rejections` but {_file_name(_BASELINE)} "
        f"records {len(rejected)}"
    )
    assert "arguments: {}" in cell, f"the README's rejection cell is {cell!r}"
    assert "gate_1_schema_validation" in cell, f"the README's rejection cell is {cell!r}"

    reasons = {str(call.get("rejection_reason")) for call in rejected}
    assert reasons == {"gate_1_schema_validation"}, (
        f"the README names one rejection reason but "
        f"{_file_name(_BASELINE)} records {sorted(reasons)}"
    )
    with_arguments = [call for call in rejected if call.get("arguments")]
    assert not with_arguments, (
        f"the README says every rejection carried `arguments: {{}}` but "
        f"{len(with_arguments)} of {len(rejected)} rejections in {_file_name(_BASELINE)} "
        "carried arguments"
    )


# ---------------------------------------------------------------------------
# The latency, token and cost row
# ---------------------------------------------------------------------------


def test_the_latency_token_and_cost_figures_are_the_baseline_file_s_own() -> None:
    """``p50 26.8 s, p95 276.7 s, in 1146 / out 646 per run, $0.0000``.

    These four are computed by today's metrics over the file's recorded
    values -- the same arithmetic the file performed when it was written --
    rather than read out of the file's own ``metrics[]``, which does not carry
    them.
    """
    raw = _raw(_BASELINE)
    cell = _plain(_cell("Latency / tokens / cost"))
    match = re.fullmatch(
        r"p50 ([0-9.]+) s,\s*p95 ([0-9.]+) s,\s*in (\d+) / out (\d+) per run,\s*\$([0-9.]+)",
        cell,
    )
    assert match is not None, f"the README's latency/token/cost cell is {cell!r}"

    recomputed = {metric.name: metric for metric in summarize(_results(raw))}
    p50 = recomputed["latency_p50_ms"]
    p95 = recomputed["latency_p95_ms"]
    tokens_in = recomputed["tokens_input_mean"]
    tokens_out = recomputed["tokens_output_mean"]
    cost = recomputed["estimated_cost_mean"]

    assert round(p50.value / 1000, 1) == float(match.group(1)), (
        f"the README prints p50 {match.group(1)}s but the latencies {_file_name(_BASELINE)} "
        f"records give {p50.value / 1000}"
    )
    assert round(p95.value / 1000, 1) == float(match.group(2)), (
        f"the README prints p95 {match.group(2)}s but the latencies {_file_name(_BASELINE)} "
        f"records give {p95.value / 1000}"
    )
    assert round(tokens_in.value) == int(match.group(3)), (
        f"the README prints {match.group(3)} input tokens per run but the calls "
        f"{_file_name(_BASELINE)} records give {tokens_in.value}"
    )
    assert round(tokens_out.value) == int(match.group(4)), (
        f"the README prints {match.group(4)} output tokens per run but the calls "
        f"{_file_name(_BASELINE)} records give {tokens_out.value}"
    )
    assert round(cost.value, 4) == float(match.group(5)), (
        f"the README prints ${match.group(5)} per run but the calls "
        f"{_file_name(_BASELINE)} records give ${cost.value}"
    )


def test_the_model_call_accounting_is_the_baseline_file_s_own() -> None:
    """``358 calls, 5.1 per run; 140 with usage, 218 without``."""
    raw = _raw(_BASELINE)
    cell = _plain(_cell("Model-call accounting"))
    match = re.fullmatch(
        r"(\d+) calls,\s*([0-9.]+) per run;\s*(\d+) with usage,\s*(\d+) without", cell
    )
    assert match is not None, f"the README's model-call cell is {cell!r}"

    calls = [
        call
        for result in _results(raw)
        for call in (result.get("model_calls") or [])
        if isinstance(call, dict)
    ]
    runs = len(_results(raw))
    with_usage = sum(1 for call in calls if call.get("tokens_available"))

    assert int(match.group(1)) == len(calls), (
        f"the README says {match.group(1)} model calls but {_file_name(_BASELINE)} records "
        f"{len(calls)}"
    )
    assert round(len(calls) / runs, 1) == float(match.group(2)), (
        f"the README says {match.group(2)} calls per run but {_file_name(_BASELINE)} records "
        f"{len(calls)} calls over {runs} runs"
    )
    assert int(match.group(3)) == with_usage, (
        f"the README says {match.group(3)} calls with usage but {_file_name(_BASELINE)} "
        f"records {with_usage}"
    )
    assert int(match.group(4)) == len(calls) - with_usage, (
        f"the README says {match.group(4)} calls without usage but {_file_name(_BASELINE)} "
        f"records {len(calls) - with_usage}"
    )


# ---------------------------------------------------------------------------
# The repeat runs
# ---------------------------------------------------------------------------


def test_the_tool_selection_repeat_runs_are_the_two_files_the_readme_names() -> None:
    """``3/15 and 2/15`` -- one figure from each of the two committed repeats."""
    cell = _plain(_cell("Tool-selection repeat runs"))
    match = re.fullmatch(r"(\d+)/(\d+) and (\d+)/(\d+)", cell)
    assert match is not None, f"the README's repeat-run cell is {cell!r}"

    repeats = ((match.group(1), match.group(2)), (match.group(3), match.group(4)))
    for filename, (ok, total) in zip(_REPEATS, repeats, strict=True):
        raw = _raw(filename)
        row = _metric_row(raw, "tool selection")
        assert int(total) == row["cases"], (
            f"the README prints the repeat run over {total} cases but {filename} records "
            f"{row['cases']}"
        )
        assert int(ok) == round(float(row["value"]) * int(row["cases"])), (
            f"the README prints {ok}/{total} for {filename} but that file records "
            f"{row['value']} over {row['cases']} cases"
        )
        assert len(_results(raw)) == row["cases"], (
            f"{filename} records {len(_results(raw))} cases but its tool-selection metric "
            f"was computed over {row['cases']}"
        )
