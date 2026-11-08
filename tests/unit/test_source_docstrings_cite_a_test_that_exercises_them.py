"""A source docstring that cites a test must cite one that exercises it.

## Why this file exists

A docstring in ``src/`` that names a test file is making a claim a reader can
check: *this behaviour is asserted over there*. When the two disagree, the
docstring is the thing that is wrong -- but nothing notices, because the
docstring is prose and the test file it names is a real, passing, green file.

That is exactly the shape of drift this repository keeps finding. A concrete
instance, found in ``src/opspilot/api/routers/runs.py``:

    Every key read here is asserted against a real recorded step by
    ``tests/integration/test_run_detail_against_the_real_store.py``.

The named file exists, passes, and asserts the *store* -- that
``SqlRunStore`` offers three reads and that the panels are not empty. It never
touches the output keys the function reads. The file that does assert them,
against real recorded steps, is
``tests/agent/test_trace_labels_are_read_from_real_steps.py``. The claim was
true; the pointer was not.

## What this file asserts

For every function and method in ``src/opspilot/api/`` and
``src/opspilot/agents/`` whose docstring cites a ``tests/...py`` path:

1. **The cited file exists.** A renamed or moved test is a broken pointer.
2. **When the docstring claims the cited file asserts something about the
   symbol it documents, the cited file must actually mention that symbol.**
   That is the mechanical proxy for "the evidence is over there": a test file
   that never names the function under discussion cannot be asserting
   something about it.

Rule 2 is deliberately narrow. It fires only when a *sentence* of the
docstring both cites a test path and carries a claim verb -- "asserted",
"checks", "drives", "pins" and so on. A docstring that merely mentions a test
file in passing ("the same sentinels ``tests/...`` looks for") is a reference,
not an evidence claim, and is not held to the symbol rule.

## What it does not do

It does not check that the cited test asserts *the right thing* -- that is a
question about meaning, and a guard that guessed would be a guard that
misfired. It checks the pointer, not the prose. A docstring that cites the
right file and describes it wrongly is still wrong; this file cannot see that.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The source trees this guard reads. The API and the agents layer: the two
#: whose docstrings make checkable claims about their own behaviour.
SOURCE_ROOTS = (
    REPO_ROOT / "src" / "opspilot" / "api",
    REPO_ROOT / "src" / "opspilot" / "agents",
    REPO_ROOT / "src" / "opspilot" / "evals",
    REPO_ROOT / "src" / "opspilot" / "worker",
    REPO_ROOT / "src" / "opspilot" / "adapters",
    REPO_ROOT / "src" / "opspilot" / "domain",
    REPO_ROOT / "src" / "opspilot" / "ports",
    REPO_ROOT / "src" / "opspilot" / "tracing",
    REPO_ROOT / "mcp_servers",
)

#: A ``tests/...py`` path inside backticks.
_CITED_TEST = re.compile(r"`(tests/[^`\s]+?\.py)`")

#: The verbs that make a citation an *evidence* claim rather than a passing
#: mention. A sentence with one of these next to a test path is saying "that
#: file checks this".
_CLAIM_VERBS = re.compile(
    r"\b(assert|asserts|asserted|checked|checks|verifies|verified|exercises|"
    r"covers|covered|drives|driven|pins|pinned|measures|measured)\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    """Split a docstring into sentences, on full stops.

    A sentence is the unit the claim rule is written against: the citation and
    the verb that gives it its force belong in the same sentence, or the
    citation is a passing mention rather than a claim about what that file
    checks.
    """
    parts = re.split(r"(?<=[.!?:])\s+", text)
    return [part for part in parts if part.strip()]


def _documented_symbols() -> list[tuple[pathlib.Path, str, str, list[str]]]:
    """Every documented function/method in scope, with its docstring and citations.

    Returns ``(source_path, symbol_name, docstring, cited_test_paths)``.
    Module-level docstrings are excluded on purpose: a module's docstring
    talks about the module, so "the cited file must name the module" would be
    a rule about a different kind of claim.
    """
    found: list[tuple[pathlib.Path, str, str, list[str]]] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                docstring = ast.get_docstring(node) or ""
                citations = _CITED_TEST.findall(docstring)
                if citations:
                    found.append((path, node.name, docstring, citations))
    return found


def _evidence_citations(docstring: str) -> list[tuple[str, str]]:
    """The citations in ``docstring`` that are *evidence* claims, with the sentence.

    A citation is an evidence claim when the sentence carrying it also carries
    a claim verb. A sentence that merely points at a file ("the same sentinels
    ``tests/...`` looks for") is a reference and is left alone.
    """
    pairs: list[tuple[str, str]] = []
    for sentence in _sentences(docstring):
        citations = _CITED_TEST.findall(sentence)
        if citations and _CLAIM_VERBS.search(sentence):
            pairs.extend((citation, sentence) for citation in citations)
    return pairs


def test_every_cited_test_file_exists() -> None:
    """A docstring that points at a test points at a file that is there.

    A renamed or moved test leaves a pointer that resolves to nothing, and
    nothing else in the suite would notice: the citation is prose.
    """
    missing: list[str] = []
    for path, symbol, _docstring, citations in _documented_symbols():
        for citation in citations:
            if not (REPO_ROOT / citation).is_file():
                missing.append(f"{path.relative_to(REPO_ROOT)}:{symbol} -> {citation}")

    assert not missing, (
        "these docstrings cite a test file that does not exist:\n  "
        + "\n  ".join(missing)
        + "\nA citation that resolves to nothing is a claim a reader cannot check."
    )


def test_a_cited_test_file_actually_exercises_the_symbol_it_is_cited_for() -> None:
    """An evidence citation names a file that mentions the symbol.

    This is the drift the file exists for: a docstring saying "asserted
    against a real recorded step by ``tests/...``" where the named file asserts
    something else entirely. The cited file must contain the documented
    symbol's name, or the pointer is pointing at the wrong evidence.

    **A private symbol is exempt.** A test does not call ``_render_report``; it
    calls the CLI and asserts what the CLI prints, so the private name never
    appears in the test file -- and the citation is perfectly good. The rule
    is therefore about symbols a test *could* name, which is to say the public
    ones. For a private symbol the only check that means anything is that the
    file exists.
    """
    wrong: list[str] = []
    for path, symbol, docstring, _citations in _documented_symbols():
        for citation, sentence in _evidence_citations(docstring):
            target = REPO_ROOT / citation
            if not target.is_file():
                continue
            if symbol.startswith("_"):
                continue
            if symbol in target.read_text(encoding="utf-8"):
                continue
            wrong.append(
                f"{path.relative_to(REPO_ROOT)}:{symbol} cites {citation}, which "
                f"never mentions {symbol!r}.\n    sentence: {' '.join(sentence.split())}"
            )

    assert not wrong, (
        "these docstrings cite a test file that does not exercise the symbol "
        "they document:\n  " + "\n  ".join(wrong) + "\nThe claim may be true; the "
        "pointer is not. Either cite the file that does exercise it, or stop "
        "presenting that file as the evidence."
    )


def test_the_guard_finds_the_citations_it_is_meant_to_find() -> None:
    """The guard is not vacuous: it finds citations, and it judges them.

    A guard that silently stopped matching would report green forever. This
    asserts three things about the machinery rather than about the tree: that
    citations are still found at all, that the evidence rule still fires on a
    claim-shaped sentence, and that it does *not* fire on a passing mention --
    so a future edit that widens the rule to "every citation" is caught here
    rather than producing false positives everywhere.
    """
    documented = _documented_symbols()
    assert documented, (
        "no docstring in the API or the agents layer cites a test file any more; "
        "the guard has nothing to check and will pass vacuously from now on"
    )

    claim_shaped = (
        "Every key read here is asserted against a real recorded step by "
        "``tests/integration/test_run_detail_against_the_real_store.py``."
    )
    reference_shaped = (
        "The same sentinels ``tests/evals/test_injection_delivery.py`` looks for "
        "decide that the injected text was delivered as data."
    )

    assert _evidence_citations(claim_shaped), (
        "the evidence rule no longer recognises a claim-shaped sentence"
    )
    assert not _evidence_citations(reference_shaped), (
        "the evidence rule now fires on a passing mention; it must fire only "
        "where a sentence claims the cited file checks something"
    )
