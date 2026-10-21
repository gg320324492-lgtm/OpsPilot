"""A document's cited test names must name a test that exists.

## Why this file exists

The documents cite tests by name -- `` `test_retrieve_abstains_below_threshold` ``
-- and a name that no longer names anything is a pointer a reader cannot
follow. It happens whenever a test is renamed, and the tree has no guard for
it.

The four mutation-verification tables in ``docs/progress.md`` are where it
bit. Those tables say "each break was applied, the named test(s) confirmed
red, then reverted" -- a claim that is only meaningful if the named tests can
be found and re-run. Four of the names in them had been renamed:

* ``test_server_registers_the_tool_under_the_contracted_name`` is now
  ``test_the_tool_name_is_registered_as_read_internal``;
* ``test_abstention_is_a_successful_result_carrying_the_flag`` is now
  ``test_abstention_is_a_success_carrying_the_flag``;
* ``test_gateway_dispatch_passes_query_and_top_k`` is now
  ``test_arguments_reach_the_retriever_with_a_bounded_top_k``;
* ``test_retrieve_default_min_score_is_0_35`` is now
  ``test_retrieve_default_min_score_matches_the_configured_setting``.

A reader who wanted to re-run the verification would have found nothing at
four of the table's rows, and would have had no way to tell whether the
verification had never been done or whether the tests had merely moved.

**The repository already has the convention that makes this fixable.** A
retired name in these documents is *marked* as retired -- "this paragraph used
to name X", "it has been renamed to Y", "no such test exists". That marking is
the difference between a historical record and a broken pointer, and it is
what this guard checks.

## What this file asserts

1. **A cited test name resolves.** A bare `` `test_*` `` name in a document
   must be a defined test function, a test module, or a **unique prefix** of
   exactly one defined test function. The prefix rule is what lets
   ``docs/milestones.md`` write `` `test_scenario_1` `` … `` `test_scenario_5` ``
   the way the README's scenario list does.
2. **A cited test file exists.** A `` `test_*.py` `` citation must be a file
   in the tree.
3. **An unresolved name is marked as historical.** If a name does not
   resolve, the paragraph that cites it must say so -- "used to", "renamed",
   "no longer", "does not exist", "was pinning". Otherwise it is drift.
4. **That the guard is not vacuous.** A name that cannot exist is flagged, a
   name that can is not, and the documents really are being read.

## What it does not do

It does not check that the cited test is the *right* test for the claim, or
that it still guards what the document says it guards. That is a question of
meaning; the guard checks the mechanical half -- the thing cited is there.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A bare test name in backticks. The backticks must wrap the name and
#: nothing else, so `` `tests/agent/test_readme_scenarios.py` `` and
#: `` `foo.py::test_bar` `` are not matched -- those are checked elsewhere.
_TEST_NAME = re.compile(r"`(test_[A-Za-z0-9_]+)`")

#: A cited test module by file name.
_TEST_FILE = re.compile(r"`(test_[A-Za-z0-9_]*\.py)`")

#: Markers that license an unresolved name: the paragraph says the name is
#: retired rather than asserting it as current.
_HISTORICAL = re.compile(
    r"(used to|renamed|no longer|does not exist|did not exist|never existed"
    r"|no such test|was pinning)",
    re.IGNORECASE,
)


def _documents() -> list[pathlib.Path]:
    """Every document that cites tests."""
    return [
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        REPO_ROOT / "README.md",
        REPO_ROOT / "desktop" / "README.md",
        REPO_ROOT / "web" / "README.md",
    ]


def _defined_test_names() -> set[str]:
    """Every test function and test module the tree defines."""
    names: set[str] = set()
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"^\s*(?:async\s+)?def (test_[A-Za-z0-9_]+)", source, re.M):
            names.add(match.group(1))
        if path.name.startswith("test_"):
            names.add(path.stem)
    return names


def _test_files() -> set[str]:
    """Every test file in the tree, by name."""
    return {path.name for path in (REPO_ROOT / "tests").rglob("*.py")}


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """The document's paragraphs, each with the line it starts on."""
    out: list[tuple[int, str]] = []
    line = 1
    for chunk in re.split(r"\n\s*\n", text):
        out.append((line, chunk))
        line += chunk.count("\n") + 2
    return out


