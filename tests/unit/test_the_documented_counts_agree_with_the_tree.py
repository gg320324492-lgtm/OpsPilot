"""The counts the documents quote must be the counts the tree has.

## Why this file exists

A number in a document is a claim about the tree, and a number is the easiest
kind of claim to go stale: add a test file, a screenshot, an invoice, a
catalogue key or a compose service, and the sentence that counted them keeps
saying the number it said last month.

The counts this file holds are the ones a reader is most likely to check
against the repository rather than take on trust, because they are the ones
that look like evidence:

- the line numbers ``docs/phase2-plan.md`` cites inside ``docker-compose.yml``
  for the three loopback port bindings — a reader who opens the file at the
  cited line must find the port;
- the number of holdout cases, and the number of files in ``evals/results/``,
  which together are the evidence for "the holdout has never been run";
- the i18n catalogue's string counts and namespace count;
- the number of ``web-e2e`` cases that cover internationalisation;
- the number of tests in the case study's scenario file, and how many of them
  assert against the billing server rather than against the run's own status.

Every one of these was wrong or nearly wrong when this file was written; see
the report that introduced it. None of them was caught by a guard, because
each is a number inside a sentence rather than a shape a machine can read.

## What this file asserts

The number in the document equals the number in the tree, **in both
directions**: the document is not allowed to quote a number the tree does not
have, and a document that stops quoting a number is not allowed to keep
quietly claiming the old one. The numbers are *read out of the documents*, not
hard-coded here, so that a document that is rewritten with a wrong number still
goes red.

## What it does not do

It does not check the numbers' meaning — only that they are current. It does
not touch ``docs/limitations.md`` §8, which has its own file
(``test_the_limitations_section_eight_claims_still_hold.py``).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The documents whose numbers are checked.
PHASE2 = REPO_ROOT / "docs" / "phase2-plan.md"
LIMITATIONS = REPO_ROOT / "docs" / "limitations.md"
CASE_STUDY = REPO_ROOT / "docs" / "case-study-duplicate-charge.md"

#: The tree the numbers are checked against.
COMPOSE = REPO_ROOT / "docker-compose.yml"
HOLDOUT = REPO_ROOT / "evals" / "datasets" / "tool_selection_holdout.jsonl"
EVAL_RESULTS = REPO_ROOT / "evals" / "results"
DICTIONARIES = REPO_ROOT / "web" / "src" / "i18n" / "dictionaries"
I18N_SPECS = (
    REPO_ROOT / "web" / "e2e" / "i18n.spec.ts",
    REPO_ROOT / "web" / "e2e" / "i18n-catalogue.spec.ts",
)
SCENARIOS = REPO_ROOT / "tests" / "agent" / "test_readme_scenarios.py"

#: English number words, for the sentences that spell their numbers out.
WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    30: "thirty",
    40: "forty",
}


def _text(path: pathlib.Path) -> str:
    """A document's text."""
    return path.read_text(encoding="utf-8")


def _row(document: str, key: str) -> str:
    """One row of a markdown table, named by its first cell's bold key.

    A row here can span several physical lines — the tables in these documents
    are written to a readable width and wrap — so the row runs until the next
    line that starts a new one.
    """
    lines = document.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(f"| **{key}** |")),
        None,
    )
    assert start is not None, f"no row for {key!r}"
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            break
        body.append(line)
    return "\n".join(body)


def _leaves(dictionary: dict[str, object]) -> int:
    """The number of string leaves in a nested catalogue."""
    total = 0
    for value in dictionary.values():
        if isinstance(value, dict):
            total += _leaves(value)
        else:
            total += 1
    return total


# ---------------------------------------------------------------------------
# docker-compose.yml — the three loopback ports and their cited lines
# ---------------------------------------------------------------------------


