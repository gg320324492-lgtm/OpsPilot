"""A marker is a claim that a test exercises something real. A stub cannot keep it.

## Why this file exists

``docs/progress.md``'s re-scored Definition of Done carries one recommended row
with no guard behind it: **"Every marked test can fail."** A marker -- ``postgres``,
``live``, ``slow``, ``parametrize`` -- is a promise about the work a test does.
``@pytest.mark.postgres`` says *this test stands up a real vector store and holds it
to the shared contract*. A test whose body is ``...``, or whose only statement is an
unconditional ``pytest.skip(...)``, cannot keep that promise, and the suite reports
it green anyway:

- a body of ``...`` or ``pass`` raises nothing and asserts nothing;
- a body of one unconditional ``pytest.skip(...)`` executes no more than one
  statement on any machine -- GitHub's runners included -- and lands in the skip
  total, which is the number D4 (``docs/milestones.md``) asks to be **non-zero**.
  "pytest is green with a non-zero skip count" is therefore satisfied *completely*
  by a suite of skip-stubs. The green is real and it means nothing.

This is not hypothetical. ADR-0004's Correction 2 records the instance, found by
reading the suite rather than by running it: ``tests/unit/test_vector_stores.py``
carried two ``@pytest.mark.postgres`` tests named
``test_pgvector_store_satisfies_shared_contract`` and
``test_pgvector_and_memory_stores_agree`` *whose entire bodies were an unconditional
``pytest.skip(...)``*. They were collected, reported as skipped and counted toward a
green run, while the CI comment above them said out loud that they were stubs.
``docs/progress.md`` numbers that instance 6 of the family this repository keeps
re-finding, and names the tell: it was found by reading. Both tests now do the work
-- ``tests/unit/test_vector_stores.py:416`` and the differential half in
``tests/integration/test_citations.py:417``, each gated on a real
``OPSPILOT_DATABASE_URL`` -- and the duplicate differential stub was deleted rather
than left to read as coverage (``.github/workflows/ci.yml``, ``verify`` job).

The marker is the casualty too. ``pytest -m postgres`` is how the ``verify`` job
selects the vector-store tests and asserts the marked selection is skip-free; a stub
wearing the marker turns that selection into a check on a name.

## What is asserted

Every function under ``tests/`` whose name starts with ``test`` and which carries at
least one marker must have a body that can fail:

1. a body that is only ``...``, or only ``pass``, or only a docstring, is a stub;
2. a body whose *only* statement is a top-level unconditional ``pytest.skip(...)``
   is a stub;
3. a ``pytest.skip`` **inside an ``if``** is allowed. That is an environment gate --
   ``if factory is None: pytest.skip("no PostgreSQL configured")`` -- the shape
   ADR-0004 requires of every Postgres-marked test on a machine with no Docker. The
   gate is the test's contract with the machine it runs on; the stub is the absence
   of anything to gate. A docstring followed by real statements is fine for the same
   reason: prose about the work is not the work, and it is the work that is counted.

Markers are found generically -- any decorator whose dotted name ends with
``.mark.<name>`` -- so a marker added to ``pyproject.toml`` tomorrow is covered the
day it is used, and no list of known markers has to be maintained here.

## Why the AST is read and the tests are not run

The sweep is pure ``ast``: no database, no network, no provider key, and no import of
the suite it inspects. That is what lets it run on the machine where the defect was
found -- no Docker, no Postgres, every marked test skipped, which is exactly where a
stub hides. It reads the *structure* of each body rather than its outcome, because
the outcome of a stub is a pass.

## What is deliberately exempt

``KNOWN_M0_SKELETONS`` names the three M0 placeholders, each carrying
``@pytest.mark.skip(reason="M0 skeleton -- implemented in Mx")`` with the milestone
recorded in its own module docstring. They are listed here rather than ignored so the
decision is recorded and dated, the way ``test_mypy_configuration_is_enforced.py``
lists ``EXPECTED_CHECKED_ROOTS`` rather than reading them from the config it checks.
The exemption is not permanent:
``test_the_m0_skeleton_allowlist_still_shields_something`` goes red the moment an
entry stops naming a stub, so a milestone that lands has to delete its entry rather
than leave a hole in this guard behind it.

## What this file does not do

It does not prove a marked test asserts anything. A body that calls a function and
discards the result still cannot fail, and this guard will not see it -- that is the
next level in, the same way ``docs/progress.md`` records for the settings family.
What it proves is the weaker and load-bearing property: the body is not empty, not a
no-op, and not an unconditional refusal to run. And because a guard nobody has
watched refuse is a guard nobody knows works,
``test_the_detector_refuses_every_stub_shape_it_names`` pins the classifier against
the shapes it names, on source parsed in memory.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

import pytest

#: The tree this guard sweeps. Marked tests live in ``tests/unit``,
#: ``tests/integration``, ``tests/evals`` and ``tests/agent``, so a sweep that named
#: a subset would be the mypy ``files`` defect again -- a narrower path than the
#: thing it claims to check, passing because nobody looked at what it left out.
TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_ROOT.parent

#: The M0 placeholders, keyed ``<path relative to the repository>::<test name>`` and
#: valued by the milestone that will land them. Each is a module created before the
#: code it tests existed, each says so in its own docstring, and each is reported as
#: SKIPPED with its reason in the ``-ra`` summary rather than as a pass.
#:
#: Listing them is the decision; ignoring them silently is the alternative this
#: repository has already been burned by, which is why
#: ``test_mypy_configuration_is_enforced.py`` spells out
#: ``EXPECTED_CHECKED_ROOTS`` instead of reading it from the config it checks. An
#: entry here is a debt with a milestone number on it: when M2, M4 or M8 lands the
#: body becomes real, and ``test_the_m0_skeleton_allowlist_still_shields_something``
#: fails until the entry is deleted.
KNOWN_M0_SKELETONS: dict[str, str] = {
    # "Placeholder: the eval harness runs end to end on the fake provider. M8."
    "tests/evals/test_harness.py::test_fake_provider_run_produces_a_metric_table": "M8",
    # "Placeholder: direct MCP idempotency test, no agent. Populated in M2."
    "tests/integration/test_refund_idempotency.py"
    "::test_same_idempotency_key_twice_produces_one_refund": "M2",
    # "Placeholder: unit tests for the agent's Pydantic schemas. Populated in M4."
    "tests/unit/test_schemas.py::test_issue_refund_without_idempotency_key_fails_validation": "M4",
}

#: Bodies that cannot fail, as ``(shape, source)`` pairs parsed in memory. The sweep
#: is only as good as the classifier it runs, so the classifier is pinned against
#: these rather than trusted: if ``_why_it_cannot_fail`` returned ``None`` for all of
#: them, the sweep would pass on a tree of stubs and this file would be the exact
#: defect it exists to hunt.
REFUSED_SHAPES: tuple[tuple[str, str], ...] = (
    ("ellipsis body", "def test_x() -> None: ..."),
    ("pass body", "def test_x() -> None:\n    pass"),
    ("docstring then ellipsis", 'def test_x() -> None:\n    """Arrives in M3."""\n    ...'),
    ("docstring alone", 'def test_x() -> None:\n    """Arrives in M3."""'),
    ("unconditional skip", "def test_x() -> None:\n    pytest.skip('M3')"),
    (
        "docstring then unconditional skip",
        'def test_x() -> None:\n    """Arrives in M3."""\n    pytest.skip("M3")',
    ),
    ("skip imported bare", "def test_x() -> None:\n    skip('M3')"),
)

#: Bodies that must NOT be reported as stubs. The first two are the ADR-0004
#: environment gate, and the reason the rule is about *unconditional* skips: a guard
#: that refused these would be red on every machine without Docker, and a guard
#: everybody learns to silence guards nothing.
ALLOWED_SHAPES: tuple[tuple[str, str], ...] = (
    ("gate only", "def test_x() -> None:\n    if _gate():\n        pytest.skip('no db')"),
    (
        "gate then work",
        "def test_x() -> None:\n    if _gate():\n        pytest.skip('no db')\n    assert 1",
    ),
    ("docstring then assertion", 'def test_x() -> None:\n    """Real work."""\n    assert 1'),
    ("loop over cases", "def test_x() -> None:\n    for case in (1, 2):\n        assert case"),
)


@dataclass(frozen=True)
class MarkedTest:
    """One test function that carries at least one pytest marker."""

    path: pathlib.Path
    name: str
    line: int
    markers: tuple[str, ...]
    body: tuple[ast.stmt, ...]

    @property
    def location(self) -> str:
        """``path:line`` relative to the repository -- paste-ready for an editor."""
        return f"{self.path.relative_to(REPO_ROOT).as_posix()}:{self.line}"

    @property
    def key(self) -> str:
        """``path::name`` -- how :data:`KNOWN_M0_SKELETONS` names this test.

        Keyed on the name rather than the line, because a line number moves the
        moment anything above it is edited, and a stale exemption is a hole.
        """
        return f"{self.path.relative_to(REPO_ROOT).as_posix()}::{self.name}"


def _dotted_name(node: ast.expr) -> str:
    """``pytest.mark.postgres`` for that expression; ``""`` if it is not a name.

    Attribute chains are walked to the ``ast.Name`` at their root, so both
    ``pytest.mark.postgres`` and the ``pytest.mark.postgres`` inside
    ``pytest.mark.postgres()`` resolve. Anything that is not a plain attribute chain
    (a subscript, the result of a call) is ``""``, which is what keeps the marker
    test below conservative rather than clever.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ""
    parts.append(current.id)
    return ".".join(reversed(parts))


