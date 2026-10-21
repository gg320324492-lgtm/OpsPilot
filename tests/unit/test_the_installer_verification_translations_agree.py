"""The two installer walk-through documents must agree, structurally.

## Why this file exists

``docs/installer-verification.md`` says of its Chinese sibling: "**This file
is authoritative; where they disagree, this one wins.**" That is a rule, and
a rule nothing checked.

The Chinese document had **two whole sections the English one did not have**:
a list of the literals that must not change when the document is translated
(the ports, the volume names, the artefact's name, the HTTP statuses -- the
things a reader compares against what is on their screen), and a record sheet
for the person doing the walk-through, one checkbox per step. A reader given
only the English document -- the authoritative one -- got neither: not the list
of literals that must survive translation, and not a sheet to fill in.

The English document now has both, and this file is what holds the two
together afterwards. It checks the **literal list** (the backticked tokens,
which must be identical -- a translated `8000` is still `8000`, and a
translated `localhost` would not be) and the **shape of the record sheet**
(the same number of steps, so a step cannot be added to one and forgotten in
the other).

## What it does not do

It does not compare the prose, and it does not translate anything. The
wording of the two documents is allowed to differ -- that is the point of a
translation -- and what has to match is the set of things a reader compares
against a screen.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGLISH = REPO_ROOT / "docs" / "installer-verification.md"
CHINESE = REPO_ROOT / "docs" / "installer-verification.zh-CN.md"

#: The heading of the section that lists the literals, in each language.
LITERAL_HEADINGS = {
    ENGLISH: "Literals that must not change",
    CHINESE: "必须原样保留的字面值",
}

#: The heading of the record sheet, in each language.
SHEET_HEADINGS = {
    ENGLISH: "A record sheet for the person doing it",
    CHINESE: "给执行者的记录表（建议填写）",  # noqa: RUF001 -- the heading is Chinese
}

#: A backticked token.
_BACKTICKED = re.compile(r"`([^`]+)`")

#: A checkbox.
_CHECKBOX = "☐"


def _section(path: pathlib.Path, heading: str) -> list[str]:
    """The lines of one ``##`` section, and the lines of the next one."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(f"## {heading}")),
        None,
    )
    assert start is not None, f"{path.name} has no section {heading!r}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _literals(path: pathlib.Path) -> list[str]:
    """The backticked tokens of the literals section."""
    body = _section(path, LITERAL_HEADINGS[path])
    found: list[str] = []
    for line in body:
        found.extend(_BACKTICKED.findall(line))
    return found


def test_both_documents_carry_the_literals_section() -> None:
    """Neither document may lose the list that the other has.

    The English one is the authoritative text, so a reader who has only it
    must still get the list -- and the Chinese one is the one a reader is
    most likely to be holding.
    """
    english = _literals(ENGLISH)
    chinese = _literals(CHINESE)
    assert len(english) >= 30, (
        f"the English literals list holds {len(english)} tokens; it was the "
        "list of the things a reader compares against their screen"
    )
    assert len(chinese) >= 30, (
        f"the Chinese literals list holds {len(chinese)} tokens; the two "
        "documents must both carry the list"
    )


def test_the_two_literal_lists_are_the_same_literals() -> None:
    """A translated literal is still the literal.

    The ports, the volume names, the artefact's name and the HTTP statuses
    are compared character for character against the screen, so a translation
    that renders `8000` as `port 8000`, or drops `--tail 100`, has changed
    the thing the reader is told to look for.
    """
    english = set(_literals(ENGLISH))
    chinese = set(_literals(CHINESE))
    assert english == chinese, (
        "the two literal lists differ, so a reader of one document is looking "
        f"for something the other does not name: only in English "
        f"{sorted(english - chinese)}, only in Chinese {sorted(chinese - english)}"
    )


def test_the_two_record_sheets_have_the_same_number_of_steps() -> None:
    """A step added to one document and forgotten in the other.

    The sheet is what the person doing the walk-through fills in, and a step
    that exists in only one of the two documents is a step one of the two
    readers will not perform.
    """
    english = _section(ENGLISH, SHEET_HEADINGS[ENGLISH])
    chinese = _section(CHINESE, SHEET_HEADINGS[CHINESE])
    english_boxes = sum(line.count(_CHECKBOX) for line in english)
    chinese_boxes = sum(line.count(_CHECKBOX) for line in chinese)
    assert english_boxes > 0, "the English record sheet has no checkboxes"
    assert english_boxes == chinese_boxes, (
        f"the English record sheet has {english_boxes} checkboxes and the "
        f"Chinese one {chinese_boxes}; a step in one document and not the "
        "other is a step one of the two readers will not perform"
    )
