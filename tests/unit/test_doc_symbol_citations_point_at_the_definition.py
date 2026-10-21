"""A documentation citation of the form ``Symbol:line`` must point at that symbol.

## Why this file exists

The documents quote code by line number — ``RunStatus:41``,
``ALLOWED_TRANSITIONS:62``, ``AgentRun.transition_to():192``. Each of those is a
*claim* about where something lives, and a claim in this repository gets a
guard or it does not get believed.

One of them rotted. ``docs/milestones.md`` said ``RunStatus:30``,
``ALLOWED_TRANSITIONS:51`` and ``AgentRun.transition_to():113`` for as long as
the file existed; the definitions are at 41, 62 and 192. Nobody noticed for a
simple reason: **every one of those citations was written by hand, so every one
of them was checked by hand — at the moment it was written, and never again.**
The project has been bitten by this class before: commit ``e906f8d`` is
"docs: five drifted figures and eight line citations that pointed elsewhere",
and that fix was manual too, which is a repair rather than a fix.

## What this file asserts

For every ``Symbol:line`` citation in ``docs/`` and ``README.md``:

* the symbol must have a **definition** somewhere in ``src/`` (a class, a
  function, or a module-level assignment), and
* that definition must be **on the cited line**.

A citation whose symbol cannot be found is not a failure — it is a citation
this guard cannot check, and the count of those is printed so a green run is
not read as full coverage. A symbol with several definitions is likewise
unchecked, and counted: `RunStatus` is defined once today, but a name that
appears in two modules is ambiguous, and an ambiguous citation is not a claim
about a line.

The dotted form (``AgentRun.transition_to():192``) is resolved on its last
segment, so it names the method rather than the class.

## What it does not do

It does not check the *other* citation form — ``path/to/file.py:123`` — where
the file is named and the line must be checked against that file. That form is
far more common and mostly resolves; where it does not, the fix is a fuller
path in the document. This guard covers the form that rotted.

**One trap worth naming.** ``docs/progress.md`` is a chronological log, and a
log sometimes has to quote the *stale* claim it is correcting. Quoting it in
the citation form (`` `RunStatus:30` ``) makes the log look as if it were
making the claim, and this guard reads it that way — which is why the log now
quotes the old figure as prose ("put ``RunStatus`` at line 30") instead. A log
that quotes a wrong claim should say that it is quoting one.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


#: The documents this guard reads. Every markdown file under ``docs/`` plus the
#: README, which quotes code by line as well.
def _documents() -> list[pathlib.Path]:
    docs = sorted((REPO_ROOT / "docs").rglob("*.md"))
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        docs.append(readme)
    return docs


#: A citation of the *symbol* form: a bare identifier (optionally dotted, and
#: optionally followed by the call's parentheses) and a line number. The
#: surrounding backticks are part of the pattern so that a URL such as
#: ``localhost:8000`` -- which is not a symbol at all -- is not mistaken for one.
_CITATION = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\(\))?:(\d+)`")

#: The file extensions the documents quote code by. A citation whose last
#: segment is one of these is a **path** (``metrics.py:432``), not a symbol, and
#: is not this guard's business — the file's own line is what such a citation
#: claims.
_FILE_EXTENSIONS = frozenset(
    {
        "py",
        "ts",
        "tsx",
        "js",
        "mjs",
        "cjs",
        "json",
        "yml",
        "yaml",
        "md",
        "toml",
        "ini",
        "css",
        "html",
    }
)

#: The source files a symbol may be defined in. ``migrations/`` is excluded: a
#: revision is a fixed point of history and may legitimately define a name that
#: also exists in the package.
_SOURCE_GLOBS = ("src/**/*.py",)


def _definitions(symbol: str) -> dict[str, list[int]]:
    """Every line at which ``symbol`` is defined, keyed by file.

    A definition is a ``class`` or ``def`` of that name, or a module-level
    assignment to it. The search is over the source tree rather than over the
    symbol table, because the point is to find *where it is written* — which is
    what a line citation claims.
    """
    pattern = re.compile(
        rf"^\s*(?:async\s+)?(?:class|def)\s+{re.escape(symbol)}\b"
        rf"|^\s*{re.escape(symbol)}\s*(?::[^=\n]+)?="
    )
    found: dict[str, list[int]] = {}
    for glob in _SOURCE_GLOBS:
        for path in sorted(REPO_ROOT.glob(glob)):
            hits = [
                number
                for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                )
                if pattern.match(line)
            ]
            if hits:
                found[path.relative_to(REPO_ROOT).as_posix()] = hits
    return found


def _is_hostname(name: str, document_text: str) -> bool:
    """Whether ``name`` is a host in a URL rather than a symbol.

    ``localhost:8000`` is a citation of a port, not of a line of code, and it
    must not be treated as a symbol that ought to have a definition. The test is
    whether the same name appears elsewhere in the same document behind a URL's
    ``@``, ``://`` or ``/`` — which is how a host is always written.
    """
    if "_" in name or not name.islower():
        return False
    return any(marker + name in document_text for marker in ("@", "://", "/"))


def _looks_like_a_word(name: str) -> bool:
    """Whether ``name`` reads as an English word rather than as code.

    A single lowercase word with no underscore is what a host name looks like
    (``localhost``) and is not what a Python symbol looks like
    (``RunStatus``, ``ALLOWED_TRANSITIONS``, ``transition_to``). A name like
    these that has *no definition* is therefore a host in a ``host:port``
    citation, not a symbol that was renamed — and the two must not be confused,
    because one is a citation of a port and the other is a broken claim.
    """
    return name.isalpha() and name.islower() and "_" not in name


def _citations() -> Iterator[tuple[pathlib.Path, str, int]]:
    """Every symbol-form citation, as ``(document, symbol, line)``.

    Three kinds of thing are *not* symbol citations and are skipped:

    * a name with two or more dots -- ``host.docker.internal:15742`` is a host;
    * a name whose last segment is a file extension -- ``metrics.py:432`` names
      a file, and the claim it makes is about that file's line, which is a
      different check against a different thing;
    * a host -- ``localhost:8000`` -- by the rule above.

    Everything else is a claim about where a symbol is defined, and it is
    checked.
    """
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _CITATION.finditer(text):
            name = match.group(1)
            if name.count(".") > 1:
                continue
            if name.split(".")[-1] in _FILE_EXTENSIONS:
                continue
            symbol = name.split(".")[-1]
            if _is_hostname(symbol, text):
                continue
            yield document, symbol, int(match.group(2))


def test_every_symbol_citation_points_at_its_definition() -> None:
    """Each ``Symbol:line`` citation names the line its symbol is defined on.

    The check is per-citation, so the failure names the document, the symbol
    and the line it should have said — a diff between two line numbers is not
    a useful error message on its own.
    """
    wrong: list[str] = []
    missing: list[str] = []
    unchecked = 0
    checked = 0

    for document, symbol, line in _citations():
        definitions = _definitions(symbol)
        if not definitions:
            if _looks_like_a_word(symbol):
                # A plain lowercase word with no definition is not a symbol that
                # was renamed -- it is a host in a `host:port` citation
                # (`localhost:8000`). Counted, not failed.
                unchecked += 1
                continue
            # The symbol has no definition anywhere in the source: it was
            # renamed, deleted, or never existed. The citation is now a claim
            # about nothing, and that is the drift this guard exists for.
            missing.append(
                f"{document.relative_to(REPO_ROOT).as_posix()}: `{symbol}:{line}` "
                f"names {symbol}, which is not defined anywhere in the source"
            )
            continue
        places = [f"{path}:{number}" for path, numbers in definitions.items() for number in numbers]
        if len(places) > 1:
            # Ambiguous: the name is defined in more than one place, so the
            # citation does not name one line. Counted, not failed.
            unchecked += 1
            continue
        checked += 1
        if line != definitions[next(iter(definitions))][0]:
            wrong.append(
                f"{document.relative_to(REPO_ROOT).as_posix()}: `{symbol}:{line}` "
                f"points at {symbol} being defined at "
                f"{definitions[next(iter(definitions))][0]}"
            )

    assert not missing, (
        "these citations name a symbol that does not exist:\n  "
        + "\n  ".join(missing)
        + "\n\nA citation of a symbol that was renamed or deleted is a claim "
        "about nothing."
    )
    assert not wrong, (
        "these citations point at the wrong line:\n  "
        + "\n  ".join(wrong)
        + "\n\nA line citation is a claim about where something lives. Checked "
        "by hand it is checked once; checked by a test it is checked for ever."
    )
    # The coverage figure is printed rather than asserted, because a guard that
    # cannot resolve a citation is not a failure — but a reader should be able
    # to see how much it actually looked at.
    print(f"\nsymbol citations checked: {checked}; unresolved (counted, not failed): {unchecked}")


def test_the_guard_finds_the_citations_it_is_meant_to_find() -> None:
    """The guard is not vacuously green.

    A guard that collects nothing checks nothing. This asserts that the scan
    finds the symbol-form citations the documents actually contain, and that
    at least one of them resolves to a definition -- so a change in the
    citation syntax, or in the documents, cannot quietly turn the guard into
    a no-op that stays green.
    """
    citations = list(_citations())
    assert citations, "no symbol-form citation was found in the documents at all"

    resolvable = [symbol for _doc, symbol, _line in citations if _definitions(symbol)]
    assert resolvable, (
        "every symbol-form citation failed to resolve to a definition, so the "
        f"guard checked nothing. The citations found were: "
        f"{sorted({symbol for _d, symbol, _l in citations})}"
    )

    # The three that rotted, by name: a guard that stopped seeing them would
    # be a guard that stopped working.
    symbols = {symbol for _doc, symbol, _line in citations}
    for expected in ("RunStatus", "ALLOWED_TRANSITIONS"):
        assert expected in symbols, (
            f"the guard no longer sees the `{expected}` citation, which is one "
            "of the two that rotted. The pattern has probably changed."
        )
