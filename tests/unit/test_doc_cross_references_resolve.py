"""A document's cross-references must resolve.

## Why this file exists

The documents are this repository's evidence: they are what a reader checks a
claim against. A cross-reference that points at nothing is a claim whose
support cannot be found, and it is the cheapest kind of drift to fix and the
easiest to miss.

There was one such reference in the tree at the moment of writing:
``docs/progress.md`` cited ``docs/mcp-contracts.md`` **section S2** three
times, and that document has no such section — its second section is
``## 2. `billing` server``. The "S" was a leftover from a numbering the
document no longer used, and a reader following it found nothing.

**The same broken reference survived in the source.** A guard that reads only
the documents cannot see a "section S2" written into a docstring in
``src/opspilot/domain/tools.py`` or into a comment in ``tests/`` — and four
copies of it did survive there, after the documents themselves had been
cleaned. So the section rule is now checked against the **source trees** as
well, and this file is scanned by it like any other.

## What this file asserts

1. **Every markdown link's file target exists**, relative to the repository
   root or to the document's own directory.
2. **Every markdown link's anchor exists** in the file it points at, compared
   against the headings' slugs the way GitHub slugs them.
3. **Every ``§`` reference that looks like a section id resolves** to a
   section some document offers -- in the documents **and in the source**.

**The source is scanned too, and the range form is allowed.** A reference
like ``§3-4`` names a *range* of sections rather than one key, and the
repository uses it: ``docs/architecture.md §3-4`` is a real citation in
``src/opspilot/ports/__init__.py``. The rule is that both endpoints resolve.
A reference to a section id of ``S2`` names one key that does not exist
anywhere, and that is the defect this file exists for.

## The rule about "looks like a section id"

``§`` is used in these documents both as a section reference and as a prose
"see" — "the detail is in § The 170 calls below" is prose, and a guard that
checked it would be guessing. The discriminator is the **shape** of a section
id in this repository: every one of them contains a digit or a hyphen (``2``,
``3.1``, ``M4``, ``P2-D1``). Prose does not. So a reference is checked when
it contains a digit or a hyphen, and counted-but-not-checked when it does not.

A document offers a section key when one of its headings carries one: ``## 2.
Title`` offers "2", ``## 3.1 Title`` offers "3.1", ``## M4 — Title`` offers
"M4", ``## P2-A · Title`` offers "P2-A". A **row** of a table whose first
cell is an identifier offers that identifier too, and a section's prefix with
a row's identifier makes the composite: section "P2-A" and row "A1" give
"§P2-A1", which is how the Phase 2 plan is cited.

## What it does not do

It does not check that a reference points at the *right* section — that a
question of meaning, and a guard that guessed would misfire. It checks that
the thing referenced exists, which is the half that is mechanical.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A markdown link: ``[text](target)``.
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: A section reference: ``§2``, ``§3.1``, ``§M4``, ``§P2-D1``.
_SECTION = re.compile(r"§\s?([A-Za-z0-9][A-Za-z0-9.\-]*)")

#: A heading, and the keys a heading offers.
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_NUMBERED = re.compile(r"^(?:§\s?)?([0-9]+(?:\.[0-9]+)*)\.?\s+(.*)$")
_NAMED = re.compile(r"^(?:§\s?)?([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*(?:—|--|:|·)\s*(.*)$")

#: A table row whose first cell is a short identifier: ``| A1 | ... |``.
_ROW_ID = re.compile(r"^\|\s*([A-Za-z0-9][A-Za-z0-9\-]{0,7})\s*\|")


def _documents() -> list[pathlib.Path]:
    """Every markdown file the documents live in."""
    return [
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        REPO_ROOT / "README.md",
        REPO_ROOT / "desktop" / "README.md",
        REPO_ROOT / "web" / "README.md",
    ]


def _source_files() -> list[pathlib.Path]:
    """Every source file a section reference can live in.

    The trees are named rather than globbed over the whole repository on
    purpose: ``web/node_modules``, ``desktop/dist`` and ``desktop/stage`` are
    not source, and scanning them would find the *copies* of the sources that
    the build makes -- copies of a stale file, which is a different defect.
    """
    patterns = (
        "src/**/*.py",
        "tests/**/*.py",
        "mcp_servers/**/*.py",
        "scripts/*.py",
        "scripts/*.mjs",
        "web/src/**/*.ts",
        "web/src/**/*.tsx",
        "web/e2e/*.ts",
        "web/scripts/*.mjs",
        "desktop/src/**/*.ts",
        "desktop/e2e/*.ts",
        "desktop/scripts/*.mjs",
    )
    files: list[pathlib.Path] = []
    for pattern in patterns:
        files.extend(path for path in REPO_ROOT.glob(pattern) if path.is_file())
    return sorted(set(files))


#: A section *range*: ``3-4``, ``2.1-2.3``.
_RANGE = re.compile(r"^(\d+(?:\.\d+)*)-(\d+(?:\.\d+)*)$")


def _section_resolves(key: str, known: set[str]) -> bool:
    """Whether a section reference names something a document offers.

    A range resolves when *both* of its endpoints do; a single key resolves
    when the key set has it.
    """
    if key in known:
        return True
    endpoints = _RANGE.match(key)
    if endpoints is None:
        return False
    return endpoints.group(1) in known and endpoints.group(2) in known


def _slug(title: str) -> str:
    """A heading's slug, the way GitHub writes it."""
    text = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")


