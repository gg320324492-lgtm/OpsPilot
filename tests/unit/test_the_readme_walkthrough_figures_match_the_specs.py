"""The README's browser-walkthrough figures must match the specs in the tree.

## Why this file exists

The README reports how many browser specs exist and how many of them are
Compose-backed -- the ones that bring up PostgreSQL, migrate, api and worker,
and click through the dashboard. Those are the specs that are the whole point
of the ``web-e2e`` job, and the number of them is a fact about the tree, not
about a run.

**They drifted.** The README said "twenty-two Compose-backed specs", a figure
that was true when it was written (commit ``75bd9b7``, when the tree had 67
specs of which 22 needed Compose) and became false as soon as a spec file
that starts a stack was added. The same paragraph also said "the CI run's 67
plus **nine** specs added since" while the table row under it said "**eighteen**
specs added since" -- and the prose's own arithmetic gave 76, not the 85 it
reported in the same sentence. The two numbers in one document disagreed, and
the prose disagreed with itself.

Neither error was visible to a reader: both are plausible-looking counts, and
nothing in the tree could be compared against them.

## What this file asserts

1. **The Compose-backed count matches the tree.** Every spec file under
   ``web/e2e/`` is counted, and a file counts as Compose-backed when it calls
   ``startStackOrSkip`` (the full stack) or ``startContainerisedWebOrSkip``
   (the containerised dashboard). The README's number must equal the tree's.
2. **The breakdown matches too.** The README names the two subsets
   separately; each must match its own count.
3. **Every mention of the count agrees.** The prose and the table both state
   it, and a third mention must not be free to disagree with them.
4. **The prose's arithmetic is right.** "the CI run's 67 plus eighteen specs
   added since" must add up to the figure reported alongside it.

## What it does not do

It does not check the *measured* figures -- "85 passed, 0 skipped" is a run on
a dated commit, and the tree has moved on since. What it checks is that the
run's own arithmetic is consistent and that the counts that describe the tree
still describe the tree.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
E2E = REPO_ROOT / "web" / "e2e"
README = REPO_ROOT / "README.md"

#: The spellings the README uses for numbers.
_ONES = {
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
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


def _words_to_int(text: str) -> int:
    """``"twenty-three"`` -> ``23``.

    The README spells its small counts out, so the guard has to read them the
    same way a reader does. Anything it cannot read is an error, not a zero.
    """
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        return int(cleaned)
    total = 0
    for part in cleaned.split("-"):
        if part in _ONES:
            total += _ONES[part]
        elif part in _TENS:
            total += _TENS[part]
        else:
            raise AssertionError(f"cannot read the number {text!r}")
    return total


def _readme_text() -> str:
    """The README with its blockquote markers removed, as one line."""
    lines = []
    for line in README.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ")
        lines.append(stripped)
    return " ".join(" ".join(lines).split())


def _spec_files() -> list[pathlib.Path]:
    return sorted(E2E.glob("*.spec.ts"))


def _spec_count(path: pathlib.Path) -> int:
    """How many tests a spec file declares."""
    return len(re.findall(r"^\s*test\(", path.read_text(encoding="utf-8"), re.M))


def _counts() -> tuple[int, int, int]:
    """``(total, full-stack, containerised)`` spec counts in the tree."""
    total = full_stack = containerised = 0
    for path in _spec_files():
        source = path.read_text(encoding="utf-8")
        count = _spec_count(path)
        total += count
        if "startStackOrSkip(" in source:
            full_stack += count
        elif "startContainerisedWebOrSkip(" in source:
            containerised += count
    return total, full_stack, containerised


def _claimed_compose_counts(text: str) -> list[tuple[str, int]]:
    """Every "N Compose-backed specs" the README states."""
    out: list[tuple[str, int]] = []
    for match in re.finditer(r"(\S+) Compose-backed specs", text):
        out.append((match.group(0), _words_to_int(match.group(1))))
    return out


def test_the_compose_backed_count_matches_the_tree() -> None:
    """The README's Compose-backed count is the tree's."""
    text = _readme_text()
    _, full_stack, containerised = _counts()

    assert full_stack > 0, "no spec starts the full stack; the counting rule has stopped matching"
    assert containerised > 0, "no spec starts the containerised dashboard"

    claims = _claimed_compose_counts(text)
    assert claims, "the README no longer states how many Compose-backed specs there are"

    wrong = [
        f"{phrase!r} says {count}, the tree has {full_stack + containerised}"
        for phrase, count in claims
        if count != full_stack + containerised
    ]
    assert not wrong, (
        "the README's Compose-backed spec count does not match the tree:\n  "
        + "\n  ".join(wrong)
        + f"\n\nThe tree has {full_stack} specs that start the full stack "
        f"(postgres, migrate, api, worker) and {containerised} that start the "
        "containerised dashboard."
    )


def test_every_mention_of_the_compose_backed_count_agrees() -> None:
    """The prose and the table do not state two different numbers."""
    text = _readme_text()
    claims = _claimed_compose_counts(text)
    assert len(claims) >= 2, (
        f"the README states the count {len(claims)} time(s); expected the prose and "
        "the table to both state it"
    )
    numbers = {count for _, count in claims}
    assert len(numbers) == 1, (
        "the README states the Compose-backed count differently in different places: "
        + ", ".join(f"{phrase!r} = {count}" for phrase, count in claims)
    )


def test_the_breakdown_matches_the_tree() -> None:
    """The README's split of the count matches the tree's split."""
    text = _readme_text()
    _, full_stack, containerised = _counts()

    full = re.search(r"(\S+) that bring up PostgreSQL", text)
    web = re.search(r"(\S+) that bring up the containerised", text)
    assert full is not None, "the README no longer breaks the count down"
    assert web is not None, "the README no longer breaks the count down"

    assert _words_to_int(full.group(1)) == full_stack, (
        f"the README says {full.group(1)} specs start PostgreSQL, migrate, api and "
        f"worker; {full_stack} do"
    )
    assert _words_to_int(web.group(1)) == containerised, (
        f"the README says {web.group(1)} specs start the containerised dashboard; "
        f"{containerised} do"
    )


def test_the_walkthrough_arithmetic_is_right() -> None:
    """ "the CI run's N plus M specs added since" adds up to the figure beside it."""
    text = _readme_text()

    run = re.search(r"the CI run's (\d+) plus (\S+) specs added since", text)
    assert run is not None, "the README no longer states the walkthrough's arithmetic"

    base = int(run.group(1))
    added = _words_to_int(run.group(2))
    assert added > 0, "the README claims no specs were added since the CI run"

    passed = re.search(r"\*\*(\d+) passed, 0 skipped\*\*", text)
    assert passed is not None, "the README no longer states the walkthrough's result"

    total = int(passed.group(1))
    assert base + added == total, (
        f"the README says the CI run's {base} plus {added} specs added since, which is "
        f"{base + added}, but it reports {total} passed"
    )


def test_the_guard_reads_the_specs_and_the_readme() -> None:
    """Neither side of the comparison came back empty."""
    total, full_stack, containerised = _counts()
    assert total >= 60, f"only {total} browser specs were counted; the pattern has changed"
    assert full_stack + containerised < total, (
        "every spec counts as Compose-backed, which is not what the tree looks like; "
        "the counting rule has probably stopped discriminating"
    )
    assert _claimed_compose_counts(_readme_text())
