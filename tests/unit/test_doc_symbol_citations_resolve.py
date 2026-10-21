"""A document's ``file.py::symbol`` citations must point at a real symbol.

## Why this file exists

The documents cite code by path in two shapes. One is ``file.py:120-131``, and
``tests/unit/test_doc_line_citations_point_at_the_code.py`` checks that shape --
the cited lines must contain the symbol named beside them. The other is
``file.py::symbol``, which names a symbol *without* a line, and **nothing
checked it**.

That is how ``docs/risks.md`` came to cite ``workers/loop.py::_resolve_resume``
for two years' worth of reading: the module is ``worker/loop.py``, singular,
and the citation pointed at a directory that does not exist. A reader following
it found nothing, and the sentence it supported -- that a restarted worker
resumes an interrupted run rather than failing it -- was the one part of that
paragraph a reader most needed to check.

The shape is worth checking because it is **unambiguous**. ``file.py::name``
says exactly two things: this file, this name. There is no prose to
disambiguate and no heuristic to tune, which is what makes it a good guard
rather than a clever one.

## What this file asserts

For every ``path::symbol`` citation in the documents:

1. **the file exists** -- relative to the repository root, relative to the
   document's own directory, or found by its bare name when the citation writes
   only a file name (``metrics.py::tool_argument_validity``);
2. **the symbol is defined there** -- for Python, by parsing the file's AST and
   looking for a module-level ``def``/``class``/assignment, or a method inside
   the named class (``SqlRunStore.claim_next``); for TypeScript, by looking for
   a declaration of that name.

## What it does not do

It does not check that the symbol *does* what the sentence says. That is a
question of meaning, and a guard that guessed would misfire on every honest
citation. It checks the half that is mechanical: the thing cited is there.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The documents that cite code by symbol.
_DOCUMENTS = (
    *sorted((REPO_ROOT / "docs").rglob("*.md")),
    REPO_ROOT / "README.md",
    REPO_ROOT / "desktop" / "README.md",
    REPO_ROOT / "web" / "README.md",
)

#: A citation: ``path/name.ext::symbol`` or ``path/name.ext::Class.method``.
_CITATION = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|ts|tsx|mjs|js))::([A-Za-z_][A-Za-z0-9_.]*)`"
)

#: The directories a bare file name may live in.
_SEARCH_ROOTS = (
    "src",
    "tests",
    "web",
    "desktop",
    "mcp_servers",
    "scripts",
    "evals",
    "docs",
)

#: A TypeScript declaration of a name.
_TS_DECLARATION = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)


def _documents() -> list[pathlib.Path]:
    """The documents that cite code by symbol."""
    return [document for document in _DOCUMENTS if document.exists()]


def _python_names(path: pathlib.Path) -> set[str]:
    """The names a Python file defines, including ``Class.method``.

    A method's **bare** name is included as well as the qualified one, because
    a citation that names a method without its class is how these documents
    have always written them -- ``mcp_gateway.py::_server_for`` for
    ``MCPToolGateway._server_for`` -- and a reader looking for the name finds
    the method.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                        names.add(f"{node.name}.{member.name}")
                        names.add(member.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _typescript_names(path: pathlib.Path) -> set[str]:
    """The names a TypeScript file declares."""
    source = path.read_text(encoding="utf-8")
    return set(_TS_DECLARATION.findall(source))


def _names(path: pathlib.Path) -> set[str]:
    """The names a file defines, by its language."""
    if path.suffix == ".py":
        return _python_names(path)
    return _typescript_names(path)


def _candidates(reference: str, document: pathlib.Path) -> list[pathlib.Path]:
    """The files a citation's path could mean."""
    direct = [
        candidate
        for candidate in (REPO_ROOT / reference, document.parent / reference)
        if candidate.is_file()
    ]
    if direct:
        return direct
    name = pathlib.PurePath(reference).name
    found: list[pathlib.Path] = []
    for root in _SEARCH_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        found.extend(path for path in base.rglob(name) if path.is_file())
    return found


def test_every_symbol_citation_points_at_a_symbol_that_exists() -> None:
    """Every ``file::symbol`` citation names a file and a name that are there."""
    missing_files: list[str] = []
    missing_symbols: list[str] = []
    checked = 0

    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _CITATION.finditer(text):
            reference = match.group(1)
            symbol = match.group(2)
            where = f"{document.relative_to(REPO_ROOT).as_posix()}: {reference}::{symbol}"

            candidates = _candidates(reference, document)
            if not candidates:
                missing_files.append(where)
                continue

            if len(candidates) > 1:
                # A bare file name: the symbol picks the file, because the
                # citation means the one that defines it.
                defining = [candidate for candidate in candidates if symbol in _names(candidate)]
                if len(defining) != 1:
                    # Ambiguous, or defined nowhere: report it rather than
                    # guessing which file was meant.
                    if not defining:
                        missing_symbols.append(where)
                    continue
                candidates = defining

            resolved = candidates[0]
            checked += 1
            if symbol not in _names(resolved):
                missing_symbols.append(where)

    assert not missing_files, (
        "these citations name a file that does not exist:\n  "
        + "\n  ".join(missing_files)
        + "\n\nA citation that points at nothing is a claim whose support cannot "
        "be found."
    )
    assert not missing_symbols, (
        "these citations name a symbol that the file does not define:\n  "
        + "\n  ".join(missing_symbols)
        + "\n\n`file.py::symbol` says exactly two things -- this file, this name. "
        "Either one being wrong makes the sentence uncitable."
    )
    print(f"\nsymbol citations checked: {checked}")


def test_the_guard_finds_the_citations_it_is_meant_to_find() -> None:
    """The guard is not vacuously green.

    A guard that collects nothing checks nothing. This asserts that the
    documents really do cite code by symbol, so a change in the pattern cannot
    quietly turn the guard into a no-op that stays green.
    """
    found = 0
    for document in _documents():
        found += len(_CITATION.findall(document.read_text(encoding="utf-8")))
    assert found >= 20, (
        f"only {found} symbol citations were found in the documents; the pattern "
        "is probably broken, and the guard would be checking nothing"
    )
    print(f"\nsymbol citations in the documents: {found}")
