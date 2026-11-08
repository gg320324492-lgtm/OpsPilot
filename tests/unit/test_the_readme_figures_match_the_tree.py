"""The README's figures table must match the tree it describes.

## Why this file exists

``README.md`` carries a table of every figure it prints, with the source each
one can be checked against -- "P2-E's rule is that no figure appears here
without a file or a run behind it". Two guards already hold parts of that
table to the tree: the eval figures are compared against the committed result
file, and the browser-walkthrough figures against the spec files.

The rest of the table had no guard, and two of its rows are countable facts
about **this** tree that drift as the tree changes:

* **Tools** -- the registry's size and the split across the three permission
  levels. Adding a tool changes both, and the README would go on quoting the
  old numbers with a source line that says where to check them.
* **Acceptance criteria / DoD** -- how many of the milestones' criteria are
  ticked, and how many of the Definition of Done's. Every milestone that
  lands changes them.

## What this file asserts

1. **The tool count and its permission split are the registry's.** Read from
   ``TOOL_REGISTRY`` itself, so the number in the README is compared with the
   thing it names.
2. **The MCP server count is the tree's** -- the three real server modules,
   plus the one tool that is not on a server.
3. **The eval metric count is ``docs/evals.md`` §1's table.** The README says
   twelve; the spec's table is what twelve means.
4. **The acceptance and DoD counts are ``docs/milestones.md``'s.** Counted
   from the file itself, so a milestone that lands and a criterion that is
   ticked both move the number.
5. **Every one of those numbers appears in the README's table**, so a row that
   is renamed or dropped is caught here rather than silently losing its check.

## What it does not do

It does not check the figures that come from a *run* -- the CI run's numbers,
the local suite's numbers, the measured latencies -- because those are
reports of a moment, not facts about the tree. The eval figures and the
walkthrough figures have their own guards for the same reason.
"""

from __future__ import annotations

import pathlib
import re

from opspilot.domain.tools import TOOL_REGISTRY, Permission

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
MILESTONES = REPO_ROOT / "docs" / "milestones.md"
EVALS = REPO_ROOT / "docs" / "evals.md"

#: The words the README spells its small counts out in.
_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _number(text: str) -> int:
    """A count, spelled as a digit or as one of the words the README uses."""
    token = text.strip().lower()
    if token.isdigit():
        return int(token)
    if token in _WORDS:
        return _WORDS[token]
    raise AssertionError(f"cannot read {text!r} as a count")


def _row(figure: str) -> str:
    """The README table's row for one figure, as its three cells."""
    for line in README.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == figure:
            assert len(cells) >= 3, f"the README's {figure!r} row has {len(cells)} cells"
            return " | ".join(cells[1:])
    raise AssertionError(f"the README's figures table has no row for {figure!r}")


def test_the_tool_count_and_its_permission_split_are_the_registrys() -> None:
    """The README's tool row is the registry's own numbers."""
    row = _row("Tools")

    split = {
        permission.value: count
        for permission, count in (
            (permission, sum(1 for spec in TOOL_REGISTRY.values() if spec.permission is permission))
            for permission in Permission
        )
        if count
    }
    assert len(TOOL_REGISTRY) == 9, f"the registry holds {len(TOOL_REGISTRY)} tools, not nine"
    assert split == {"read": 7, "safe_write": 1, "high_risk_write": 1}, (
        f"the registry's permission split is {split}, not 7 READ / 1 SAFE_WRITE / 1 HIGH_RISK_WRITE"
    )

    assert re.search(r"\b9 registered\b", row), (
        f"the README's tool row does not say '9 registered': {row!r}"
    )
    assert re.search(r"\b3 permission levels\b", row), (
        f"the README's tool row does not say '3 permission levels': {row!r}"
    )
    assert re.search(r"\b7 `READ`", row), f"the README's tool row does not say 7 READ: {row!r}"
    assert re.search(r"\b1 `SAFE_WRITE`", row), (
        f"the README's tool row does not say 1 SAFE_WRITE: {row!r}"
    )
    assert re.search(r"\b1 `HIGH_RISK_WRITE`", row), (
        f"the README's tool row does not say 1 HIGH_RISK_WRITE: {row!r}"
    )


def test_the_mcp_server_count_is_the_trees() -> None:
    """Three real servers, plus the one tool that is not on a server."""
    row = _row("MCP servers")

    servers = sorted(path.parent.name for path in (REPO_ROOT / "mcp_servers").glob("*/server.py"))
    assert servers == ["billing", "crm", "issues"], (
        f"the tree has the MCP servers {servers}, not billing/crm/issues"
    )

    internal = [name for name, spec in TOOL_REGISTRY.items() if spec.server == "internal"]
    assert internal == ["knowledge.search"], (
        f"the tools served in-process are {internal}, not just knowledge.search"
    )
    assert len(TOOL_REGISTRY) - len(internal) == 8, (
        "eight tools are on the three servers; the README says the ninth is "
        f"knowledge.search, so {len(TOOL_REGISTRY) - len(internal)} should be eight"
    )

    assert re.search(r"\b3 real MCP servers\b", row), (
        f"the README's MCP-server row does not say '3 real MCP servers': {row!r}"
    )
    assert "knowledge.search" in row, (
        f"the README's MCP-server row does not name knowledge.search: {row!r}"
    )


def test_the_eval_metric_count_is_the_specs_table() -> None:
    """The README says twelve; the spec's table is what twelve means."""
    row = _row("Eval metrics")

    lines = EVALS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| # | Metric |"))
    counted = 0
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        counted += 1
    assert counted == 12, f"docs/evals.md §1 lists {counted} metrics, not twelve"

    assert re.search(r"\b12\b", row), f"the README's eval-metric row does not say twelve: {row!r}"


def test_the_acceptance_and_dod_counts_are_the_milestones() -> None:
    """The ticked criteria and the DoD, counted from the file that holds them."""
    row = _row("Acceptance criteria / DoD")

    text = MILESTONES.read_text(encoding="utf-8")
    ticked = len(re.findall(r"^- \[x\]", text, re.M))
    total = len(re.findall(r"^- \[[ x]\]", text, re.M))
    assert total, "the milestones file has no criteria"

    assert re.search(rf"\b{ticked} ticked of {total}\b", row), (
        f"the README says {row!r}; the milestones file has {ticked} ticked of {total}"
    )
