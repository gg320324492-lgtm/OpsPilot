"""The desktop's error dialog, and the verification documents that quote it.

## What this file exists for

``docs/installer-verification.md`` tells a person who did not build this
application to read the window and compare what it says against a list of
expected messages. That list is a copy of strings the shell produces, and a
copy is a second source of truth: the two drifted once, and the drift was
found by an audit rather than by a test. The drifted pair was::

    the app says   "Docker is not installed"
    the doc said   "Docker Desktop is not installed"

    the app says   "Docker is installed but not running"
    the doc said   "Docker is installed but not responding"

Neither was a *wrong* statement about the world -- the app is the one that
shows the message, and it was telling the truth. But a person verifying
against the wrong literal would have reported a failure that did not happen,
and a person verifying against the right one would have been told the
application was broken when it was working.

The documents reproduce more than the title. For the "Docker is not
installed" state they reproduce the **detail** and the **remedy** as well --
in italics in the English procedure and in a code block in the Chinese one --
and those are the three strings the operator reads. A document that quotes
text the application no longer shows is describing an application that does
not exist.

## Why this is Python, and what it reads

The shell is TypeScript, so this cannot import it the way the Playwright
specification in ``desktop/e2e/error-text.spec.ts`` does. What it can do is
*read* ``desktop/src/lifecycle/errors.ts`` and recover the message the
``dockerNotInstalled`` function assembles.

The message is assembled from a template and a pair of literals, so the two
holes in it are filled with the values the shell fills them with: ``command``
is the ``docker`` executable and ``code`` is ``ENOENT`` -- the code Node
reports when an executable is not on PATH, which is the only way a machine
without Docker reaches this branch.

**The reader fails loudly.** A hole it does not know, a function it cannot
find, a field it cannot read -- each raises, rather than yielding an empty
string that compares equal to nothing and passes.

## What this does not do

It does not check every message the shell can produce. The documents do not
quote all of them -- the procedure walks two of the three Docker states, and
says nothing about the third -- and a check that demanded a quotation the
document does not make would be a check of the document's contents rather
than of the drift.

It also does not replace the Playwright specification. That one compares the
documents against the *running* code, which is the stronger check; this one
is a second, independent reading of the same contract, in a different
language, and it fails when the TypeScript source's shape changes so that
the first reading no longer sees the message.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The shell's own source: where the message the documents quote is written.
ERRORS_SOURCE = REPO_ROOT / "desktop" / "src" / "lifecycle" / "errors.ts"

#: The documents a verifier is handed.
DOCUMENTS = (
    REPO_ROOT / "docs" / "installer-verification.md",
    REPO_ROOT / "docs" / "installer-verification.zh-CN.md",
)

#: The function that assembles the message the documents reproduce in full.
FUNCTION = "dockerNotInstalled"

#: The two holes in the detail, and the values the shell puts in them.
#:
#: ``command`` is ``deps.docker``, which is ``docker`` unless an operator
#: overrode it; ``code`` is the ``ENOENT`` Node reports for a missing
#: executable, which is the only path that reaches this message.
HOLES = (
    ("${command}", "docker"),
    ('${code === undefined ? "" : ` (${code})`}', " (ENOENT)"),
)

#: A string literal, in either of the two forms the source uses.
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`')


def _function_body(source: str, name: str) -> str:
    """The body of one exported function, or a loud failure."""
    start = source.find(f"export function {name}(")
    if start == -1:
        raise AssertionError(f"errors.ts has no exported function called {name!r}")
    end = source.find("\nexport function ", start + 1)
    if end == -1:
        end = source.find("\n/**", start + 1)
    if end == -1:
        end = len(source)
    return source[start:end]


def _field(body: str, name: str) -> str:
    """The expression a field is assigned, up to the next field."""
    start = body.find(f"\n    {name}:")
    if start == -1:
        raise AssertionError(f"{FUNCTION} has no {name!r} field")
    start += len(f"\n    {name}:")
    rest = body[start:]
    following = re.search(r"\n    (?:kind|title|detail|remedy):", rest)
    return rest[: following.start()] if following is not None else rest


def _resolve(expression: str) -> str:
    """The text the expression produces, with the holes filled in."""
    filled = expression
    for hole, value in HOLES:
        filled = filled.replace(hole, value)
    if "${" in filled:
        raise AssertionError(
            f"errors.ts assembles a message with a hole this reader does not know: {filled!r}"
        )
    pieces: list[str] = []
    for match in LITERAL.finditer(filled):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        assert raw is not None
        pieces.append(raw.replace("\\`", "`").replace('\\"', '"').replace("\\\\", "\\"))
    if not pieces:
        raise AssertionError(f"the expression produced no text: {expression!r}")
    return "".join(pieces)


def _collapse(text: str) -> str:
    """The text with its whitespace collapsed.

    The documents wrap their quotations across lines and the code does not,
    so the comparison has to ignore where the line breaks are.
    """
    return " ".join(text.split())


def _first_sentence(text: str) -> str:
    """A message's first sentence.

    The cut is at a full stop *followed by a space*, which is why a URL
    inside the sentence does not split it: ``https://www.docker.com`` has a
    full stop in it and no space after one.
    """
    stop = text.find(". ")
    return text if stop == -1 else text[: stop + 1]


def _quoted_spans(document: str) -> list[str]:
    """Every span the document *quotes*.

    The document is prose, and prose is not a structured artefact. What a
    reader is misled by is a **quoted** string the application never shows --
    a sentence about Docker Desktop the product being absent is not a
    quotation of what the window says, and the section heading "If Docker
    Desktop is not installed" is exactly such a sentence.

    So the check is not "the document must not contain the words". It is "the
    document must not quote a string the application never displays". Four
    shapes of quotation are recognised, because the documents use four:
    ``"…"``, `` `…` ``, a Markdown block quote, and a fenced code block.
    """
    spans: list[str] = []
    spans.extend(match.group(1) for match in re.finditer(r"`([^`\n]+)`", document))
    for match in re.finditer(r"```[^\n]*\n([\s\S]*?)```", document):
        block = match.group(1)
        spans.extend(block.split("\n"))
        spans.append(block)
    without_code = re.sub(r"```[\s\S]*?```", "", document)
    for match in re.finditer(r"^((?:>[^\n]*\n?)+)", without_code, re.MULTILINE):
        quoted = "\n".join(
            line.removeprefix(">").removeprefix(" ") for line in match.group(1).split("\n")
        )
        spans.append(quoted)
        spans.extend(quoted.split("\n"))
    spans.extend(" ".join(match.group(1).split()) for match in re.finditer(r'"([^"]+)"', document))
    return [" ".join(span.split()).strip() for span in spans]


def _message() -> dict[str, str]:
    """The three parts of the message the documents reproduce."""
    source = ERRORS_SOURCE.read_text(encoding="utf-8")
    body = _function_body(source, FUNCTION)
    return {
        "title": _resolve(_field(body, "title")),
        "detail": _resolve(_field(body, "detail")),
        "remedy": _resolve(_field(body, "remedy")),
    }


def test_the_message_is_read_from_the_shells_own_source() -> None:
    """The reader found the message, and the message is the one it should be.

    A reader that returned nothing would compare against nothing and pass
    every assertion below, so the message it recovered is asserted here --
    against the shell's own source, so that this file cannot drift into
    asserting a string the shell does not produce.
    """
    message = _message()

    assert message["title"] == "Docker is not installed"
    assert "not found on PATH (ENOENT)" in message["detail"]
    assert message["detail"].endswith("there is nothing this app can start without it.")
    assert "Install Docker Desktop for Windows" in message["remedy"]


def test_the_documents_quote_the_message_the_shell_shows() -> None:
    """Each document reproduces the message the window shows.

    The English procedure quotes the remedy's first sentence and describes
    the parenthetical that follows it; the Chinese one quotes the whole of
    it in a code block. So every document has to reproduce the opening, and
    **at least one** has to reproduce the whole -- otherwise a message
    reworded after its first sentence would leave both documents quoting
    text the application no longer shows, with the opening still matching
    and nothing red.
    """
    message = _message()
    documents = {path.name: _collapse(path.read_text(encoding="utf-8")) for path in DOCUMENTS}

    for name, document in documents.items():
        head = _collapse(_first_sentence(message["detail"]))
        assert head in document, (
            f"{name} does not quote the detail of the Docker-not-installed state. "
            "The window shows exactly that, and a verifier comparing against "
            "anything else reports a failure that did not happen."
        )
        remedy_head = _collapse(_first_sentence(message["remedy"]))
        assert remedy_head in document, (
            f"{name} does not quote the remedy of the Docker-not-installed "
            "state. The window shows exactly that."
        )

    assert any(_collapse(message["detail"]) in document for document in documents.values()), (
        "neither verification document quotes the detail of the "
        "Docker-not-installed state in full. The window shows exactly that."
    )
    assert any(_collapse(message["remedy"]) in document for document in documents.values()), (
        "neither verification document quotes the remedy of the "
        "Docker-not-installed state in full. The window shows exactly that."
    )


def test_neither_document_quotes_a_message_the_shell_never_shows() -> None:
    """The drifted pair, the one an audit found and a test did not.

    Each of these reads like a plausible message and each is exactly what a
    copy of slightly-different wording looks like. A document quoting one of
    them is describing an application that does not exist.
    """
    superseded = (
        "Docker Desktop is not installed",
        "Docker is installed but not responding",
    )
    for path in DOCUMENTS:
        quoted = _quoted_spans(path.read_text(encoding="utf-8"))
        for stale in superseded:
            assert stale not in quoted, (
                f"{path.name} still quotes {stale!r}, which the application has "
                "never displayed. The title it replaced is one of the two Docker "
                "states the procedure walks through."
            )