def test_the_phase2_port_citations_point_at_the_ports() -> None:
    """The A1 row's ``:line`` citations point at the ports it names.

    The row says the compose file publishes three ports, all on ``127.0.0.1``,
    and cites the line each one is on. A line number that has drifted is a
    citation a reader cannot follow, and the port bindings are the single
    P0 in the plan.
    """
    row = _row(_text(PHASE2), "A1")
    cited = [int(number) for number in re.findall(r":(\d+)", row)]
    assert len(cited) == 3, (
        f"the A1 row cites {len(cited)} compose lines ({cited}); it names three ports"
    )

    lines = _text(COMPOSE).splitlines()
    ports: list[str] = []
    for number in cited:
        assert number <= len(lines), f"docker-compose.yml has no line {number}"
        line = lines[number - 1]
        # The mapping's host side is `${OPSPILOT_*_HOST_PORT:-default}`: the
        # default is what the A1 row's port numbers name, and the `127.0.0.1`
        # prefix is the loopback binding the row asserts. A regex over the
        # whole line, rather than a field split, because the template's `:-`
        # adds colons a count would trip on.
        match = re.search(r"127\.0\.0\.1:\$\{[A-Z_]+:-(\d+)\}:(\d+)", line)
        assert match is not None, (
            f"docker-compose.yml:{number} is {line.strip()!r}, which is not a "
            "loopback port mapping with a host-port template; the A1 row's "
            "citation has drifted"
        )
        ports.append(match.group(1))

    assert sorted(ports) == ["3000", "5432", "8000"], (
        f"the cited lines publish {sorted(ports)}; the plan names 5432, 8000 and 3000"
    )

    # And the three loopback bindings really are the only published ports.
    published = re.findall(
        r"^\s*-\s*\"?([0-9.]+):\$\{[A-Z_]+:-(\d+)\}:(\d+)\"?\s*$", _text(COMPOSE), re.M
    )
    hosts = {host for host, _, _ in published}
    assert hosts == {"127.0.0.1"}, (
        f"the compose file publishes on {sorted(hosts)}; the plan's A1 row says "
        "all three are on 127.0.0.1"
    )


# ---------------------------------------------------------------------------
# evals/ — the holdout, and the results directory that shows it was never run
# ---------------------------------------------------------------------------


def test_the_phase2_holdout_counts_are_current() -> None:
    """The B4 row's case count is current, and its committed-run citation too.

    The row's *total* file count for ``evals/results/`` is deliberately not
    pinned here: the directory grows with every evaluation, so a guard over it
    would go red every time somebody ran one — a guard that cries wolf is a
    guard that gets switched off. What the row actually turns on is the
    holdout's case count and the identity of the run that closed B4's third
    clause.
    """
    row = _row(_text(PHASE2), "B4")

    cases = re.search(r"`([^`]+\.jsonl)`, (\d+) cases", row)
    assert cases is not None, "the B4 row no longer states the holdout's case count"
    path = REPO_ROOT / cases.group(1)
    actual = len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
    assert actual == int(cases.group(2)), (
        f"the holdout now holds {actual} cases; the B4 row says {cases.group(2)}"
    )

    # The third clause is closed by one named local run. The row must keep
    # naming it, and that run must exist and be the holdout's -- so the
    # "tuning never consults it" claim is pinned to a real, checkable file
    # rather than to a sentence.
    cited = re.search(r"producing `evals/results/([^`]+\.json)`", row)
    assert cited is not None, (
        "the B4 row no longer names the results file that closed its third clause"
    )
    cited_path = REPO_ROOT / "evals" / "results" / cited.group(1)
    # `evals/results/*` is gitignored by design (see evals/results/README.md),
    # so the file is a local artefact: the machine that ran the holdout has it,
    # a fresh clone (CI included) does not. What is universal is the *citation*:
    # the row must keep naming the run, and the name must be the holdout's.
    # Existence is checked only where the artefact actually lives, so the row
    # cannot quietly drop or rename its evidence without the machine that can
    # verify it going red.
    if not cited_path.exists():
        pytest.skip(
            f"{cited.group(1)} is not on this machine (evals/results/* is "
            "gitignored); the citation itself is checked unconditionally, the "
            "artefact only where the run was made"
        )
    cited_text = cited_path.read_text(encoding="utf-8")
    assert '"tool_selection_holdout"' in cited_text, (
        f"{cited.group(1)} is cited as the holdout run but does not name the dataset"
    )


