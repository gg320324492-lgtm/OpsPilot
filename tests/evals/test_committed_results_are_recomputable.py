"""The committed eval results must still be what ``metrics.py`` computes from them.

## Why this file exists

``evals/results/`` holds a few hundred result files, but only a handful are
committed to the repository: ``.gitignore`` ignores ``evals/results/*`` and the
committed ones are there anyway. Those committed files are the **only** source
of the numbers ``docs/evals.md`` and the README quote, so a change to the
*semantics* of a metric -- a different denominator, a different ``source``
filter, a different aggregation level -- silently invalidates every published
number while every test stays green.

Nothing bound the two together. ``tests/evals/test_metrics_definitions.py``
asserts hand-computed values against hand-written literal result sets, so it
agrees with whatever the metrics do; the CLI tests read the files a test run
has just written and never assert a number; CI's ``eval-smoke`` job only proves
the plumbing exits zero. "The metrics changed, the recorded numbers did not,
and nobody noticed" was a passing state of this repository.

## What this file asserts

For every committed result file, independently:

* every row of the file's ``metrics[]`` still resolves to a metric of today's
  ``summarize()`` -- through the writer's own display-name table -- and its
  ``value``, ``cases`` and ``gate`` flag still agree;
* every entry of the file's ``breakdown[]`` is still produced, with the same
  value and the same count;
* the breakdown today's code computes still reconstructs the row the file
  recorded, and the two independently-derived splits of the same tool calls
  still agree with each other.

## What it deliberately does not assert

That a file written before a metric existed contains that metric.
``2026-10-08`` and ``2026-10-11`` predate the ``breakdown`` key, and both
``2026-10-13`` files predate ``malformed_argument_proposals``. A committed
result file is a raw record and is never edited, so this guard checks that what
the file says is still *true* -- not that the file says everything today's code
could say. Asserting the absence of a key would assert a fact about the past
that no re-run can change, and would make the guard red for a reason that has
nothing to do with the metrics.

## Offline by construction

The only thing outside this process is one ``git ls-files``, used to decide
which result files are committed. No network, no model, no database, no
Docker, and no untracked file is read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from opspilot.evals.__main__ import _ROW_SPEC, _breakdown_rows
from opspilot.evals.metrics import MetricResult, summarize

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_DIR = _REPO_ROOT / "evals" / "results"

#: The result files committed to the repository as of this file being written.
#:
#: This list is **not** the guard's source of truth -- ``git ls-files`` is --
#: and it exists only so that the guard still has teeth in an environment
#: where ``git`` cannot be run. See :func:`_tracked_result_files`. The list is
#: kept honest by :func:`test_the_known_committed_result_files_are_still_committed`,
#: which fails if a file named here is no longer tracked.
_KNOWN_COMMITTED_RESULTS = (
    "2026-10-08T05-24-41.json",
    "2026-10-11T15-39-42.json",
    "2026-10-13T18-24-25.json",
    "2026-10-13T18-40-10.json",
)

#: The writer's display name -> (kind, the metric names the row may bind to,
#: whether the row shows a case count). Read from ``_ROW_SPEC`` rather than
#: copied.
#:
#: ``_ROW_SPEC`` is a module-level constant of ``opspilot.evals.__main__``, and
#: importing that module has no side effect: it defines constants, one
#: dataclass and some functions -- it reads no environment, opens no database,
#: starts no thread and makes no network call. Importing the writer's own
#: table is therefore both safe and the right thing to do. A copy written here
#: would be a *second* source of truth for the display names, and a second
#: source of truth drifting away from the first is precisely the failure this
#: guard exists to catch.
_ROWS = {
    label: (kind, aliases, show_cases)
    for label, kind, aliases, _show_ratio, show_cases in _ROW_SPEC
}


def _git_tracked_result_names() -> tuple[list[str], str]:
    """The committed result files, relative to the repository root, and how they were found.

    ``git ls-files`` is the only honest answer to "which result files are
    committed": ``.gitignore`` ignores ``evals/results/*`` and the committed
    files are there regardless (they were force-added), so a glob over the
    directory would also find the local run residue -- files from incomplete
    runs that would make the guard go red at random, and for no reason.

    ``-z`` and the split on NUL are not fussiness. ``git`` prints forward
    slashes, and NUL is the only separator that cannot appear in a path, so
    the split is unambiguous on every platform.

    The second element of the returned tuple says how the list was obtained, so
    a failure can name the mechanism rather than leaving the reader to guess.
    """
    git = shutil.which("git")
    if git is None:
        return [], "git was not found on PATH"
    # S603: the argument list is a resolved absolute path to git plus literal
    # flags this file wrote. Nothing here comes from outside the test process.
    completed = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z", "--", "evals/results"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return [], f"git ls-files exited {completed.returncode}: {completed.stderr.strip()}"
    names = [name for name in completed.stdout.split("\0") if name.endswith(".json")]
    return names, "git ls-files"


def _tracked_result_files() -> tuple[list[Path], str]:
    """The committed result files to guard, and how they were found.

    ``git`` is the primary answer, so a result file committed later is guarded
    without anyone editing this file. When ``git`` cannot be run at all, the
    known committed files are used **instead of skipping**: a skipped guard is a
    guard that can never go red, which is the failure mode this file exists to
    prevent. The fallback is weaker (it cannot notice a newly committed file)
    and is not silent -- :func:`test_the_known_committed_result_files_are_still_committed`
    says so out loud whenever ``git`` is available.
    """
    names, how = _git_tracked_result_names()
    if not names:
        fallback = [str(_RESULTS_DIR / name) for name in _KNOWN_COMMITTED_RESULTS]
        return [_REPO_ROOT / name for name in fallback], f"{how}; using the known committed files"
    return [_REPO_ROOT / name for name in names], how


_TRACKED_FILES, _DISCOVERY = _tracked_result_files()
_TRACKED_IDS = [path.name for path in _TRACKED_FILES]


def _load(path: Path) -> dict[str, object]:
    """The parsed contents of a results file."""
    raw: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return raw


def _recomputed(raw: dict[str, object]) -> dict[str, MetricResult]:
    """Today's metrics for a recorded result set, indexed by lowercased name.

    ``summarize`` is a pure function over ``results[]`` -- it re-reads no case,
    calls no model and touches no network -- so recomputing a committed file is
    exactly the operation the file performed when it was written.
    """
    results = raw.get("results")
    assert isinstance(results, list), "a results file must carry a 'results' array"
    return {metric.name.lower(): metric for metric in summarize(results)}


def _recorded_rows(raw: dict[str, object], key: str) -> list[dict[str, object]]:
    """The rows a results file records under ``key``, as dictionaries."""
    rows = raw.get(key)
    assert isinstance(rows, list), f"a results file must carry a '{key}' array"
    recorded: list[dict[str, object]] = []
    for row in rows:
        assert isinstance(row, dict), f"every '{key}' entry must be an object"
        recorded.append(row)
    return recorded


@pytest.mark.parametrize("path", _TRACKED_FILES, ids=_TRACKED_IDS)
def test_a_committed_result_records_the_table_and_it_still_computes_the_same_way(
    path: Path,
) -> None:
    """Every row of a committed result's ``metrics[]`` is still what the metrics compute.

    The comparison is exact, not approximate. The file was written by
    ``json.dumps`` of the very same ``float`` the metric produced, and the
    recomputation performs the same arithmetic over the same recorded values in
    the same order, so the two are the identical IEEE-754 double. ``approx``
    would be the wrong tool twice over: it would hide a drift smaller than its
    tolerance -- precisely the "the denominator moved a little" change this
    guard exists to catch -- and it would paper over a real divergence in a
    number the documents quote verbatim.

    ``cases`` needs one distinction the file's ``null`` does not make for us.
    A ``null`` count means one of two different things, and both are legitimate:

    * the row is ``tool argument validity``, whose count is over *calls* rather
      than cases and travels in the row's detail instead of this column
      (``show_cases`` is False for it in ``_ROW_SPEC``); or
    * the metric was computed over zero cases, which the writer renders as
      *unknown* -- neither ``0`` nor ``1``, but "we scored nothing" -- and
      which therefore records no count either.

    The test asserts which of the two applies rather than accepting any
    ``null``, so a metric that silently stopped being measured is not mistaken
    for one that was never asked to show a count.
    """
    assert path.is_file(), f"the committed result file {path.name} is missing from the working tree"
    raw = _load(path)
    recomputed = _recomputed(raw)
    rows = _recorded_rows(raw, "metrics")

    labels = [row["name"] for row in rows]
    assert labels == list(_ROWS), (
        f"{path.name} records the rows {labels}, but the writer's table is now {list(_ROWS)}; "
        "a new or renamed row means the committed results are stale and a fresh run "
        "has to be committed with them"
    )

    for row in rows:
        label = row["name"]
        # A row's display name is a string. Asserting it narrows the type for
        # the lookup below and states the fact the lookup depends on: the row
        # names a metric in the writer's own table.
        assert isinstance(label, str), f"{path.name}: a row's name is not a string: {label!r}"
        kind, aliases, show_cases = _ROWS[label]
        metric = next(
            (recomputed[alias.lower()] for alias in aliases if alias.lower() in recomputed),
            None,
        )
        assert metric is not None, (
            f"{path.name}: no metric named {aliases} is computed any more, so the recorded "
            f"row {label!r} cannot be reproduced"
        )

        recorded_value = row["value"]
        assert recorded_value == metric.value, (
            f"{path.name}: the row {label!r} records {recorded_value!r} but today's metrics "
            f"compute {metric.value!r} from the same recorded results"
        )

        recorded_gate = row["gate"]
        assert recorded_gate is (kind == "gate"), (
            f"{path.name}: the row {label!r} records gate={recorded_gate!r}, but the writer's "
            f"table makes it a {'gate' if kind == 'gate' else 'score'}"
        )

        recorded_cases = row["cases"]
        if metric.cases == 0:
            expected_cases = None
            why = "the metric was computed over zero cases, which is unknown, not zero"
        elif not show_cases:
            expected_cases = None
            why = "the row counts calls rather than cases and shows no case count"
        else:
            expected_cases = metric.cases
            why = ""
        assert recorded_cases == expected_cases, (
            f"{path.name}: the row {label!r} records cases={recorded_cases!r} but today's "
            f"metrics compute {metric.cases!r}" + (f" -- {why}" if why else "")
        )


@pytest.mark.parametrize("path", _TRACKED_FILES, ids=_TRACKED_IDS)
def test_a_committed_result_records_the_breakdown_and_it_is_still_produced(path: Path) -> None:
    """Every entry of a committed result's ``breakdown[]`` is still produced, unchanged.

    The breakdown carries the composition the printed table deliberately does
    not: the per-dataset split of the argument-validity metric, the empty and
    malformed proposal counts, and the rejected calls counted by the gate that
    refused them. The two ``2026-10-13`` files carry it; the two older files
    predate the key and are skipped here -- a committed result file is a raw
    record and is never edited, so the absence of a key is a fact about the
    past, not a metric that went wrong. What today's code computes for those
    files is checked against their recorded rows by
    :func:`test_the_breakdown_today_computes_still_agrees_with_the_row_the_file_recorded`.

    The file's breakdown is a *subset* of what today's code computes, and must
    stay one: the ``2026-10-13`` files predate ``malformed_argument_proposals``,
    so requiring the two sets to be equal would fail for a reason that has
    nothing to do with the numbers.
    """
    raw = _load(path)
    recomputed = _recomputed(raw)
    recorded = raw.get("breakdown")
    if recorded is None:
        pytest.skip(
            f"{path.name} was written before the breakdown was recorded, so it has none to check"
        )

    rows = _recorded_rows(raw, "breakdown")
    writer_names = {
        getattr(metric, "name", "") for metric in _breakdown_rows(list(recomputed.values()))
    }
    for row in rows:
        name = row["name"]
        assert name in writer_names, (
            f"{path.name}: the breakdown records {name!r}, which the writer would not produce"
        )
        metric = recomputed.get(str(name).lower())
        assert metric is not None, (
            f"{path.name}: the breakdown records {name!r} but today's metrics do not compute it"
        )
        recorded_value = row["value"]
        assert recorded_value == metric.value, (
            f"{path.name}: the breakdown entry {name!r} records {recorded_value!r} but today's "
            f"metrics compute {metric.value!r}"
        )
        recorded_cases = row["cases"]
        assert recorded_cases == metric.cases, (
            f"{path.name}: the breakdown entry {name!r} records cases={recorded_cases!r} but "
            f"today's metrics compute {metric.cases!r}"
        )


@pytest.mark.parametrize("path", _TRACKED_FILES, ids=_TRACKED_IDS)
def test_the_breakdown_today_computes_still_agrees_with_the_row_the_file_recorded(
    path: Path,
) -> None:
    """The composition today's code computes still reconstructs the numbers the file recorded.

    This is the check that stands in for the two files that predate the
    ``breakdown`` key, and it runs for all of them because it needs nothing
    from the file beyond the row it already records.

    Two independent views of the same tool calls have to agree:

    * ``tool_argument_validity`` pools every proposed call; the per-dataset
      breakdown reaches the same calls by a different route -- it groups by the
      dataset of the case that proposed them. If the two ever disagree about
      the denominator or the numerator, the pooled ratio and the split can no
      longer be read as the same number, and the file's recorded ratio is the
      one that would be silently wrong.
    * the gate split counts the calls each gate refused; the empty/malformed
      split classifies the same calls by what the model actually proposed. A
      call the schema gate refused is precisely a call the model got wrong --
      it filled in nothing, or it filled in something the schema refused -- and
      a call the registry gate refused is precisely one naming a tool that has
      no schema. This is the coupling that catches a change to
      ``TOOL_ARGUMENT_SCHEMAS``: re-registering a tool's schema would move
      calls out of the empty/malformed buckets without moving a single one of
      the recorded rejection reasons.
    """
    raw = _load(path)
    recomputed = _recomputed(raw)

    pooled = recomputed["tool_argument_validity"]
    per_dataset = {
        name: metric
        for name, metric in recomputed.items()
        if name.startswith("tool_argument_validity.")
    }
    assert per_dataset, f"{path.name}: the per-dataset breakdown computed nothing to compare"

    total_cases = sum(metric.cases for metric in per_dataset.values())
    total_valid = sum(round(metric.value * metric.cases) for metric in per_dataset.values())
    assert total_cases == pooled.cases, (
        f"{path.name}: the per-dataset breakdown covers {total_cases} calls while the pooled "
        f"metric was computed over {pooled.cases}"
    )
    assert total_cases > 0, f"{path.name}: the pooled argument-validity metric has no cases"

    ratio = total_valid / total_cases
    assert ratio == pooled.value, (
        f"{path.name}: the per-dataset breakdown reconstructs {ratio!r} while the pooled metric "
        f"computes {pooled.value!r}"
    )
    recorded = next(
        row for row in _recorded_rows(raw, "metrics") if row["name"] == "tool argument validity"
    )
    recorded_value = recorded["value"]
    assert recorded_value == ratio, (
        f"{path.name}: the recorded argument-validity row is {recorded_value!r} while today's "
        f"breakdown reconstructs {ratio!r} from the same recorded calls"
    )

    empty = recomputed["empty_argument_proposals"]
    malformed = recomputed["malformed_argument_proposals"]
    gate_1 = recomputed.get("rejected_calls.gate_1_schema_validation")
    gate_2 = recomputed.get("rejected_calls.gate_2_registry_lookup")
    gate_1_count = gate_1.value if gate_1 is not None else 0.0
    gate_2_count = gate_2.value if gate_2 is not None else 0.0

    assert gate_1_count == empty.value + malformed.value, (
        f"{path.name}: the schema gate refused {gate_1_count} calls while the empty and malformed "
        f"buckets hold {empty.value + malformed.value} -- the two splits of the same calls "
        "disagree"
    )

    unknown_tool = pooled.cases - round(pooled.value * pooled.cases) - empty.value - malformed.value
    assert gate_2_count == unknown_tool, (
        f"{path.name}: the registry gate refused {gate_2_count} calls while {unknown_tool} calls "
        "name a tool with no schema -- the two splits of the same calls disagree"
    )


def test_the_known_committed_result_files_are_still_committed() -> None:
    """The fallback list must not go stale behind the guard's back.

    ``git ls-files`` is what the guard uses, so a result file that is deleted
    or untracked simply stops being guarded -- silently, and with no failure
    anywhere. This test is the counterweight: as long as ``git`` can be run,
    every file named in :data:`_KNOWN_COMMITTED_RESULTS` must still be tracked.

    It is skipped when ``git`` is unavailable, because there is then nothing to
    cross-check against. The skip is deliberate and narrow: it applies to this
    cross-check only, never to the guard itself, which falls back to the same
    list and keeps asserting.
    """
    names, how = _git_tracked_result_names()
    if not names:
        pytest.skip(f"git could not list the committed result files ({how})")
    tracked = {name.rsplit("/", 1)[-1] for name in names}
    missing = sorted(set(_KNOWN_COMMITTED_RESULTS) - tracked)
    assert not missing, (
        f"these result files are no longer tracked by git, so the guard would stop covering "
        f"them: {missing}"
    )


def test_the_guard_covers_every_committed_result_file() -> None:
    """The guard's file list is the committed one, and every file in it is there.

    A guard that quietly covered two files out of four would be worse than no
    guard, because it would read as a passing baseline. So this asserts the
    list is not empty, that it holds the known committed files, and that each
    one exists on disk.
    """
    assert _TRACKED_FILES, f"the guard found no committed result files ({_DISCOVERY})"
    tracked_names = {path.name for path in _TRACKED_FILES}
    missing = sorted(set(_KNOWN_COMMITTED_RESULTS) - tracked_names)
    assert not missing, f"the guard does not cover the known committed files: {missing}"
    for path in _TRACKED_FILES:
        assert path.is_file(), f"the committed result file {path.name} is missing from disk"
