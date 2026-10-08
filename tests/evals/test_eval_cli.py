"""``opspilot-eval`` must behave as a console script, not just as a function.

## Why this file runs the CLI in a subprocess

``pyproject.toml`` declares ``opspilot-eval = "opspilot.evals.__main__:main"``.
That is how the program is used: a shell invokes it, and the *exit code* is part
of the contract -- CI's ``eval-smoke`` job and the ``unsafe execution count``
gate both act on it. Calling ``main()`` in-process would test the parsing but
not the thing CI depends on, and ``SystemExit`` raised over a shared interpreter
is a different program from one that owns its process.

## How a run is driven without the runner

``runner.py`` and ``metrics.py`` are implemented by a parallel milestone. These
tests therefore substitute doubles for those two modules by writing them into
``sys.modules`` *before* the CLI imports them lazily -- the CLI reaches for them
through :func:`importlib.import_module`, which honours ``sys.modules``. The
driver script that does this is itself launched as a subprocess, so the real
``argparse``, the real exit codes and the real ``main`` are all exercised.

The doubles also let a test force the one thing a real run cannot force on
demand: a non-zero ``unsafe execution count``. The gate's whole point is that it
fails the process, so it has to be tested by making it trip.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The exact string the four documents agree on. Asserted verbatim, em dash and
# all, because a summary that drifts from the spec is the failure mode this
# whole harness exists to avoid.
_FAKE_REFUSAL = "fake provider — harness check only, not a model result"

# Metric names the CLI is written against. Declared here, once, so the doubles
# and the expectations cannot disagree with the rows they feed.
_METRIC_NAMES = (
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


def _write_doubles(directory: Path) -> None:
    """Write fake ``runner``/``metrics`` modules into a directory on the path.

    They live in a temporary package named to shadow the real submodules'
    *lookup*, not their files: the driver installs them into ``sys.modules``
    under the real dotted names.
    """
    (directory / "fake_runner.py").write_text(
        textwrap.dedent(
            '''
            """A runner double: records the call and returns one raw result."""

            from __future__ import annotations

            #: One entry per call, so a test can assert the exact keyword set the
            #: CLI passes -- the runner's real signature is `provider_name` only.
            CALLS: list[dict[str, object]] = []


            async def run_dataset(path, *, provider_name="fake", dataset_dir=None):
                lines = [
                    ln
                    for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                ]
                CALLS.append(
                    {
                        "path": str(path),
                        "provider_name": provider_name,
                        "cases": len(lines),
                    }
                )
                return [
                    {"case_id": f"c{i}", "ok": True, "dataset": path.stem}
                    for i in range(len(lines))
                ]
            '''
        ).lstrip(),
        encoding="utf-8",
    )
    (directory / "fake_metrics.py").write_text(
        textwrap.dedent(
            '''
            """A metrics double: a fixed, spec-shaped set of results."""

            from __future__ import annotations

            from dataclasses import dataclass


            @dataclass(frozen=True)
            class MetricResult:
                name: str
                value: float
                cases: int = 0
                detail: str = ""


            def _unsafe_count() -> int:
                import os

                return int(os.environ.get("FAKE_UNSAFE_COUNT", "0"))


            def summarize(results):
                unsafe = _unsafe_count()
                return [
                    MetricResult("classification_accuracy", 0.900, 20),
                    MetricResult("retrieval_recall_at_k", 0.850, 20),
                    MetricResult("retrieval_precision_at_k", 0.612, 20),
                    MetricResult("abstention_correctness", 1.000, 4),
                    MetricResult("tool_selection_accuracy", 0.867, 15),
                    MetricResult("tool_argument_validity", 1.000, 41),
                    MetricResult("approval_policy_compliance", 1.000, 15),
                    MetricResult("unsafe_execution_count", float(unsafe), 15),
                    MetricResult("task_completion_rate", 0.917, 12),
                    MetricResult("latency_p50_ms", 4200.0, 20),
                    MetricResult("latency_p95_ms", 11800.0, 20),
                    MetricResult("tokens_input_mean", 1840.0, 20),
                    MetricResult("tokens_output_mean", 312.0, 20),
                    MetricResult("estimated_cost_mean", 0.0184, 20),
                ]
            '''
        ).lstrip(),
        encoding="utf-8",
    )


_DRIVER = textwrap.dedent(
    '''
    """Run the real CLI with the runner/metrics doubles installed."""

    from __future__ import annotations

    import importlib
    import sys

    import fake_metrics
    import fake_runner

    runner_mod = importlib.import_module("opspilot.evals.runner")
    metrics_mod = importlib.import_module("opspilot.evals.metrics")
    sys.modules["opspilot.evals.runner"] = fake_runner
    sys.modules["opspilot.evals.metrics"] = fake_metrics
    # `from opspilot.evals import runner` would read the parent's attribute, so
    # set that too; the CLI's lazy import handles the sys.modules route either
    # way, but a stray attribute import must not reach the stub and raise.
    parent = importlib.import_module("opspilot.evals")
    parent.runner = fake_runner
    parent.metrics = fake_metrics
    del runner_mod, metrics_mod

    from opspilot.evals.__main__ import main

    main(sys.argv[1:])
    '''
).lstrip()


@dataclass(frozen=True)
class _Cli:
    """A ready-to-run CLI driver: the script, its environment, and overrides."""

    driver: Path
    env: dict[str, str]


@pytest.fixture
def cli_env(tmp_path: Path) -> Iterator[_Cli]:
    """A driver script plus the environment to run it as a subprocess.

    The CLI writes into the real ``evals/results/`` by design -- that is where a
    run's raw file belongs -- so this fixture records what was there before and
    removes whatever a test added, keeping the working tree as it found it.
    """
    results_dir = _REPO_ROOT / "evals" / "results"
    before = set(results_dir.glob("*.json")) if results_dir.exists() else set()

    _write_doubles(tmp_path)
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(_REPO_ROOT / "src"), env.get("PYTHONPATH", "")]
    )
    try:
        yield _Cli(driver=driver, env=env)
    finally:
        for path in set(results_dir.glob("*.json")) - before:
            path.unlink(missing_ok=True)


def _run_cli(cli: _Cli, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI as a subprocess and capture its output and exit code."""
    # S603: every element is either the test interpreter, a path the fixture
    # created under tmp_path, or a literal flag the test itself wrote. Nothing
    # here comes from outside the test process.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(cli.driver), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=cli.env,
        check=False,
    )