def test_no_committed_result_file_touches_the_holdout() -> None:
    """The B4 row's load-bearing claim: tuning never consulted the holdout.

    The committed results are the ones a reader can check, so *those* must
    carry no holdout run. A local (gitignored) run of the holdout is the
    clause's own instrument -- it exists to be run once at the end -- and is
    asserted by :func:`test_the_phase2_holdout_counts_are_current` through the
    file the row cites; this guard keeps the *tracked* directory honest.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "evals/results/"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for name in tracked:
        if not name.endswith(".json"):
            continue
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "tool_selection_holdout" not in text, (
            f"{name} is committed and scores the holdout; the B4 row says tuning never consulted it"
        )


# ---------------------------------------------------------------------------
# web/src/i18n — the catalogues
# ---------------------------------------------------------------------------


def test_the_documented_catalogue_sizes_are_current() -> None:
    """The i18n string and namespace counts the documents quote are current."""
    english = json.loads((DICTIONARIES / "en.json").read_text(encoding="utf-8"))
    chinese = json.loads((DICTIONARIES / "zh-CN.json").read_text(encoding="utf-8"))

    phase2 = _text(PHASE2)
    quoted = re.search(r"`en` \((\d+) string leaves\) and `zh-CN` \((\d+)\)", phase2)
    assert quoted is not None, "the phase2-plan no longer states the two catalogues' leaf counts"
    assert _leaves(english) == int(quoted.group(1)), (
        f"the English catalogue now holds {_leaves(english)} string leaves; the "
        f"plan says {quoted.group(1)}"
    )
    assert _leaves(chinese) == int(quoted.group(2)), (
        f"the Chinese catalogue now holds {_leaves(chinese)} string leaves; the "
        f"plan says {quoted.group(2)}"
    )

    limitations = _text(LIMITATIONS)
    namespaces = re.search(
        r"a `zh-CN` catalogue of \*\*(\d+) strings\*\* across (\d+)", limitations
    )
    assert namespaces is not None, (
        "docs/limitations.md no longer states the Chinese catalogue's size"
    )
    assert _leaves(chinese) == int(namespaces.group(1)), (
        f"the Chinese catalogue now holds {_leaves(chinese)} strings; "
        f"limitations.md says {namespaces.group(1)}"
    )
    assert len(chinese) == int(namespaces.group(2)), (
        f"the Chinese catalogue now has {len(chinese)} namespaces; "
        f"limitations.md says {namespaces.group(2)}"
    )


def test_the_documented_i18n_e2e_case_count_is_current() -> None:
    """The number of ``web-e2e`` cases covering i18n is the number there are."""
    limitations = _text(LIMITATIONS)
    quoted = re.search(r"Covered by (\d+) `web-e2e` cases", limitations)
    assert quoted is not None, (
        "docs/limitations.md no longer states how many web-e2e cases cover i18n"
    )
    actual = 0
    for spec in I18N_SPECS:
        actual += len(re.findall(r'^\s*test\(\s*"', _text(spec), re.M))
    assert actual == int(quoted.group(1)), (
        f"the two i18n specs now hold {actual} test cases; limitations.md says {quoted.group(1)}"
    )


# ---------------------------------------------------------------------------
# tests/agent/test_readme_scenarios.py — the case study's two counts
# ---------------------------------------------------------------------------


def _scenario_tests() -> list[tuple[str, str]]:
    """The scenario file's test functions, with their source."""
    import ast

    source = _text(SCENARIOS)
    tree = ast.parse(source)
    return [
        (node.name, ast.get_source_segment(source, node) or "")
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def test_the_case_study_scenario_counts_are_current() -> None:
    """The case study's "seven of the nine" is the file's own arithmetic."""
    tests = _scenario_tests()
    against_server = [
        name
        for name, body in tests
        if any(
            marker in body
            for marker in (
                "refunded_transaction_ids",
                "refund_rows_in_store",
                "transactions_in_store",
            )
        )
    ]

    text = _text(CASE_STUDY)
    quoted = re.search(r"\*\*(\w+) of the (\w+) tests\*\*", text)
    assert quoted is not None, (
        "the case study no longer says how many of the scenario tests assert "
        "against the billing server"
    )
    assert quoted.group(2).lower() == WORDS[len(tests)], (
        f"the scenario file now holds {len(tests)} tests; the case study says {quoted.group(2)}"
    )
    assert quoted.group(1).lower() == WORDS[len(against_server)], (
        f"{len(against_server)} of the scenario tests now assert against the "
        f"billing server; the case study says {quoted.group(1)}"
    )

    whole = re.search(r"— (\w+) tests, one file", text)
    assert whole is not None, "the case study no longer says how many tests the scenario file holds"
    assert whole.group(1).lower() == WORDS[len(tests)], (
        f"the scenario file now holds {len(tests)} tests; the case study says {whole.group(1)}"
    )