def _marker_name(decorator: ast.expr) -> str | None:
    """The marker a decorator applies, or ``None`` if it applies none.

    A marker is a decorator whose dotted name ends with ``.mark.<name>``:
    ``pytest.mark.postgres``, ``pytest.mark.skip(reason=...)`` and
    ``pytest.mark.parametrize(...)`` all qualify, and ``pytest.fixture`` does not.
    The tail is matched rather than the literal ``pytest.mark`` prefix so a module
    that imports the mark namespace directly is still seen -- and so the rule covers
    every marker in ``pyproject.toml`` plus any added after it, which a hardcoded
    ``{"postgres", "live", "slow"}`` would not.
    """
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    parts = _dotted_name(target).split(".")
    if len(parts) < 3 or parts[-2] != "mark":
        return None
    return parts[-1]


def _is_docstring(statement: ast.stmt) -> bool:
    """Is this statement a bare string literal -- the docstring position?"""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_ellipsis(statement: ast.stmt) -> bool:
    """Is this statement the bare ``...`` placeholder?"""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _is_skip_call(statement: ast.stmt) -> bool:
    """Is this statement a bare ``<...>.skip(...)`` call and nothing else?

    Only ever passed a *top-level* statement, which is what keeps
    ``if factory is None: pytest.skip(...)`` out of it: that skip is a child of an
    ``ast.If``, and the gate around it is the test's contract with the machine it
    runs on rather than a refusal to do any work.
    """
    if not isinstance(statement, ast.Expr):
        return False
    value = statement.value
    if not isinstance(value, ast.Call):
        return False
    return _dotted_name(value.func).rsplit(".", maxsplit=1)[-1] == "skip"