def test_fake_provider_suppresses_the_table_and_exits_zero(
    cli_env: _Cli,
) -> None:
    """``--provider fake`` without ``--allow-fake-scores`` prints the notice.

    The smoke job relies on this: it is a plumbing check, so it must *run* (exit
    0), print the refusal line instead of numbers, and never claim a score.
    """
    result = _run_cli(cli_env, "run", "--provider", "fake")
    assert result.returncode == 0, result.stderr
    assert _FAKE_REFUSAL in result.stdout
    assert "classification" not in result.stdout
    assert "raw:" not in result.stdout


def test_fake_provider_allow_fake_scores_prints_the_table(
    cli_env: _Cli,
) -> None:
    """``--allow-fake-scores`` is the switch that un-suppresses the table."""
    result = _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    assert result.returncode == 0, result.stderr
    assert _FAKE_REFUSAL not in result.stdout
    assert "unsafe execution count" in result.stdout
    assert "raw:" in result.stdout


def test_table_matches_the_spec_character_for_character(
    cli_env: _Cli,
) -> None:
    """The printed table equals the fenced block in ``docs/evals.md`` §4.

    Read from the spec rather than retyped, so the test cannot silently agree
    with a *copy* of the format that has drifted from the published one.
    """
    result = _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    assert result.returncode == 0, result.stderr

    spec_lines = (_REPO_ROOT / "docs" / "evals.md").read_text(encoding="utf-8").splitlines()
    # The fence block runs from the banner line to the `raw:` line inclusive.
    start = spec_lines.index(
        "OpsPilot evaluation — provider=anthropic model=claude-sonnet-5-5"
    )
    end = next(i for i in range(start, len(spec_lines)) if spec_lines[i].startswith("raw:"))
    expected = spec_lines[start : end + 1]

    produced = result.stdout.splitlines()
    # The driver's banner names the fake provider/model; normalise only that
    # first line, which is the one value the spec example necessarily differs on.
    produced[0] = "OpsPilot evaluation — provider=anthropic model=claude-sonnet-5-5"
    # The raw path is timestamped per run; the spec shows one example run.
    produced[-1] = "raw: evals/results/2026-10-05T11-20-03.json"
    assert produced == expected


def test_unsafe_execution_count_trips_a_non_zero_exit(
    cli_env: _Cli,
) -> None:
    """A non-zero gate fails the process and still prints the table.

    The gate is the run's verdict: the operator gets the numbers *and* the
    non-zero exit, so the build fails without hiding why.
    """
    cli_env.env["FAKE_UNSAFE_COUNT"] = "1"
    result = _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    assert result.returncode != 0
    assert result.returncode == 3, "the gate must be distinguishable from a config fault"
    assert "unsafe execution count" in result.stdout


def test_zero_gate_exits_zero(cli_env: _Cli) -> None:
    """The gate at 0 is a pass, so the process exits 0."""
    cli_env.env["FAKE_UNSAFE_COUNT"] = "0"
    result = _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    assert result.returncode == 0, result.stderr


def test_limit_selects_representative_cases_round_robin(
    cli_env: _Cli,
) -> None:
    """``--limit 8`` runs 8 cases total, spread across every dataset.

    The smoke job needs this: 8 cases that span all four datasets catch a
    plumbing regression in any one of them, where 8 cases from one dataset
    would not.
    """
    result = _run_cli(
        cli_env, "run", "--provider", "fake", "--allow-fake-scores", "--limit", "8"
    )
    assert result.returncode == 0, result.stderr
    raw = _latest_results_file()
    assert _config(raw)["limit"] == 8
    # 8 over 4 datasets is 2 per dataset (ceil), and every dataset contributed a
    # case -- the round-robin property the smoke job depends on.
    datasets = {str(r["dataset"]) for r in _case_results(raw)}
    assert datasets == {"classification", "retrieval", "tool_selection", "safety"}
    assert len(_case_results(raw)) == 8


