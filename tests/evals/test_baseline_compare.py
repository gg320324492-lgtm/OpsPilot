"""``opspilot-eval compare`` compares a results file with a stored baseline.

## What this file asserts

The comparison is the minimal baseline tool (Phase 3 M1-04): one command, one
baseline summary file, no evaluation platform. Each test drives the real CLI
in a subprocess (the exit code is part of the contract), against the
**committed** baseline and committed result files, so nothing here depends on
a run this machine happened to make.

- the identity section names the commit, provider, model and settings, and
  prints ``unknown`` for a fact the baseline does not carry;
- the dataset-hash section agrees when the datasets are unchanged, and says a
  difference confounds the metrics when one differs;
- a metric within tolerance is ``ok``; one that moved beyond it fails the
  process with the gate exit code;
- a metric one side did not measure (``cases: null``) is *unknown on that
  side*, never a fabricated ``0.000`` regression;
- the committed baseline is self-consistent: comparing it against its own
  source results file is within tolerance at tolerance 0.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = "evals/baselines/phase1-baseline.json"
_PYTHON = sys.executable


def _run_compare(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real CLI's ``compare`` subcommand as a subprocess."""
    return subprocess.run(  # noqa: S603 -- literal args, no external input
        [_PYTHON, "-m", "opspilot.evals", "compare", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO_ROOT),
        check=False,
    )


def test_comparing_the_baseline_with_its_own_source_run_is_within_tolerance() -> None:
    """The baseline derived from a file compares clean against that file.

    Tolerance 0, because this is the same numbers-against-themselves check the
    recomputable guard makes one level up: if this fails, the baseline summary
    no longer describes its own source file.
    """
    result = _run_compare(
        "--baseline",
        _BASELINE,
        "--results",
        "evals/results/2026-10-11T15-39-42.json",
        "--tolerance",
        "0",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "within tolerance" in result.stdout
    # The identity section names the pinned facts.
    assert "code commit" in result.stdout
    assert "MiniMax-M3.1-Flash-Preview" in result.stdout
    # The dataset hashes agree, and the report says so.
    assert "(all pinned dataset hashes agree)" in result.stdout


def test_a_metric_that_moved_beyond_tolerance_exits_with_the_gate_code() -> None:
    """The 2026-10-13 repeat run scored 2/15 on tool selection vs the baseline's 3/15.

    The difference is exactly one case (0.067); at tolerance 0 the comparison
    must fail with the gate exit code -- a comparison that moved is a finding
    about the system, not a usage error.
    """
    result = _run_compare(
        "--baseline",
        _BASELINE,
        "--results",
        "evals/results/2026-10-13T18-40-10.json",
        "--tolerance",
        "0",
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "moved beyond tolerance" in result.stdout
    # The delta is printed, not just the verdict.
    assert "-0.067" in result.stdout


def test_an_unmeasured_metric_is_unknown_not_a_zero_regression() -> None:
    """The 2026-10-13 file is a tool-selection-only run.

    Its ``classification`` row records ``cases: null`` -- the dataset was not
    run, not run-and-scored-zero. The comparison must say ``unknown`` for it;
    treating the recorded 0.0 as a score would report a fabricated -0.800
    regression on a metric nobody measured in that file.
    """
    result = _run_compare(
        "--baseline",
        _BASELINE,
        "--results",
        "evals/results/2026-10-13T18-40-10.json",
    )
    assert "unknown" in result.stdout
    assert "n/a (one side unknown)" in result.stdout


def test_a_missing_baseline_file_is_a_config_error() -> None:
    """A baseline that does not exist is an operator error, exit 1."""
    result = _run_compare("--baseline", "evals/baselines/does-not-exist.json")
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_the_committed_baseline_records_the_identity_facts() -> None:
    """The baseline pins commit, model, configuration and dataset hashes.

    These are the fields M1-04 requires the baseline to record; a baseline
    without them cannot tell "same code, same data, same model" and so cannot
    support any comparison.
    """
    baseline = json.loads((_REPO_ROOT / _BASELINE).read_text(encoding="utf-8"))
    for key in ("code_commit", "provider", "model", "model_config", "dataset_sha256", "metrics"):
        assert key in baseline, f"the baseline does not record {key!r}"
    assert baseline["code_commit"], "the baseline must name the commit it was produced at"
    assert len(baseline["dataset_sha256"]) == 4, "all four datasets are pinned"
    assert "unsafe execution count" in baseline["metrics"]
    assert baseline["metrics"]["unsafe execution count"]["value"] == 0.0


def test_the_baselines_directory_is_committed() -> None:
    """A baseline that lives only on one machine is not a baseline.

    ``.gitignore`` must not exclude ``evals/baselines/`` -- the summary and
    its recomputation note are part of the repository's evidence, unlike the
    run-by-run raw files under ``evals/results/``. Both files are asserted to
    exist on disk here; the *tracked* assertion is made by the commit itself
    (the branch carries them), so this file passes before and after the
    commit without depending on test-execution order.
    """
    gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "evals/baselines" not in gitignore
    assert (_REPO_ROOT / "evals" / "baselines" / "phase1-baseline.json").is_file()
    assert (_REPO_ROOT / "evals" / "baselines" / "README.md").is_file()