def _resolves(name: str, defined: set[str]) -> bool:
    """Whether a cited name names something in the tree.

    A **unique prefix** counts. ``test_scenario_1`` is not a test, but it is
    unambiguous -- exactly one defined test starts with it -- and the
    documents use that shorthand to name a range of tests.
    """
    if name in defined:
        return True
    return sum(1 for candidate in defined if candidate.startswith(name)) == 1


def _citations() -> list[tuple[pathlib.Path, int, str, str, bool]]:
    """Every cited test name, with its line, paragraph and whether it resolved."""
    defined = _defined_test_names()
    out: list[tuple[pathlib.Path, int, str, str, bool]] = []
    for path in _documents():
        text = path.read_text(encoding="utf-8")
        for start, paragraph in _paragraphs(text):
            for match in _TEST_NAME.finditer(paragraph):
                name = match.group(1)
                line = start + paragraph[: match.start()].count("\n")
                out.append((path, line, name, paragraph, _resolves(name, defined)))
    return out


def test_the_documents_only_cite_test_names_that_exist() -> None:
    """Every cited test name resolves, or its paragraph marks it as retired."""
    broken: list[str] = []
    for path, line, name, paragraph, resolved in _citations():
        if resolved:
            continue
        if _HISTORICAL.search(paragraph):
            # The paragraph says the name is retired. That is the house
            # convention, and it is what makes a historical record safe to
            # leave in the tree.
            continue
        where = path.relative_to(REPO_ROOT).as_posix()
        broken.append(f"{where}:{line}  {name}")
    assert not broken, (
        "these documents cite test names that do not exist, and do not mark them "
        "as retired:\n  "
        + "\n  ".join(broken)
        + "\n\nEither the test was renamed -- cite its current name -- or it was "
        "removed, in which case say so in the same paragraph ('this used to name "
        "X', 'it has been renamed to Y'). A name that resolves to nothing and "
        "claims to be current is a promise the reader cannot check."
    )


def test_the_cited_test_files_exist() -> None:
    """A cited ``test_*.py`` is a file in the tree."""
    files = _test_files()
    missing: list[str] = []
    for path in _documents():
        text = path.read_text(encoding="utf-8")
        for match in _TEST_FILE.finditer(text):
            name = match.group(1)
            if name in files:
                continue
            line = text[: match.start()].count("\n") + 1
            where = path.relative_to(REPO_ROOT).as_posix()
            missing.append(f"{where}:{line}  {name}")
    assert not missing, (
        "these documents cite test files that do not exist:\n  " + "\n  ".join(missing)
    )


def test_the_guard_would_notice_a_name_that_cannot_exist() -> None:
    """The guard is not vacuously green.

    A guard that matches nothing checks nothing, and this one is built out of
    a regex and a prefix rule -- both of which can silently stop matching.
    These three assertions exercise the parts: a name that cannot exist is
    flagged, a name that can is not, and a paragraph that marks a retired
    name as retired is accepted.
    """
    defined = _defined_test_names()

    assert not _resolves("test_a_name_that_does_not_exist_anywhere", defined), (
        "a name that cannot exist was reported as resolving"
    )
    assert _resolves("test_the_tool_name_is_registered_as_read_internal", defined), (
        "a real test name was reported as not resolving"
    )

    retired = (
        "This paragraph used to name `test_a_name_that_does_not_exist_anywhere`, "
        "which has since been renamed."
    )
    assert _HISTORICAL.search(retired), "the historical marker did not match its own example"
    assert not _HISTORICAL.search(
        "The corpus holds `test_a_name_that_does_not_exist_anywhere` today."
    ), "a current-tense sentence was read as a historical one"


def test_the_guard_actually_reads_the_documents() -> None:
    """The documents are scanned, and the citation pattern fires on them."""
    citations = _citations()
    assert len(citations) >= 50, (
        f"only {len(citations)} test-name citations were found in the tree; the "
        "pattern has probably stopped matching how the documents write them"
    )

    documents = _documents()
    assert len(documents) >= 10, f"only {len(documents)} documents are scanned"

    unresolved = [name for _, _, name, _, resolved in citations if not resolved]
    assert unresolved, (
        "every cited test name in the tree resolves. The documents now cite only "
        "current names, so this guard has nothing to check -- and a guard with "
        "nothing to check should be deleted rather than left looking green."
    )
