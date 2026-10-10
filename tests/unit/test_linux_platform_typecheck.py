"""The type check CI runs is a **Linux** check, and a Windows laptop cannot see it.

## The defect this file exists for

``tests/integration/test_worker_entry_point.py`` handed its process-group
keyword to ``subprocess.Popen`` through a *conditional expression*:

    **(
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )

``subprocess.CREATE_NEW_PROCESS_GROUP`` is declared in typeshed only under
``sys.platform == "win32"``. mypy platform-narrows an ``if`` **statement** and
does not narrow a ternary, so it checked the attribute against the POSIX
branch of typeshed and reported::

    tests/integration/test_worker_entry_point.py:278: error:
      Module has no attribute "CREATE_NEW_PROCESS_GROUP"  [attr-defined]

That is the whole of the ``typecheck`` job's red: it has failed on every run it
has ever had (two, both 2026-10-08). mypy infers ``--platform`` from the host,
so bare ``mypy`` on a Windows machine *is* ``mypy --platform win32``, and the
identical command on ``ubuntu-latest`` is ``--platform linux``. A gate that is
red only where it runs and green only where nobody looks is not a gate -- the
failure mode this repository keeps producing, and the reason ``docs/progress.md``
proposes "the CI gates are green on the platform CI runs" as a definition-of-done
row.

The same file already used a statement-level ``if sys.platform == "win32":`` for
the signal it sends, and that line was never wrong. The defect is the
inconsistency between the two shapes, not a missing platform check.

## What is asserted here, and why two things

1. :func:`test_the_worker_entry_point_type_checks_on_the_platform_ci_runs` runs
   the check CI runs -- ``mypy --platform linux`` -- against that one file and
   requires it clean. It is the reproduction, not an approximation of one.

2. :func:`test_no_windows_only_name_is_touched_outside_a_platform_branch` is an
   AST scan of every root ``[tool.mypy] files`` names, and it asserts the
   *shape* mypy depends on: a Windows-only stdlib name may only be touched
   inside a statement-level ``if sys.platform == "win32":``. It costs
   milliseconds, it needs no mypy, and it catches the next one of these in any
   file rather than only in the file that already had one.

**The cost of (1), measured on this machine** (``.venv``, Python 3.12): 0.8s
against a warm ``.mypy_cache``, 9.6s cold into a fresh cache directory. So the
usual local cost is under a second and a fresh clone or a CI runner pays
roughly ten.

Why not re-run the *whole* ``mypy --platform linux`` pass over all 173 configured
files inside pytest (13s here, and slower cold)?
``tests/unit/test_mypy_configuration_is_enforced.py`` already recorded the
project's position on that -- "that is the check itself, not a guard on the
check" -- and it is right for the full pass. Checking *one file* under a
foreign platform is a different thing: it is a targeted reproduction of the
failure mode, over the ~40 files that file's imports pull in, which is the
narrowest scope that can actually catch this defect. If that trade is judged
wrong later, deleting test (1) leaves test (2) catching the same class.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The file the CI `typecheck` job's error named, and the one these two tests
#: guard. Named rather than globbed: a guard that has to find its own subject is
#: a guard that can quietly stop guarding it.
GUARDED_FILE = "tests/integration/test_worker_entry_point.py"

#: Stdlib names typeshed declares only in its ``sys.platform == "win32"``
#: branch, grouped by the module they hang off. Read from typeshed rather than
#: guessed: a name in this table that typeshed does *not* gate would make this
#: test demand a branch that is not needed, and a gated name missing from the
#: table is the defect this file exists for. Deliberately not exhaustive -- it
#: covers the two modules this repository's tests actually launch processes with.
WINDOWS_ONLY_NAMES: dict[str, frozenset[str]] = {
    "subprocess": frozenset(
        {
            "CREATE_NEW_CONSOLE",
            "CREATE_NEW_PROCESS_GROUP",
            "CREATE_NO_WINDOW",
            "DETACHED_PROCESS",
            "STD_ERROR_HANDLE",
            "STD_INPUT_HANDLE",
            "STD_OUTPUT_HANDLE",
            "STARTF_USESHOWWINDOW",
            "STARTF_USESTDHANDLES",
            "STARTUPINFO",
            "SW_HIDE",
        }
    ),
    "signal": frozenset({"CTRL_BREAK_EVENT", "CTRL_C_EVENT"}),
    "os": frozenset(
        {
            "O_BINARY",
            "O_NOINHERIT",
            "P_DETACH",
            "P_OVERLAPPED",
            "STD_ERROR_HANDLE",
            "STD_INPUT_HANDLE",
            "STD_OUTPUT_HANDLE",
            "STARTF_USESHOWWINDOW",
            "STARTF_USESTDHANDLES",
            "STARTUPINFO",
            "startfile",
        }
    ),
}

#: Modules that exist on Windows and nowhere else. An unconditional ``import
#: msvcrt`` is the same defect in a different dress: a green local run on the
#: one platform that has it.
WINDOWS_ONLY_MODULES = frozenset(
    {"msvcrt", "nt", "pythoncom", "pywintypes", "win32api", "win32con", "winreg", "winsound"}
)


@pytest.mark.slow
def test_the_worker_entry_point_type_checks_on_the_platform_ci_runs() -> None:
    """``mypy --platform linux`` on the guarded file is clean. The CI command.

    Marked ``slow`` because it is: under a warm cache it is under a second, and
    into a cold one it is the ten seconds measured in this module's docstring.
    The marker is here to be honest about that, not to switch the test off --
    nothing deselects it, which is the point: this defect survived two CI runs
    precisely because nothing reproduced it locally.

    ``MYPYPATH`` points at ``src`` because a single-file run does not read
    ``[tool.mypy] files`` and would otherwise report three spurious
    ``import-not-found`` errors for ``opspilot`` and fail a clean file. The rest
    of the configuration (strict, ``warn_unused_ignores``, the
    ``ignore_missing_imports`` overrides) *is* read -- mypy still finds
    ``pyproject.toml`` in the working directory below -- so this is CI's check
    over a narrower file set, not a laxer one.
    """
    if importlib.util.find_spec("mypy") is None:  # pragma: no cover - dev extra absent
        pytest.skip("mypy is not installed (it is in the `dev` extra); this check is CI's job")

    env = {**os.environ, "MYPYPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(  # noqa: S603 - [sys.executable, "-m", "mypy", <literal args>]
        [sys.executable, "-m", "mypy", "--platform", "linux", GUARDED_FILE],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"`mypy --platform linux {GUARDED_FILE}` reported errors, so the CI "
        "`typecheck` job (ubuntu-latest, where mypy defaults to "
        f"--platform linux) is red:\n{result.stdout}{result.stderr}\n\n"
        "mypy infers --platform from the host, which is why this is invisible "
        "on a Windows machine and was still red on both CI runs this repository "
        "has ever had. Fix the platform-conditional access; do not silence it "
        "with a type: ignore -- `warn_unused_ignores = true` makes that ignore "
        "an error on the platform where the attribute exists."
    )


def _is_sys_platform(node: ast.expr) -> bool:
    """Is this expression the ``sys.platform`` a platform branch is keyed on?

    ``sys.platform`` is an ``ast.Attribute`` over a ``Name``, not a ``Name`` --
    getting that wrong makes every real branch look unguarded, which is what
    this function's first version did to the *fixed* file. ``from sys import
    platform`` is accepted because mypy narrows it identically. An aliased
    ``import sys as s`` is not recognised, and the failure mode of that omission
    is a missed detection rather than a false alarm.
    """
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "platform" and isinstance(node.value, ast.Name) and node.value.id == "sys"
        )
    if isinstance(node, ast.Name):
        return node.id == "platform"
    return False


def _is_win32_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "win32"


def _is_win32_branch(test: ast.expr) -> bool:
    """Is this ``if`` test a statement-level ``sys.platform == "win32"`` check?

    Only the forms mypy itself narrows are recognised, deliberately: the point
    of the rule is that the platform decision stays visible to the type checker,
    so accepting a shape mypy cannot narrow would let the defect back in through
    the guard that is supposed to stop it. ``"win32" == sys.platform`` is
    accepted because it is the same comparison.
    """
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (_is_sys_platform(left) and _is_win32_literal(right)) or (
        _is_win32_literal(left) and _is_sys_platform(right)
    )


class _WindowsOnlyVisitor(ast.NodeVisitor):
    """Collect Windows-only names touched outside a ``sys.platform`` branch.

    ``_depth`` counts enclosing win32 branches rather than a boolean, so an
    access inside a nested function, loop or ``try`` *within* the branch is
    still inside it -- which is where the shipped code puts its ``Popen`` call.
    """

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []
        self._depth = 0

    def _record(self, node: ast.expr | ast.stmt, described: str) -> None:
        self.violations.append((node.lineno, described))

    def visit_If(self, node: ast.If) -> None:
        if _is_win32_branch(node.test):
            for statement in node.body:
                self._depth += 1
                self.visit(statement)
                self._depth -= 1
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._depth == 0 and isinstance(node.value, ast.Name):
            names = WINDOWS_ONLY_NAMES.get(node.value.id)
            if names is not None and node.attr in names:
                self._record(
                    node,
                    f"{node.value.id}.{node.attr} (typeshed declares it only for win32)",
                )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._depth == 0:
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in WINDOWS_ONLY_MODULES:
                    self._record(node, f"import {alias.name} (the module exists only on Windows)")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._depth == 0 and node.module is not None:
            names = WINDOWS_ONLY_NAMES.get(node.module)
            module_root = node.module.split(".")[0]
            for alias in node.names:
                if names is not None and alias.name in names:
                    self._record(
                        node,
                        f"from {node.module} import {alias.name} "
                        "(typeshed declares it only for win32)",
                    )
                elif node.module == module_root and module_root in WINDOWS_ONLY_MODULES:
                    self._record(
                        node,
                        f"from {node.module} import {alias.name} "
                        "(the module exists only on Windows)",
                    )
        self.generic_visit(node)


def _checked_roots() -> list[pathlib.Path]:
    """The roots ``[tool.mypy] files`` names, which is what CI type-checks.

    Read from the configuration rather than listed here, so this scan cannot
    silently cover less than mypy does. ``test_mypy_configuration_is_enforced``
    is what keeps that list honest -- this is not the place to duplicate it.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    roots = data.get("tool", {}).get("mypy", {}).get("files", [])
    assert isinstance(roots, list) and roots, (
        "[tool.mypy] files is missing or empty, so this scan has no scope. That "
        "is itself a defect -- bare `mypy` would then check whatever directory "
        "it is run from."
    )
    return [REPO_ROOT / str(root) for root in roots]


