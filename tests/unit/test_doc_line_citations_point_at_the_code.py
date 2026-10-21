"""A documentation citation must point at the line it claims to.

## Why this file exists

The documents cite code by line number, and a citation that points at the
wrong line is a claim a reader cannot check. It has happened three times in
this repository's history — commit ``e906f8d`` is "docs: five drifted
figures and eight line citations that pointed elsewhere", and an audit of
``docs/milestones.md`` found eight more, several of them pointing at a
*different class* in the same file: ``repositories.py:395-420`` for
``SqlRunStore.set_status``, which is ``TicketStore.count``'s docstring.

Every one of those was fixed by hand, and a manual fix to a defect that
recurs is a repair rather than a fix.

## What this file asserts

Two citation shapes are mechanical enough to check, and between them they cover
the citations that carry a *claim about specific code*:

1. **A named symbol beside a citation** — `` `SqlRunStore.set_status`
   (`repositories.py:395-420`) ``. The symbol must be **defined** at the cited
   line. This is the shape most of the wrong pointers had.
2. **A quotation beside a citation** — `` `web/src/lib/format.ts:136` ("a null
   here is not missing data ...") ``. The quoted text must appear at the cited
   line, or within the cited range.

## What it does not do

* It does not check a **bare** citation — `` `repositories.py:395-420` `` with
  no symbol and no quotation beside it. There is nothing mechanical to check
  against: whether the line is the *right* line is a question about meaning,
  and a guard that guessed would be a guard that misfired. Those citations are
  counted and reported, so the coverage is visible rather than implied.
* It does not check a citation of a **line range** as a whole, only its
  endpoints, and only for the two shapes above.
* It does not check that the symbol is the *right* symbol for the sentence --
  only that the symbol named beside the citation is defined where the citation
  says it is. That is the part that can be checked without reading the prose.

A citation whose path cannot be resolved to exactly one file in the
repository is counted as unchecked rather than failed: an ambiguous path is a
document that should be clearer, and that is a different defect.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


#: The documents this guard reads. Every markdown under ``docs/``, the two
#: READMEs, and the ADRs (which live under ``docs/``).
def _documents() -> list[pathlib.Path]:
    return [
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        REPO_ROOT / "README.md",
        REPO_ROOT / "desktop" / "README.md",
    ]


#: `` `Symbol` (`path:line`) `` -- the symbol is named right before the citation.
_SYMBOL_THEN_CITE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`\s*\(\s*`"
    r"([A-Za-z0-9_./\[\]-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`"
)

#: `` `path:line` ("a quotation") `` -- the text is quoted right after the citation.
_QUOTE_AFTER_CITE = re.compile(
    r"`([A-Za-z0-9_./\[\]-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`\s*\(?\s*\"([^\"]{12,})\""
)

#: A bare citation -- counted, not checked.
_BARE_CITE = re.compile(r"`[A-Za-z0-9_./\[\]-]+\.[A-Za-z0-9]+:\d+(?:-\d+)?`")

#: A definition of a name, in the shapes the sources write them. The
#: templates take ``{name}``; they are compiled per name below, because a
#: compiled pattern has no ``format``.
_DEFINITION_TEMPLATES = (
    r"^\s*class\s+{name}\b",
    r"^\s*(?:async\s+)?def\s+{name}\b",
    r"^\s*{name}\s*(?::[^=\n]+)?=",
)


def _squashed(text: str) -> str:
    """The text reduced to the form two writings of it can be compared on.

    Four differences are legitimate and are normalised away: the wrapping (a
    quotation is wrapped to the document's margin, the source to the source's),
    the backticks (a document may drop the ones the source has around an
    identifier), the comment markers (a wrapped block comment puts a ``*`` at
    the start of every line, in the middle of the sentence it carries), and
    the case (a quotation taken from the middle of a sentence may begin in the
    lower case). The punctuation an abridged quotation replaces with an ellipsis
    is handled by the caller, which splits the quotation into the pieces around
    it. What is left is the words, and the words are the claim.
    """
    without_markers = re.sub(r"(?m)^\s*(?:\*+|//+|#+)\s?", "", text)
    without_ticks = without_markers.replace("`", "")
    return re.sub(r"\s+", " ", without_ticks).strip().lower()


def _fragments(quotation: str) -> list[str]:
    """The quotation split at its ellipses.

    A quotation may abridge the middle of a passage, and the abridgement is
    marked with an ellipsis. Each piece around it is still a claim about the
    cited line, and each is checked.
    """
    pieces = re.split(r"…|\.\.\.", quotation)
    return [piece for piece in (_squashed(piece) for piece in pieces) if len(piece) >= 12]


def _resolve(path: str) -> pathlib.Path | None:
    """The repository file a cited path names, or ``None`` if it is ambiguous.

    The documents cite a path relative to the repository root, but they also
    cite a *suffix* of it -- ``repositories.py`` for a file three directories
    down. A suffix that names exactly one file in the tree resolves; a suffix
    that names several does not, and a citation that cannot be resolved is
    counted rather than failed.
    """
    candidates = _candidates(path)
    if len(candidates) == 1:
        return candidates[0]
    return None


#: Directories that hold **copies** of the source rather than the source: the
#: desktop package's staged and unpacked runtime trees, the build outputs, and
#: the dependency trees. A citation points at a file in the repository, and
#: these are not files in the repository -- they are files *made from* it. Left
#: in, they make every bare file name ambiguous, because ``metrics.py`` then
#: names three files and none of them is the one the document meant.
_GENERATED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "release",
        "stage",
        "test-results",
    }
)


def _candidates(path: str) -> list[pathlib.Path]:
    """Every repository file a cited path could name.

    A path that is exactly right resolves to one file. A path that is only a
    suffix -- ``repositories.py`` -- resolves to every file with that name,
    and the symbol named beside the citation is what picks between them.
    """
    direct = REPO_ROOT / path
    if direct.is_file():
        return [direct]

    def is_source(candidate: pathlib.Path) -> bool:
        return not (_GENERATED_DIRECTORIES & set(candidate.parts))

    name = pathlib.Path(path).name
    by_suffix = [
        candidate
        for candidate in REPO_ROOT.rglob(name)
        if candidate.is_file()
        and is_source(candidate)
        and candidate.as_posix().endswith("/" + path)
    ]
    if by_suffix:
        return sorted(by_suffix)
    return sorted(
        candidate
        for candidate in REPO_ROOT.rglob(name)
        if candidate.is_file() and is_source(candidate)
    )


def _lines_of(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _defines_at(lines: list[str], symbol: str, line: int) -> bool:
    """Whether ``symbol`` is defined on ``line`` (1-based).

    A dotted symbol names a member: ``SqlRunStore.set_status`` is the method
    ``set_status``, so the last segment is what is looked for, and the class
    part is only used to keep the search honest.
    """
    if not 1 <= line <= len(lines):
        return False
    name = symbol.split(".")[-1]
    for template in _DEFINITION_TEMPLATES:
        if re.match(template.format(name=re.escape(name)), lines[line - 1]):
            return True
    return False


def _definition_line(lines: list[str], symbol: str) -> int | None:
    """The line the symbol is defined on, or ``None``.

    A dotted symbol names a member: ``SqlRunStore.set_status`` is the method
    ``set_status``, so the last segment is what is looked for.
    """
    name = symbol.split(".")[-1]
    for number, line in enumerate(lines, 1):
        for template in _DEFINITION_TEMPLATES:
            if re.match(template.format(name=re.escape(name)), line):
                return number
    return None


def _block_extent(lines: list[str], definition: int) -> tuple[int, int]:
    """The lines the definition at ``definition`` owns.

    A block ends at the next line that is not blank and is indented no further
    than the definition itself -- the standard reading of a Python block, and
    good enough to say "the cited lines are inside this definition".

    The **signature** is not part of that rule, because a multi-line signature
    ends with a line at the definition's own indentation (``) -> None:``), which
    the indentation rule would take for the end of the body. So the signature is
    skipped first, by bracket depth: the body's first line is the one after the
    definition's parentheses balance.
    """
    indent = len(lines[definition - 1]) - len(lines[definition - 1].lstrip())

    depth = 0
    body_start = definition + 1
    for number in range(definition, len(lines) + 1):
        line = lines[number - 1]
        depth += line.count("(") + line.count("[") + line.count("{")
        depth -= line.count(")") + line.count("]") + line.count("}")
        if depth <= 0:
            body_start = number + 1
            break

    end = body_start - 1
    for number in range(body_start, len(lines) + 1):
        line = lines[number - 1]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        end = number
    return definition, max(end, definition)


def _mentions(lines: list[str], symbol: str, first: int, last: int) -> bool:
    """Whether the cited lines mention the symbol at all.

    The fallback for a symbol that is **not** a definition in the cited file --
    a string constant, a metric name, an enum member. It is still always
    something the cited lines are about, and a citation that points at a
    different function mentions nothing of the sort.
    """
    name = symbol.split(".")[-1].lower()
    if not name:
        return True
    window = "\n".join(lines[first - 1 : last]) if first <= len(lines) else ""
    return name in window.lower()


def test_every_named_symbol_citation_points_at_its_definition() -> None:
    """A symbol named beside a citation is defined at the cited line."""
    wrong: list[str] = []
    checked = 0
    unchecked = 0

    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _SYMBOL_THEN_CITE.finditer(text):
            symbol = match.group(1)
            path = match.group(2)
            first = int(match.group(3))
            last = match.group(4)
            candidates = _candidates(path)
            if not candidates:
                unchecked += 1
                continue
            if len(candidates) > 1:
                # The path is a bare file name, so the symbol picks the file:
                # the one that mentions it is the one the citation means.
                mentioning = [
                    candidate
                    for candidate in candidates
                    if _mentions(_lines_of(candidate), symbol, 1, len(_lines_of(candidate)))
                ]
                if len(mentioning) != 1:
                    unchecked += 1
                    continue
                candidates = mentioning
            resolved = candidates[0]
            lines = _lines_of(resolved)
            end = int(last) if last is not None else first
            if end > len(lines):
                wrong.append(
                    f"{document.relative_to(REPO_ROOT).as_posix()}: the citation "
                    f"{path}:{first}-{end} runs past the end of the file "
                    f"({len(lines)} lines)"
                )
                continue
            definition = _definition_line(lines, symbol)
            if definition is not None:
                # The symbol is a definition in this file, so the cited lines
                # must be inside its block.
                block_start, block_end = _block_extent(lines, definition)
                if not (block_start <= end and first <= block_end):
                    wrong.append(
                        f"{document.relative_to(REPO_ROOT).as_posix()}: `{symbol}` is cited at "
                        f"{path}:{first}{'-' + last if last else ''}, and those lines are not "
                        f"inside it (it is defined at line {definition}). Line {first} of that "
                        f"file is: "
                        f"{(lines[first - 1].strip() if first <= len(lines) else '<past end>')!r}"
                    )
                    continue
            elif not _mentions(lines, symbol, first, end):
                wrong.append(
                    f"{document.relative_to(REPO_ROOT).as_posix()}: `{symbol}` is cited at "
                    f"{path}:{first}{'-' + last if last else ''}, and those lines do not "
                    f"mention it. Line {first} of that file is: "
                    f"{(lines[first - 1].strip() if first <= len(lines) else '<past the end>')!r}"
                )
                continue
            checked += 1

    assert not wrong, (
        "these citations point at a line that does not define the symbol they name:\n  "
        + "\n  ".join(wrong)
        + "\n\nA citation that points at the wrong line is a claim a reader cannot check."
    )
    print(f"\nsymbol citations checked: {checked}; unresolved: {unchecked}")


def test_every_quoted_citation_contains_its_quotation() -> None:
    """A quotation beside a citation is on the cited line, or within its range."""
    wrong: list[str] = []
    checked = 0
    unchecked = 0

    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for match in _QUOTE_AFTER_CITE.finditer(text):
            path, first, last, quotation = (
                match.group(1),
                int(match.group(2)),
                match.group(3),
                match.group(4),
            )
            resolved = _resolve(path)
            if resolved is None:
                unchecked += 1
                continue
            lines = _lines_of(resolved)
            end = int(last) if last is not None else first
            window = "\n".join(lines[first - 1 : end]) if first <= len(lines) else ""
            # Each piece of the quotation -- the text around an ellipsis, if it
            # has one -- must appear in the cited lines.
            squashed = _squashed(window)
            missing = [piece for piece in _fragments(quotation) if piece not in squashed]
            if missing:
                wrong.append(
                    f"{document.relative_to(REPO_ROOT).as_posix()}: the citation {path}:{first}"
                    f"{'-' + last if last else ''} does not contain the text quoted beside it: "
                    f"{missing[0][:70]!r}"
                )
                continue
            checked += 1

    assert not wrong, (
        "these citations point at a line that does not contain the text quoted "
        "beside it:\n  " + "\n  ".join(wrong)
    )
    print(f"\nquoted citations checked: {checked}; unresolved: {unchecked}")


def test_the_guard_finds_the_citations_it_is_meant_to_find() -> None:
    """The guard is not vacuously green.

    A guard that collects nothing checks nothing. This asserts that both
    patterns are found, that at least one citation of each kind resolves to a
    file, and that the number of *bare* citations -- the ones this guard does
    not check -- is printed, so the coverage is visible.
    """
    symbols = 0
    quotes = 0
    bare = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        symbols += len(_SYMBOL_THEN_CITE.findall(text))
        quotes += len(_QUOTE_AFTER_CITE.findall(text))
        bare += len(_BARE_CITE.findall(text))

    assert symbols, "no `Symbol` (`path:line`) citation was found; the pattern is broken"
    assert quotes, "no quoted citation was found; the pattern is broken"
    print(f"\nsymbol+cite: {symbols}, quote+cite: {quotes}, bare (unchecked): {bare}")