def test_dataset_flag_restricts_the_run(cli_env: _Cli) -> None:
    """``--dataset`` runs only the named dataset."""
    result = _run_cli(
        cli_env, "run", "--provider", "fake", "--allow-fake-scores", "--dataset", "safety"
    )
    assert result.returncode == 0, result.stderr
    raw = _latest_results_file()
    assert _config(raw)["datasets"] == ["safety"]
    assert {str(r["dataset"]) for r in _case_results(raw)} == {"safety"}


def test_results_file_records_provider_model_date_and_config(
    cli_env: _Cli,
) -> None:
    """The raw file must say what produced it (``evals/results/README.md``)."""
    result = _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    assert result.returncode == 0, result.stderr
    raw = _latest_results_file()
    assert raw["provider"] == "fake"
    assert raw["model"]  # the provider's default, recorded rather than blank
    assert raw["date"]
    assert "embedding_provider" in _config(raw)


def test_results_filename_matches_the_spec_timestamp_format(
    cli_env: _Cli,
) -> None:
    """The filename is ``<timestamp>.json`` with colons replaced by dashes."""
    _run_cli(cli_env, "run", "--provider", "fake", "--allow-fake-scores")
    name = _latest_results_filename()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.json", name), name


def test_unknown_provider_is_a_usage_error(cli_env: _Cli) -> None:
    """argparse rejects an unknown provider with its own exit code 2."""
    result = _run_cli(cli_env, "run", "--provider", "acme")
    assert result.returncode == 2


def _results_dir() -> Path:
    """The results directory under the repository root."""
    return _REPO_ROOT / "evals" / "results"


def _latest_results_file() -> dict[str, object]:
    """Parse the newest ``*.json`` in the results directory."""
    candidates = sorted(_results_dir().glob("*.json"))
    assert candidates, "no results file was written"
    parsed: dict[str, object] = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return parsed


def _config(raw: dict[str, object]) -> dict[str, object]:
    """The ``config`` object from a results file, typed for indexing."""
    config = raw["config"]
    assert isinstance(config, dict)
    return config


def _case_results(raw: dict[str, object]) -> list[dict[str, object]]:
    """The per-case raw results from a results file, typed for iteration."""
    results = raw["results"]
    assert isinstance(results, list)
    return [r for r in results if isinstance(r, dict)]


def _latest_results_filename() -> str:
    """The newest results filename."""
    candidates = sorted(_results_dir().glob("*.json"))
    assert candidates, "no results file was written"
    return candidates[-1].name


# -- the recorded model must be an observed one -------------------------------


def test_the_recorded_model_ignores_defaulted_names() -> None:
    """Only calls that reported their own name may decide the recorded model.

    Found in the first full-scale live run. `choose_tool` returns a bare
    proposal with no usage, so its ``model_called`` event carries the
    *configured* name as a fallback -- 299 of them against 96 real ones. The
    counting rule was "most frequent", so the configured name won and the
    results file recorded ``claude-sonnet-5-5`` for a run whose every answer
    came from ``deepseek-v4.1-flash``.

    That is the misattribution ``_reported_model`` exists to prevent,
    reintroduced by the counting rule rather than by the fallback. The two cases
    below are the whole distinction: one where the defaulted name is more
    frequent and must lose, and one where it is the only name and must still be
    usable as a last resort.
    """
    from opspilot.evals.__main__ import _reported_model

    observed = {"model": "deepseek-v4.1-flash", "tokens_available": True}
    defaulted = {"model": "claude-sonnet-5-5", "tokens_available": False}

    results: list[dict[str, object]] = [
        {"case_id": "c1", "model_calls": [observed]},
        {"case_id": "c2", "model_calls": [defaulted, defaulted, defaulted]},
    ]
    assert _reported_model(results) == "deepseek-v4.1-flash", (
        "a defaulted name outvoted an observed one; the results file would name "
        "a model that did not answer"
    )


def test_no_observed_name_at_all_yields_empty_so_the_caller_can_fall_back() -> None:
    """A run with only defaulted names reports nothing, so ``_run`` falls back.

    Not the configured name: returning it here would make the caller's fallback
    unreachable and hide the fact that nothing observed a name. The fake
    provider's events carry a fixture-recorded name, so this is the shape a
    provider that reports nothing produces.
    """
    from opspilot.evals.__main__ import _reported_model

    defaulted = {"model": "claude-sonnet-5-5", "tokens_available": False}
    assert _reported_model([{"case_id": "c1", "model_calls": [defaulted]}]) == ""
    assert _reported_model([{"case_id": "c1", "model_calls": []}]) == ""
