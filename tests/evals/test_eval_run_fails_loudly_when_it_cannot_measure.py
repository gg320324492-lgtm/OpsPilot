"""An eval run that cannot measure anything must not exit 0.

## The defect this file exists for

Measured against a real provider with no API key:

```
$ OPENAI_API_KEY="" opspilot-eval run --provider openai --limit 2
...
tool selection                   1   0.000   (0/1)
unsafe execution count           1   0         <- gate, must be 0
task completion                  1   0.000   (0/1)
...
EXIT=0
```

Every case failed, the table printed `0/1`, the gate read `0` **because nothing
executed**, and the process exited **0**. A live run against a misconfigured
provider produced output that reads like a passing run.

That is the failure mode this project exists to prevent, and the eval harness is
a bad place for it: these numbers are the evidence a reader trusts, and a table
of zeros with a zero exit code is indistinguishable from a clean result on a
quiet day.

## Where the fault is, and where it is not

`runner._failed_result` records a per-case exception as a result with
``terminal_status='failed'`` rather than dropping it — **that part is right**,
and its docstring says why: "A dropped case would shrink the denominator and
flatter the score." The case is correctly counted as a failure.

The fault is one level up. When **every** case fails, the runner has not
measured anything, and it reports that as a set of failures rather than as the
configuration fault it is. A run where 0 of N cases produced a result has no
scores to report; printing them anyway is the report lying about what it knows.

The distinction matters and is the point of these tests:

- **Some cases failing** is a result. A model that errors on one ticket out of
  twenty is a measured fact about the model, and the metric should say so.
- **Every case failing** is not a result. It is the harness or the deployment,
  and no metric computed over it means anything.

So the guard cannot be "no failures" — that would be wrong and would fail on a
legitimately bad model. It has to be "no results at all".
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DATASETS = _REPO_ROOT / "evals" / "datasets"

#: Enough cases that "all of them failed" is unambiguous, few enough to be fast.
_LIMIT = "4"


def _run_eval(*args: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke the real console script with the environment controlled.

    A subprocess, because the exit code is the contract. ``--provider openai``
    with an empty key is the configuration fault under test: it is reachable
    without a network call and without spending credit, and it is exactly what a
    fresh clone with ``MODEL_PROVIDER=openai`` and no key looks like.
    """
    import os

    env = {
        **os.environ,
        "MODEL_PROVIDER": "openai",
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "",
        **env_overrides,
    }
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "opspilot.evals",
            "run",
            "--provider",
            "openai",
            "--limit",
            _LIMIT,
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        timeout=300,
    )


@pytest.fixture
def no_key_run() -> subprocess.CompletedProcess[str]:
    """One run against a provider that cannot possibly answer."""
    return _run_eval(env_overrides={})


def test_a_run_that_measured_nothing_exits_non_zero(
    no_key_run: subprocess.CompletedProcess[str],
) -> None:
    """The process must not report success when no case produced a result."""
    assert no_key_run.returncode != 0, (
        "an eval run where every case failed exited 0. The table it printed "
        "reads as a passing run -- `0/1`, `unsafe execution count 0`, exit 0 -- "
        "while measuring nothing. A configuration fault must be a nonzero exit, "
        "which is what CI acts on.\n\n"
        f"stdout:\n{no_key_run.stdout}\n\nstderr:\n{no_key_run.stderr}"
    )


def test_the_failure_names_the_provider_problem_not_just_a_zero(
    no_key_run: subprocess.CompletedProcess[str],
) -> None:
    """The message must say what is wrong, not only that something is.

    A nonzero exit alone is better than a false zero, but an operator still has
    to be told which knob to turn. ``MissingAPIKeyError`` already says it; the
    requirement is that it reaches the terminal rather than being summarized
    into a table of zeros.
    """
    combined = (no_key_run.stdout + no_key_run.stderr).lower()
    assert any(
        marker in combined for marker in ("api key", "api_key", "no api key", "missingapikey")
    ), (
        "the run failed without saying that the provider had no API key. "
        "Output was:\n" + no_key_run.stdout + no_key_run.stderr
    )


def test_a_partial_failure_is_still_a_result(
    no_key_run: subprocess.CompletedProcess[str],
) -> None:
    """The guard must not be "any failure fails the run".

    Asserted by contrast: with the *fake* provider, where cases succeed, the run
    exits 0. If this ever fails the rule has been over-applied, and a model that
    genuinely errors on one ticket would abort an otherwise valid measurement.

    This is the half that keeps the fix honest. "Every case failed" and "a case
    failed" are one condition apart in code and a world apart in meaning.
    """
    import os

    fake = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "opspilot.evals",
            "run",
            "--provider",
            "fake",
            "--allow-fake-scores",
            "--limit",
            _LIMIT,
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "MODEL_PROVIDER": "fake"},
        check=False,
        timeout=600,
    )
    assert fake.returncode == 0, (
        "a fake-provider run with results exited non-zero, so the "
        "measured-nothing guard is over-applied:\n" + fake.stdout + fake.stderr
    )


def test_the_datasets_are_present_so_this_guard_is_not_vacuous() -> None:
    """Sanity: the run under test has real cases to fail on.

    Without this, a missing or renamed dataset directory would make "every case
    failed" true for the wrong reason -- zero cases ran, so zero results were
    produced. The guard would pass while testing nothing.
    """
    files = sorted(_DATASETS.glob("*.jsonl"))
    assert files, f"no datasets under {_DATASETS}; the eval run has nothing to score"
    total = sum(
        len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
        for path in files
    )
    assert total >= int(_LIMIT), (
        f"the datasets hold {total} cases, fewer than the {_LIMIT} this guard "
        "asks for, so the run is not exercising what it claims"
    )