def _keys_of(text: str) -> set[str]:
    """The section keys a document offers."""
    keys: set[str] = set()
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading is not None:
            title = heading.group(2).strip()
            numbered = _NUMBERED.match(title)
            if numbered is not None:
                keys.add(numbered.group(1))
                continue
            named = _NAMED.match(title)
            if named is not None:
                keys.add(named.group(1))
            continue
        row = _ROW_ID.match(line)
        if row is not None:
            keys.add(row.group(1))
    return keys


def _all_keys() -> set[str]:
    """Every section key any document offers, composites included.

    A composite is a section's **prefix** with a row's identifier: the Phase 2
    plan's section "P2-A" and its acceptance row "A1" make "§P2-A1", which is
    how the milestones ledger cites the plan.
    """
    per_document = [_keys_of(doc.read_text(encoding="utf-8")) for doc in _documents()]
    keys: set[str] = set()
    for document_keys in per_document:
        keys |= document_keys
    for document_keys in per_document:
        for key in document_keys:
            if "-" not in key:
                continue
            prefix = key.rsplit("-", 1)[0] + "-"
            for other in document_keys:
                if other != key and "-" not in other:
                    keys.add(prefix + other)
    return keys


def test_every_link_target_exists() -> None:
    """Every markdown link points at a file that is there."""
    missing: list[str] = []
    checked = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            checked += 1
            if not (REPO_ROOT / path).exists() and not (document.parent / path).exists():
                missing.append(f"{document.name}: {target}")
    assert not missing, "these links point at files that do not exist:\n  " + "\n  ".join(missing)
    print(f"\nlink targets checked: {checked}")


def test_every_link_anchor_exists() -> None:
    """Every markdown link's anchor is a heading in the file it points at."""
    missing: list[str] = []
    checked = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")) or "#" not in target:
                continue
            path, anchor = target.split("#", 1)
            if path:
                candidate = REPO_ROOT / path
                target_file = candidate if candidate.exists() else document.parent / path
                if not target_file.exists():
                    continue
            else:
                target_file = document
            checked += 1
            headings = [
                _slug(heading)
                for heading in re.findall(
                    r"^#{1,6}\s+(.*)$", target_file.read_text(encoding="utf-8"), re.MULTILINE
                )
            ]
            if anchor not in headings:
                missing.append(f"{document.name}: {target} (the file's headings: {headings[:6]})")
    assert not missing, "these links point at an anchor that is not a heading:\n  " + "\n  ".join(
        missing
    )
    print(f"\nanchors checked: {checked}")


def test_every_section_reference_resolves() -> None:
    """Every ``§`` reference that looks like a section id names a section."""
    known = _all_keys()
    unresolved: list[str] = []
    checked = 0
    prose = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _SECTION.finditer(text):
            key = match.group(1).rstrip(".")
            if not any(character.isdigit() for character in key) and "-" not in key:
                # Prose: "§" used as "see". A section id in this repository
                # always carries a digit or a hyphen.
                prose += 1
                continue
            checked += 1
            if not _section_resolves(key, known):
                unresolved.append(f"{document.name}: §{key}")
    assert not unresolved, (
        "these section references name a section that does not exist:\n  "
        + "\n  ".join(unresolved)
        + "\n\nA reference that points at nothing is a claim whose support cannot be found."
    )
    print(f"\nsection references checked: {checked}; read as prose: {prose}")


def test_every_section_reference_in_the_source_resolves() -> None:
    """A section reference in the source names a section that exists.

    A docstring that cites a section id of ``S2`` in ``docs/mcp-contracts.md``
    is making the same claim a document makes, and it is a claim a reader
    follows to find out what the code is supposed to do. Four such references
    survived in the source after the documents had been cleaned: one in
    ``src/opspilot/domain/tools.py``, two in ``tests/evals/`` and one in
    ``tests/unit/``.

    The source is scanned with the same key set as the documents, so the two
    halves of the tree are held to one rule.
    """
    known = _all_keys()
    unresolved: list[str] = []
    checked = 0
    prose = 0
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _SECTION.finditer(line):
                key = match.group(1).rstrip(".")
                if not any(character.isdigit() for character in key) and "-" not in key:
                    prose += 1
                    continue
                checked += 1
                if not _section_resolves(key, known):
                    unresolved.append(f"{relative}:{number}  §{key}")
    assert not unresolved, (
        "these source references name a section that does not exist:\n  "
        + "\n  ".join(unresolved)
        + "\n\nA reference that points at nothing is a claim whose support cannot "
        "be found -- in a docstring it is a reader's only guide to what the "
        "code is meant to do."
    )
    print(f"\nsource section references checked: {checked}; read as prose: {prose}")


def test_the_guard_finds_the_references_it_is_meant_to_find() -> None:
    """The guard is not vacuously green.

    A guard that collects nothing checks nothing. This asserts that all three
    kinds of reference are found, so a change in the patterns -- or in the
    documents -- cannot quietly turn the guard into a no-op that stays green.
    """
    links = 0
    anchors = 0
    sections = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            links += 1
            if "#" in target:
                anchors += 1
        sections += len(_SECTION.findall(text))

    assert links, "no markdown link was found; the pattern is broken"
    assert anchors, "no anchored link was found; the pattern is broken"
    assert sections, "no section reference was found; the pattern is broken"

    source_sections = 0
    for path in _source_files():
        source_sections += len(_SECTION.findall(path.read_text(encoding="utf-8")))
    assert source_sections, (
        "no section reference was found in the source; the source half of the "
        "rule has stopped matching"
    )
    print(
        f"\nlinks: {links}, anchored: {anchors}, section references: {sections}, "
        f"source section references: {source_sections}"
    )