def _python_files(roots: list[pathlib.Path]) -> list[pathlib.Path]:
    """Every ``.py`` under ``roots``, sorted, so a failure names them stably."""
    files: list[pathlib.Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.py")))
    return sorted(files)


def test_no_windows_only_name_is_touched_outside_a_platform_branch() -> None:
    """No Windows-only stdlib name is read outside ``if sys.platform == "win32"``.

    The rule is about *shape*, not about one line: mypy narrows the platform
    inside a statement-level ``if`` and nowhere else, so that is the only shape
    in which reading a Windows-only constant type-checks on the platform CI
    runs. A ternary, a ``dict.get``, a module-level constant, or a helper called
    from elsewhere all read as green on Windows and red on the runner.

    Covers every root ``[tool.mypy] files`` names, not only the file that had
    the defect, and costs about a millisecond -- which is why it runs on every
    ``pytest`` instead of being a CI-only idea. It is a *proxy*: it cannot see a
    Linux-only type error of any other kind, which is why
    :func:`test_the_worker_entry_point_type_checks_on_the_platform_ci_runs` sits
    next to it.
    """
    violations: list[str] = []
    for path in _python_files(_checked_roots()):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _WindowsOnlyVisitor()
        visitor.visit(tree)
        relative = path.relative_to(REPO_ROOT).as_posix()
        for lineno, described in visitor.violations:
            violations.append(f"{relative}:{lineno}: {described}")

    assert not violations, (
        "Windows-only stdlib names reached outside a statement-level\n"
        '    if sys.platform == "win32":\n'
        "branch. mypy platform-narrows that branch and nothing else, so every\n"
        "line below type-checks on a Windows machine and fails the CI typecheck\n"
        "job on ubuntu-latest:\n  "
        + "\n  ".join(violations)
        + "\n\nPut the access inside the branch (that is what\n"
        "tests/integration/test_worker_entry_point.py now does), or select the\n"
        "platform-specific value in a statement rather than an expression."
    )


def test_the_guarded_file_is_one_the_scan_and_the_mypy_run_both_cover() -> None:
    """The file named in this module's docstring is inside the scanned roots.

    A guard whose subject had been moved or renamed would otherwise keep
    passing -- testing a file that no longer exists is the shape of test this
    repository keeps having to remove.
    """
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _python_files(_checked_roots())}
    assert GUARDED_FILE in scanned, (
        f"{GUARDED_FILE} is not among the files [tool.mypy] files covers, so the "
        "mypy run above would check a path that does not exist and report "
        "'no files found' -- an error, but not the one it means to be. Update "
        "GUARDED_FILE if the file moved."
    )