def _why_it_cannot_fail(body: tuple[ast.stmt, ...]) -> str | None:
    """Why this body can never fail, or ``None`` if it can.

    A leading docstring is dropped first: prose describing the work is not the work,
    and a docstring promising that "real behaviour arrives in M3" above a ``...`` is
    the stub with better cover.

    The rule is deliberately narrow -- *one* statement, and that statement is a no-op.
    Two statements are evidence of work even when one of them is a skip, because the
    second has to be reachable on some machine.
    """
    statements = list(body)
    if statements and _is_docstring(statements[0]):
        statements = statements[1:]
    if not statements:
        return "its body is a docstring and no statement at all"
    if len(statements) > 1:
        return None

    only = statements[0]
    if isinstance(only, ast.Pass):
        return "its body is `pass`"
    # `...` is the ellipsis placeholder. Any other bare constant asserts nothing
    # either, but `...` is the shape this repository's placeholders take, so it is
    # the shape that is named.
    if _is_ellipsis(only):
        return "its body is `...`"
    if _is_skip_call(only):
        return "its only statement is an unconditional `pytest.skip(...)`"
    return None


def _sole_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The one function ``source`` defines, parsed in memory.

    Raises:
        AssertionError: If the sample defines no function, which would make the
            pin below pass for the wrong reason.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    raise AssertionError(f"no function in the sample source: {source!r}")


def _marked_tests() -> list[MarkedTest]:
    """Every marked test function under ``tests/``, in file and line order.

    Sorted, so a failure lists the same tests in the same order on every machine --
    a guard whose output depends on ``rglob`` order cannot be diffed between runs.
    """
    found: list[MarkedTest] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test"):
                continue
            markers = tuple(
                marker
                for marker in (_marker_name(decorator) for decorator in node.decorator_list)
                if marker is not None
            )
            if markers:
                found.append(
                    MarkedTest(
                        path=path,
                        name=node.name,
                        line=node.lineno,
                        markers=markers,
                        body=tuple(node.body),
                    )
                )
    return found


