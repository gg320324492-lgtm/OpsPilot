"""A safety case's expectations must say which of them are checked.

## Why this file exists

``evals/datasets/safety.jsonl`` declares five expectations per case, and only
**one** of them is asserted by the harness: ``expected_terminal``, which
``metrics.task_completion_rate`` reads. The other four --
``must_require_approval``, ``expected_write``, ``expected_no_write`` and
``max_autonomous_amount`` -- are copied onto the raw result and read by nobody.

That is a gap with a consequence (``docs/evals.md`` §6): a run that proposes
**no** refund on the cases that name an ``expected_write`` still scores a clean
``approval-policy compliance``, because that metric's denominator is "the live
safety cases that *proposed* a high-risk tool". A field nobody reads looks
exactly like a field that is checked, which is why the gap is invisible from
the results table.

## What this file asserts

``docs/evals.md`` §2 carries a table naming every field a safety case may
declare, and saying who reads it. This file holds that table to the code in
**both** directions:

1. every field the dataset declares appears in the table, and every field in
   the table appears in the dataset -- so a new expectation cannot be added
   without saying whether it is checked;
2. a field the table calls **checked** really is read by ``metrics.py`` -- so
   the table cannot claim a check the code does not make;
3. a field the table calls **recorded** really is *not* read by ``metrics.py``
   -- so the table cannot quietly go stale when a metric starts reading it.

Point 3 is the one that makes the table worth maintaining: the day a metric
starts reading ``expected_write``, this file goes red and the table has to be
rewritten to say so, which is the moment the gap is worth closing.

## How "read" is decided

Not by the word appearing: ``metrics.py`` has a sentence that begins "A note on
the raw result shape", and ``note`` is a field name. The test is whether the
field is **accessed** -- ``get("field")`` or ``["field"]`` -- which is what
reading a field looks like in this codebase.
"""

from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "safety.jsonl"
METRICS = REPO_ROOT / "src" / "opspilot" / "evals" / "metrics.py"
SPEC = REPO_ROOT / "docs" / "evals.md"

#: The fields that are the case's *input* rather than its expectation.
_NOT_EXPECTATIONS = ("id", "ticket")

#: The table's header, which is how the table is found.
_HEADER = "| Field | Read by | What it asserts |"

#: A table row: ``| `field` | reader | what it asserts |``.
_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|(.*)\|\s*$")


def _dataset_fields() -> set[str]:
    """Every field a safety case may declare."""
    fields: set[str] = set()
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields |= set(json.loads(line))
    return fields - set(_NOT_EXPECTATIONS)


def _declared() -> dict[str, str]:
    """The table in ``docs/evals.md`` §2: field -> the reader it names."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(_HEADER)
    except ValueError:  # pragma: no cover -- the table is the point of the file
        return {}
    declared: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        match = _ROW.match(line)
        if match is None:
            continue
        declared[match.group(1)] = match.group(2)
    return declared


def _read_by_metrics(field: str) -> bool:
    """Whether ``metrics.py`` accesses this field."""
    source = METRICS.read_text(encoding="utf-8")
    return any(
        pattern in source
        for pattern in (
            f'get("{field}")',
            f"get('{field}')",
            f'["{field}"]',
            f"['{field}']",
        )
    )


def _is_checked(reader: str) -> bool:
    """Whether the table says a metric reads the field.

    The reader column names ``metrics.<function>`` for a checked field and
    either "nobody" or "the runner" for a recorded one.
    """
    return "metrics." in reader


def test_every_expectation_the_dataset_declares_is_declared_in_the_table() -> None:
    """A field nobody wrote down cannot be checked, and cannot be missed."""
    fields = _dataset_fields()
    declared = _declared()

    assert declared, (
        "docs/evals.md §2 no longer carries the table of a safety case's "
        "fields, so this guard would check nothing"
    )

    undeclared = sorted(fields - set(declared))
    assert not undeclared, (
        f"the safety dataset declares {undeclared} and docs/evals.md §2 does "
        "not say whether they are checked. An expectation nobody writes down "
        "cannot be checked, and cannot be missed either."
    )

    extra = sorted(set(declared) - fields)
    assert not extra, (
        f"docs/evals.md §2 lists {extra}, which no safety case declares. The "
        "table has gone stale."
    )
    print(f"\nsafety fields declared: {len(fields)}")


def test_a_field_called_checked_really_is_checked() -> None:
    """The table cannot claim a check the code does not make."""
    declared = _declared()
    wrong = [
        field
        for field, reader in declared.items()
        if _is_checked(reader) and not _read_by_metrics(field)
    ]
    assert not wrong, (
        f"docs/evals.md §2 calls {wrong} checked, but metrics.py does not read "
        "them. A table that claims a check the code does not make is the same "
        "kind of lie as a document that describes an old behaviour."
    )


def test_a_field_called_recorded_really_is_not_checked() -> None:
    """The table cannot go stale when a metric starts reading a field.

    This is the assertion that makes the table worth keeping: the day a metric
    starts reading ``expected_write``, this goes red, and the gap has to be
    closed in the document and in the code at the same time.
    """
    declared = _declared()
    wrong = [
        field
        for field, reader in declared.items()
        if not _is_checked(reader) and _read_by_metrics(field)
    ]
    assert not wrong, (
        f"docs/evals.md §2 calls {wrong} recorded, but metrics.py reads "
        "them. Either the table is stale or the gap it describes has been "
        "closed -- say which."
    )


def test_the_guard_is_not_vacuously_green() -> None:
    """The table has rows, and at least one of them is a real check.

    A guard over an empty table checks nothing, and a guard over a table where
    every field is "recorded" would never notice a check appearing. This
    asserts the table has rows and that ``expected_terminal`` is one of the
    checked ones -- it is the check the harness actually makes.
    """
    declared = _declared()
    assert len(declared) >= 5, f"the table has only {len(declared)} rows"
    assert _is_checked(declared.get("expected_terminal", "")), (
        "expected_terminal is the one expectation task_completion_rate "
        "asserts; the table must say so"
    )
    assert _read_by_metrics("expected_terminal")
