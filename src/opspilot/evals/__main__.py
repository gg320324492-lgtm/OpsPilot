"""Eval process entry point.

Responsibility: the ``opspilot-eval`` console script and ``python -m
opspilot.evals`` -- parse the dataset arguments, run the runner, print the
metric table.

Layer: tooling.

**The output format is a contract, not a convenience.** ``docs/evals.md`` §4
pins the exact table -- the labels, the ``(n/N)`` counts, the ``--`` for a
metric with no case count, the blank line that groups the three model metrics,
and the trailing ``raw:`` line. The public README quotes this table with a dated
run, so a reformatting here would silently invalidate the number the README
claims. ``_render_report`` is written against that literal layout and the test
suite compares its output to the fenced block in the spec, character for
character.

**Two things are deliberately not "scores".**

- ``--provider fake`` replays a scripted provider, so its accuracy is a property
  of the script, not of a model. The table is suppressed unless
  ``--allow-fake-scores`` is passed (``docs/limitations.md`` §5). The run still
  happens -- CI's ``eval-smoke`` job uses it to prove the plumbing end to end --
  and the results file is still written, but the numbers are never printed as if
  they meant something.
- ``unsafe execution count`` is a **gate**: the process exits non-zero if it is
  anything but 0, so it cannot be trended down. That is the §M8 acceptance
  criterion, and it is why the exit code here is not just success/failure.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_DATASETS_DIR: Final[Path] = _REPO_ROOT / "evals" / "datasets"
_RESULTS_DIR: Final[Path] = _REPO_ROOT / "evals" / "results"

# The four committed datasets, in the order the table reports them. Order matters
# because the printed table is keyed by it and ``--dataset`` re-sorts into it, so
# flag order never changes the output.
_DATASETS: Final[tuple[str, ...]] = (
    "classification",
    "retrieval",
    "tool_selection",
    "safety",
)

#: Printed instead of the table for ``--provider fake`` without
#: ``--allow-fake-scores``. The em dash is intentional and is asserted verbatim
#: by the test suite -- it is the same string in ``docs/evals.md``,
#: ``evals/README.md`` and ``docs/limitations.md``.
_FAKE_REFUSAL: Final[str] = "fake provider — harness check only, not a model result"

# Exit codes. Distinguishable because a caller (CI) has to tell "the safety gate
# tripped" apart from "you configured this wrong": the first is a real finding
# about the system under test, the second is an operator error, and a job that
# treats them the same sends the reader to the wrong place.
_EXIT_OK: Final[int] = 0
_EXIT_CONFIG: Final[int] = 1
_EXIT_GATE: Final[int] = 3

# How many cases the CI smoke job runs (`docs/evals.md` §5: "8 representative
# cases"). Named here so the CI invocation and this comment cannot drift.
_SMOKE_CASES: Final[int] = 8


@dataclass(frozen=True)
class _MetricRow:
    """One line of the metric table.

    ``numerator``/``denominator`` drive the ``(18/20)`` suffix. They are optional
    because two of the spec's rows have no such count: ``retrieval precision@5``
    prints a bare score, and ``tool argument validity`` counts *calls* rather
    than cases, so its count arrives as ``detail`` instead.
    """

    label: str
    cases: int | None
    value: float
    numerator: int | None = None
    denominator: int | None = None
    detail: str = ""
    is_gate: bool = False
    #: The metric was computed over zero cases, so it is *unknown* -- rendered
    #: as ``--`` in both the count and the score columns, never as a clean 0.
    unknown: bool = False


def _build_parser() -> argparse.ArgumentParser:
    """The ``opspilot-eval`` argument parser.

    A ``run`` subcommand even though there is only one today: the spec's
    invocation is ``opspilot-eval run ...``, and a subparser is what lets a
    future ``opspilot-eval compare`` (Phase 2's stored baselines) share the
    provider and model flags without contorting a flat namespace.
    """
    parser = argparse.ArgumentParser(
        prog="opspilot-eval",
        description="Run the OpsPilot evaluation harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run",
        help="Run the datasets through a provider and print the metric table.",
    )
    run.add_argument(
        "--provider",
        choices=("fake", "anthropic", "openai"),
        default="fake",
        help=(
            "Model provider to run against. 'fake' replays recorded responses "
            "and needs no API key (the default, and what CI's eval-smoke uses)."
        ),
    )
    run.add_argument(
        "--model",
        default=None,
        help=(
            "Model name, e.g. claude-sonnet-5-5. Omit to use the provider's own "
            "default, which is the honest choice for a run you intend to record: "
            "the default a provider picks is part of what the run measured."
        ),
    )
    run.add_argument(
        "--allow-fake-scores",
        action="store_true",
        help=(
            "Print the metric table for --provider fake. Without this the table "
            "is suppressed and a harness-check notice is printed instead, "
            "because a scripted provider's accuracy measures the script."
        ),
    )
    run.add_argument(
        "--dataset",
        action="append",
        choices=_DATASETS,
        default=None,
        metavar="NAME",
        help=(
            "Run only this dataset; repeatable. One of: "
            f"{', '.join(_DATASETS)}. Default: all four."
        ),
    )
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Run at most N cases in total, split evenly across the selected "
            "datasets (the first N/datasets of each, in file order). This is "
            f"what the CI smoke job uses with --limit {_SMOKE_CASES} to keep the "
            "plumbing check to a handful of representative cases spanning every "
            "dataset rather than all 70."
        ),
    )
    return parser


def _selected_datasets(requested: Sequence[str] | None) -> list[str]:
    """The datasets to run, in the canonical table order.

    ``--dataset `` repeated builds a list in the order the operator typed it;
    re-sorting into ``_DATASETS`` order makes the output independent of flag
    order, so two invocations that select the same datasets compare equal.
    """
    if not requested:
        return list(_DATASETS)
    return [name for name in _DATASETS if name in requested]


def _limit_per_dataset(limit: int | None, datasets: Sequence[str]) -> int | None:
    """The per-dataset cap implied by a total ``--limit``.

    The smoke job asks for "8 representative cases" and means a *total*, spread
    across the datasets so a regression in any one of them is still caught. The
    runner takes a whole dataset file, so the total is divided evenly across the
    selected datasets: ``ceil`` rather than ``floor`` so the budget is spent
    rather than under-spent, and ``max(1, ...)`` so a limit smaller than the
    dataset count still selects one case from each -- never dropping a dataset
    from the run, which is the coverage the smoke check exists to provide. For
    the four datasets and ``--limit 8`` this is two cases from each.
    """
    if limit is None:
        return None
    if limit < 1:
        raise _ConfigError("--limit must be at least 1")
    per_dataset = -(-limit // len(datasets))  # ceil division, integer-only
    return max(1, per_dataset)


class _ConfigError(RuntimeError):
    """The CLI cannot proceed because the invocation is inconsistent."""


def _load(module: str) -> ModuleType:
    """Import an eval submodule by name.

    Indirected through :func:`importlib.import_module` rather than a top-level
    ``from`` so the console script imports neither the runner nor the metrics
    until a ``run`` is actually requested, and so a test can substitute a double
    (``sys.modules``) without importing the real, heavier module graph.
    """
    return importlib.import_module(f"opspilot.evals.{module}")


def _utc_now() -> datetime:
    """Timezone-aware "now", so the recorded date is unambiguous."""
    return datetime.now(UTC)


def _timestamp_slug(moment: datetime) -> str:
    """The results filename stem: ``2026-10-05T11-20-03``.

    Colons are replaced because the spec's filename is a filename, and a colon
    is not a legal character in a Windows path. Seconds resolution is the spec's;
    a run need not be reproducible from its filename, only findable by it.
    """
    return moment.strftime("%Y-%m-%dT%H-%M-%S")


def _write_results(
    *,
    moment: datetime,
    provider: str,
    model: str,
    datasets: Sequence[str],
    limit: int | None,
    allow_fake_scores: bool,
    metric_rows: Sequence[_MetricRow],
    raw_results: Sequence[dict[str, object]],
) -> Path:
    """Write ``evals/results/<timestamp>.json`` and return its path.

    The provider, model, date and config are recorded because a result that does
    not say what produced it is not evidence (``evals/results/README.md``): the
    metric table is only meaningful alongside the model that generated it, and a
    file that omits them cannot be compared with anything. The embeddings and
    retrieval settings are included for the same reason -- ``docs/limitations.md``
    §3 is explicit that a retrieval number means nothing without the embedder that
    produced it.
    """
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _RESULTS_DIR / f"{_timestamp_slug(moment)}.json"

    payload: dict[str, object] = {
        "provider": provider,
        "model": model,
        "date": moment.date().isoformat(),
        "timestamp": moment.isoformat(),
        "config": {
            "datasets": list(datasets),
            "limit": limit,
            "allow_fake_scores": allow_fake_scores,
            "embedding_provider": _settings_value("embedding_provider"),
            "embedding_model": _settings_value("embedding_model"),
            "retrieval_top_k": _settings_value("retrieval_top_k"),
            "retrieval_min_score": _settings_value("retrieval_min_score"),
        },
        "metrics": [
            {
                "name": row.label,
                "value": row.value,
                "cases": row.cases,
                "gate": row.is_gate,
            }
            for row in metric_rows
        ],
        "results": list(raw_results),
    }

    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return path


def _settings_value(name: str) -> object:
    """Read one setting, or ``None`` if settings cannot be read at all.

    The results file must never be the reason a run fails: a missing or
    unreadable settings module costs the ``config`` detail, not the run. Read
    defensively rather than assuming the module imports cleanly, because the
    whole point of writing this file is to have a record of a run that may have
    been run precisely because something else is broken.
    """
    try:
        settings = importlib.import_module("opspilot.settings").get_settings()
    except (ImportError, ValueError):
        return None
    return getattr(settings, name, None)


# The metric rows, in the order and with the labels ``docs/evals.md`` §4 pins.
# Each entry is (label, kind, aliases, show_ratio, show_cases):
#
#   - ``kind`` is ``score`` or ``gate``; a gate prints an integer and fails the
#     process when non-zero.
#   - ``aliases`` let the row bind to a ``MetricResult`` whatever it is called --
#     ``classification_accuracy`` or ``classification`` -- so the person
#     computing the metrics is not forced to name them for the printer. The
#     lookup is by name because that is the only stable handle across the
#     runner/metrics boundary.
#   - ``show_ratio`` controls the ``(n/N)`` suffix. It is False for
#     ``retrieval precision@5``, which is an average over the retrieved set
#     rather than a count of cases, so ``(12/20)`` would be a fabricated exact
#     count that merely multiplies out to the average.
#   - ``show_cases`` controls the ``cases`` column itself. It is False only for
#     ``tool argument validity``, whose count is over *calls*: that count arrives
#     as an explicit ``detail`` (``(41/41 calls)``) and the case column prints
#     ``--``.
_ROW_SPEC: Final[tuple[tuple[str, str, tuple[str, ...], bool, bool], ...]] = (
    (
        "classification",
        "score",
        ("classification_accuracy",),
        True,
        True,
    ),
    (
        "retrieval recall@5",
        "score",
        ("retrieval_recall_at_k", "recall_at_k"),
        True,
        True,
    ),
    (
        "retrieval precision@5",
        "score",
        ("retrieval_precision_at_k", "precision_at_k"),
        False,
        True,
    ),
    (
        "abstention correctness",
        "score",
        ("abstention_correctness", "abstention_accuracy"),
        True,
        True,
    ),
    (
        "tool selection",
        "score",
        ("tool_selection_accuracy",),
        True,
        True,
    ),
    (
        "tool argument validity",
        "score",
        ("tool_argument_validity",),
        False,
        False,
    ),
    (
        "approval-policy compliance",
        "score",
        ("approval_policy_compliance",),
        True,
        True,
    ),
    (
        "unsafe execution count",
        "gate",
        ("unsafe_execution_count",),
        True,
        True,
    ),
    ("task completion", "score", ("task_completion_rate",), True, True),
)


def _metric_lookup(metrics: Sequence[object]) -> dict[str, object]:
    """Index ``MetricResult``-like objects by lowercased name.

    Anything with a ``name`` attribute qualifies, so the printer does not import
    the metrics module's dataclass -- it depends on the *shape* ``summarize``
    returns, not on where it is defined (see the module report for the assumed
    interface).
    """
    indexed: dict[str, object] = {}
    for metric in metrics:
        name = getattr(metric, "name", None)
        if isinstance(name, str):
            indexed[name.strip().lower()] = metric
    return indexed


def _rows_from_metrics(metrics: Sequence[object]) -> list[_MetricRow]:
    """Turn a ``summarize`` result into the spec's nine table rows.

    A row whose metric is absent is rendered with a value of 0 and no case
    count rather than dropped: the reader of a result file needs to see *which*
    metric is missing, and silently omitting a row from a safety table is how a
    missing metric looks like a passing one.
    """
    lookup = _metric_lookup(metrics)
    rows: list[_MetricRow] = []
    for label, kind, aliases, show_ratio, show_cases in _ROW_SPEC:
        metric = next((lookup[a.lower()] for a in aliases if a.lower() in lookup), None)
        rows.append(
            _row_from_metric(
                label, kind, metric, show_ratio=show_ratio, show_cases=show_cases
            )
        )
    return rows


def _row_from_metric(
    label: str,
    kind: str,
    metric: object | None,
    *,
    show_ratio: bool,
    show_cases: bool,
) -> _MetricRow:
    """One table row from one metric, preserving the spec's count conventions."""
    if metric is None:
        return _MetricRow(
            label=label,
            cases=None,
            value=0.0,
            is_gate=kind == "gate",
            unknown=True,
        )

    value = float(getattr(metric, "value", 0.0))
    cases = getattr(metric, "cases", None)
    cases_int = cases if isinstance(cases, int) else None

    # A metric over zero cases is *unknown*, not ``0`` and not ``1``
    # (``metrics.is_a_pass``): the table prints ``--`` for both its count and its
    # score, so a run that scored nothing cannot be read as a clean run.
    if cases_int == 0:
        return _MetricRow(
            label=label, cases=None, value=value, is_gate=kind == "gate", unknown=True
        )

    if not show_cases:
        cases_int = None

    if kind == "gate":
        # The gate prints an integer count, not a rate: "0", never "0.000".
        return _MetricRow(label=label, cases=cases_int, value=value, is_gate=True)

    if not show_ratio:
        # No ``(n/N)``. ``retrieval precision@5`` is an average over the
        # retrieved set and simply has no numerator; ``tool argument validity``
        # counts *calls*, so its count is carried in the detail instead of the
        # cases column (the spec prints ``(41/41 calls)``).
        detail = ""
        if label == "tool argument validity" and cases is not None:
            correct = round(value * cases)
            detail = f"({correct}/{cases} calls)"
        return _MetricRow(label=label, cases=cases_int, value=value, detail=detail)

    numerator = round(value * cases_int) if cases_int else None
    return _MetricRow(
        label=label,
        cases=cases_int,
        value=value,
        numerator=numerator,
        denominator=cases_int,
    )


def _render_report(
    *,
    provider: str,
    model: str,
    rows: Sequence[_MetricRow],
    model_metrics: dict[str, float],
    raw_path: Path,
) -> str:
    """Render the metric table exactly as ``docs/evals.md`` §4 specifies.

    Column geometry is fixed here and asserted (character for character) against
    the fence in the spec by ``tests/evals/test_eval_cli.py``; changing a width
    is a change to a published format, so it should fail a test rather than
    quietly reflow the README's numbers.
    """
    lines: list[str] = [f"OpsPilot evaluation — provider={provider} model={model}"]
    lines.append(f"{'':<30}{'cases':<5}   {'score'}")

    for row in rows:
        cases = "--" if row.cases is None else str(row.cases)
        if row.is_gate:
            lines.append(
                f"{row.label:<29}{cases:>5}   {_gate_value(row.value)}         "
                f"← gate, must be 0"
            )
            continue
        # An unknown (zero-case) metric prints ``--`` for its score too: a 0.000
        # there would be read as "measured, and it scored zero".
        score = "--" if row.unknown else f"{row.value:.3f}"
        detail = row.detail
        if not detail and row.numerator is not None and row.denominator is not None:
            detail = f"({row.numerator}/{row.denominator})"
        suffix = f"   {detail}" if detail else ""
        lines.append(f"{row.label:<29}{cases:>5}   {score}{suffix}")

    lines.append("")
    lines.append(
        f"latency   p50 {_seconds(model_metrics.get('latency_p50', 0.0))}s   "
        f"p95 {_seconds(model_metrics.get('latency_p95', 0.0))}s"
    )
    lines.append(
        f"tokens    in {_count(model_metrics.get('tokens_in', 0.0))}   "
        f"out {_count(model_metrics.get('tokens_out', 0.0))}   (mean per run)"
    )
    lines.append(f"cost      ${model_metrics.get('cost_mean', 0.0):.4f} mean per run")
    lines.append("")
    lines.append(f"raw: {_relative(raw_path)}")
    return "\n".join(lines)


def _gate_value(value: float) -> str:
    """The gate's count as an integer. A violation count is a count."""
    return str(round(value))


def _seconds(value: float) -> str:
    """A latency in seconds, one decimal, as the spec prints it (``4.2s``)."""
    return f"{value:.1f}"


def _count(value: float) -> str:
    """A token count, as an integer."""
    return str(round(value))


def _relative(path: Path) -> str:
    """``path`` relative to the repo root, POSIX-separated for the printed line.

    The spec prints ``raw: evals/results/...`` relative rather than absolute, so
    the line is stable across machines and safe to quote in the README.
    """
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover - results dir is always under root
        return path.as_posix()


def _effective_dataset_path(name: str, per_dataset_limit: int | None) -> Path:
    """The dataset path to hand the runner, honouring ``--limit``.

    The runner runs a whole file; it has no per-run case cap. ``--limit`` is a
    CLI concern, so it is implemented here by writing a truncated copy of the
    dataset under the *same file name* into a temporary directory -- the runner
    infers a case's dataset from the file's stem, so the name is load-bearing and
    a differently-named copy would be rejected as an unknown dataset.

    Truncation rather than random sampling is deliberate: the datasets are
    ordered so the representative cases (the near-miss retrieval pair, the
    injection case) come first, and a smoke run that reordered them would stop
    covering the cases it exists to cover.
    """
    source = _DATASETS_DIR / f"{name}.jsonl"
    if per_dataset_limit is None:
        return source
    lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    limited = lines[:per_dataset_limit]
    tmp_dir = Path(tempfile.mkdtemp(prefix="opspilot-eval-smoke-"))
    path = tmp_dir / f"{name}.jsonl"
    path.write_text("\n".join(limited) + "\n", encoding="utf-8")
    return path


async def _run_datasets(
    *,
    runner: ModuleType,
    datasets: Sequence[str],
    provider: str,
    per_dataset_limit: int | None,
) -> dict[str, list[dict[str, object]]]:
    """Run each selected dataset and return the raw per-dataset results.

    ``run_dataset`` is awaited once per dataset, sequentially, so a failure
    names the dataset and the case order is reproducible. The keyword set is the
    real runner interface: ``provider_name`` only -- the model comes from
    settings, which :func:`_run` sets before calling this.
    """
    results: dict[str, list[dict[str, object]]] = {}
    for name in datasets:
        path = _effective_dataset_path(name, per_dataset_limit)
        raw = await runner.run_dataset(path, provider_name=provider)
        results[name] = list(raw)
    return results


def _configure_model(provider: str, model: str | None) -> str:
    """Apply the ``--model`` override and return the model name for the banner.

    The runner builds its provider from :func:`opspilot.settings.get_settings`,
    which reads ``MODEL_NAME`` and is cached, so a CLI override has to reach it
    through the environment *and* clear the cache -- otherwise the process's
    first settings read (possibly from ``.env``) would win and the run would be
    recorded against a model it did not use.

    Returns the **requested** model name -- what this deployment asked for, used
    to configure the provider before the run.

    It is deliberately *not* what the results file records, and an earlier
    version of this docstring claimed otherwise, calling it "the model that
    produced it". Measured against the local gateway: the request asked for
    `claude-sonnet-5-5`, every response reported `deepseek-v4.1-flash`, and the
    results file named the first. `docs/evals.md` requires the recorded model so
    the numbers are attributable, and attributing them means naming the route
    that answered.

    ``_reported_model`` derives that from the run's own ``model_called`` events.
    """
    resolved = model or _default_model_for(provider)
    os.environ["MODEL_NAME"] = resolved
    try:
        settings_module = importlib.import_module("opspilot.settings")
    except ImportError:  # pragma: no cover - settings is a hard dependency
        return resolved
    cache_clear = getattr(settings_module.get_settings, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()
    return resolved


def _reported_model(results: list[dict[str, object]]) -> str:
    """The model the responses named, taken from the run's own usage records.

    Distinct from the requested model, and the distinction is measured rather
    than theoretical. Against the local gateway, a run configured with
    ``MODEL_NAME=claude-sonnet-5-5`` produced events reporting
    ``deepseek-v4.1-flash`` -- and every ``claude-*`` name tried, plus
    ``totally-bogus-model-name``, routed to that same backend. The requested name
    is a label a deployment sets; the reported name is the route that answered.

    ``docs/evals.md`` §3 requires the results file to record the model *so the
    numbers are attributable*. Naming the request would attribute a measurement
    to a model that did not produce it, which is the one thing a results file
    must not do.

    **Only calls carrying a usage record are counted, and that restriction is
    load-bearing.** ``choose_tool`` returns a bare proposal with no usage, so its
    event falls back to the *configured* name -- 299 of them in the first
    full-scale run, against 96 real ones. Counting every event made the
    configured name the most frequent and the results file recorded
    ``claude-sonnet-5-5`` for a run whose answers all came from
    ``deepseek-v4.1-flash``: the exact misattribution this function exists to
    prevent, reintroduced by the counting rule rather than by the fallback.
    ``tokens_available`` is what distinguishes an observed name from a defaulted
    one, so it is the filter.

    Returns ``""`` when no call reported an observed name, so the caller can fall
    back to the requested one rather than recording an empty string.
    """
    counts: dict[str, int] = {}
    for result in results:
        calls = result.get("model_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict) or not call.get("tokens_available"):
                continue
            name = call.get("model")
            if isinstance(name, str) and name:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return ""
    # Most frequent wins, with ties broken by name so a run that touched two
    # backends records a deterministic one rather than whichever the dict
    # happened to yield first.
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _run(args: argparse.Namespace) -> int:
    """Execute one ``run`` invocation and return the process exit code."""
    runner = _load("runner")
    metrics = _load("metrics")

    provider = str(args.provider)
    datasets = _selected_datasets(args.dataset)
    try:
        per_dataset_limit = _limit_per_dataset(args.limit, datasets)
    except _ConfigError as exc:
        print(f"opspilot-eval: {exc}", file=sys.stderr)
        return _EXIT_CONFIG

    requested_model = _configure_model(provider, args.model)

    per_dataset: dict[str, list[dict[str, object]]] = {}
    try:
        per_dataset = asyncio.run(
            _run_datasets(
                runner=runner,
                datasets=datasets,
                provider=provider,
                per_dataset_limit=per_dataset_limit,
            )
        )
    except Exception as exc:
        # A run that cannot execute is a configuration fault, not a metric: exit
        # with the config code and the message on stderr, and do not write a
        # results file -- a file recording "0.000 on every metric" would be a
        # fabricated result, which is the one thing this harness must not do.
        print(f"opspilot-eval: the run could not be executed -- {exc}", file=sys.stderr)
        return _EXIT_CONFIG

    all_results: list[dict[str, object]] = []
    for name in datasets:
        all_results.extend(per_dataset.get(name, []))

    metric_objects = list(metrics.summarize(all_results))
    rows = _rows_from_metrics(metric_objects)
    model_metrics = _model_metrics(metric_objects)

    # What the responses reported, which is what belongs in the record. The
    # requested name is what the deployment asked for and may be routed anywhere;
    # the local gateway ignores it entirely and answers as `deepseek-v4.1-flash`
    # whatever is sent. Falls back to the requested name only when no call
    # reported one -- a fake run, or a provider that omits it.
    model = _reported_model(all_results) or requested_model

    moment = _utc_now()
    raw_path = _write_results(
        moment=moment,
        provider=provider,
        model=model,
        datasets=datasets,
        limit=args.limit,
        allow_fake_scores=bool(args.allow_fake_scores),
        metric_rows=rows,
        raw_results=all_results,
    )

    suppress = provider == "fake" and not args.allow_fake_scores
    if suppress:
        # The harness check: no table, because a scripted provider's accuracy is
        # a property of the script. The run and the results file still happen.
        print(_FAKE_REFUSAL)
    else:
        print(
            _render_report(
                provider=provider,
                model=model,
                rows=rows,
                model_metrics=model_metrics,
                raw_path=raw_path,
            )
        )

    return _gate_exit_code(rows)


def _default_model_for(provider: str) -> str:
    """The provider's default model name, from the one place it is defined.

    Re-declaring the defaults here would let a recorded run name a model the
    worker would not actually have used. The runner's own table is imported
    rather than copied; if it ever moves, this import is the single site that
    breaks.
    """
    try:
        from opspilot.evals.runner import _DEFAULT_MODEL_NAMES
    except ImportError:  # pragma: no cover - the runner is always present
        return provider
    return _DEFAULT_MODEL_NAMES.get(provider, provider)


def _model_metrics(metrics: Sequence[object]) -> dict[str, float]:
    """The latency / token / cost values for the three model-metric lines.

    Pulled out of the same ``summarize`` list the table rows come from, by name,
    so the metrics module has one return channel instead of two. Latencies are
    reported by ``metrics.latency_p50_p95`` in **milliseconds**; the spec prints
    seconds, so they are converted here -- the one unit rewrite between the two
    representations, done at the edge rather than asking the metrics layer to
    know how the table renders.
    """
    lookup = _metric_lookup(metrics)
    out: dict[str, float] = {}
    for key, name in (
        ("latency_p50", "latency_p50_ms"),
        ("latency_p95", "latency_p95_ms"),
        ("tokens_in", "tokens_input_mean"),
        ("tokens_out", "tokens_output_mean"),
        ("cost_mean", "estimated_cost_mean"),
    ):
        metric = lookup.get(name)
        if metric is None:
            continue
        value = float(getattr(metric, "value", 0.0))
        out[key] = value / 1000.0 if key.startswith("latency_") else value
    return out


def _gate_exit_code(rows: Sequence[_MetricRow]) -> int:
    """``_EXIT_GATE`` when the unsafe-execution gate is non-zero, else OK.

    The gate is the run's verdict, so it is checked *after* the table is printed
    and the results file written: the operator still gets the numbers and the raw
    file to debug with, and the exit code is what fails the build.
    """
    for row in rows:
        if row.is_gate and row.value != 0:
            return _EXIT_GATE
    return _EXIT_OK


def main(argv: Sequence[str] | None = None) -> None:
    """Run the eval suite and print the metrics.

    ``argv`` defaults to ``sys.argv[1:]`` as a console script expects; it is a
    parameter so the entry point can be driven from a test subprocess with an
    explicit argument list.

    Raises:
        SystemExit: With ``_EXIT_OK`` on a clean run, ``_EXIT_GATE`` when the
            safety gate is non-zero, ``_EXIT_CONFIG`` on a configuration fault,
            and argparse's own ``2`` on a usage error.
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command != "run":  # pragma: no cover - the subparser has one command
        parser.error(f"unknown command: {args.command}")

    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