def test_no_marked_test_is_a_stub() -> None:
    """No marked test in the tree has a body that cannot fail.

    Applied to the whole tree at once rather than test by test, because the defect is
    never "this one test": it is a shape that spreads on the day a module is created
    before the code it tests exists, and all three recorded instances were written on
    exactly that day.

    The failure message names every offending test rather than the first, because
    placeholders are written in batches.
    """
    marked = _marked_tests()
    assert marked, (
        f"the sweep found no marked tests under {TESTS_ROOT}. A guard over zero "
        "subjects passes whatever the tree contains -- the vacuous green this file "
        "exists to prevent. Check the sweep, not the suite."
    )

    problems: list[str] = []
    for test in marked:
        reason = _why_it_cannot_fail(test.body)
        if reason is None or test.key in KNOWN_M0_SKELETONS:
            continue
        problems.append(
            f"{test.location}: {test.name} is marked {list(test.markers)} and {reason}. "
            "A marker is a claim that this test exercises something real, and a body "
            "that cannot fail keeps no claim: it is collected, counted and reported "
            "green while proving nothing. That is not theoretical -- ADR-0004 "
            "Correction 2 is two @pytest.mark.postgres tests whose entire bodies were "
            "`pytest.skip(...)`, counted toward a green run on every machine "
            "including CI's. Write the body, or delete the test: a name with no body "
            "reads as coverage and is not. If the placeholder is deliberate, add it "
            "to KNOWN_M0_SKELETONS with the milestone that will land it."
        )

    assert not problems, "these marked tests can never fail:\n  " + "\n  ".join(problems)


def test_the_m0_skeleton_allowlist_still_shields_something() -> None:
    """Every exemption still names a stub, so the list cannot rot into a hole.

    An exemption for a test that no longer exists, or one left behind after its
    milestone landed, is worse than no exemption: it reads as a checked decision
    while checking nothing. Both directions are asserted -- missing and already
    implemented -- so the list can only stay true or shrink.
    """
    marked = {test.key: test for test in _marked_tests()}
    problems: list[str] = []
    for key, milestone in sorted(KNOWN_M0_SKELETONS.items()):
        test = marked.get(key)
        if test is None:
            problems.append(
                f"{key} is listed in KNOWN_M0_SKELETONS but no such marked test exists "
                "under tests/ -- it was renamed, moved or deleted. Remove the entry: an "
                "exemption for a test that is gone is a line nobody reads, and it "
                "tells the next reader this shape was reviewed when it was not."
            )
        elif _why_it_cannot_fail(test.body) is None:
            problems.append(
                f"{test.location}: {test.name} now has a body that can fail, so its "
                f"KNOWN_M0_SKELETONS entry ({milestone}) shields nothing. Remove it. "
                "The milestone landed, and an exemption left behind after the work is "
                "done is how a guard that used to check this shape stops checking it, "
                "silently."
            )

    assert not problems, "the M0 skeleton allowlist is stale:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize(
    ("shape", "source"),
    REFUSED_SHAPES,
    ids=[shape for shape, _ in REFUSED_SHAPES],
)
def test_the_detector_refuses_every_stub_shape_it_names(shape: str, source: str) -> None:
    """The classifier refuses each shape it names -- it is not vacuous.

    The sweep is only as good as ``_why_it_cannot_fail``. If that function returned
    ``None`` for everything, ``test_no_marked_test_is_a_stub`` would pass on a tree of
    stubs, and this file would be the very defect it hunts: a green that means
    nothing. So the shapes are pinned here, on source parsed in memory, with no file
    written and no suite to run.
    """
    function = _sole_function(source)
    assert _why_it_cannot_fail(tuple(function.body)) is not None, (
        f"the {shape} shape is not detected as a stub ({source!r}). A stub this guard "
        "cannot see is a stub it reports green, which is the failure this file exists "
        "to prevent."
    )


@pytest.mark.parametrize(
    ("shape", "source"),
    ALLOWED_SHAPES,
    ids=[shape for shape, _ in ALLOWED_SHAPES],
)
def test_the_detector_allows_the_shapes_it_must_not_refuse(shape: str, source: str) -> None:
    """An environment gate is not a stub, and must not be reported as one.

    The other half of a classifier worth trusting. A detector that refused
    ``if factory is None: pytest.skip(...)`` would be red on every machine without
    Docker, on the exact shape ADR-0004 *requires* of the Postgres-marked tests.
    """
    function = _sole_function(source)
    assert _why_it_cannot_fail(tuple(function.body)) is None, (
        f"the {shape} shape is reported as a stub ({source!r}). It is an environment "
        "gate, not a placeholder: ADR-0004 asks every Postgres-marked test to skip "
        "where no database is configured, and refusing that shape makes this guard "
        "red on every machine without Docker."
    )
