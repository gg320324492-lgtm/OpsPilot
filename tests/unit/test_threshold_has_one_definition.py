"""``RETRIEVAL_MIN_SCORE`` must have exactly one definition.

The abstention threshold used to be a literal in three places:
``settings.py`` (the real default), ``agents/runtime.py`` and
``worker/loop.py``. Moving it in one left the other two stale, and the result
was the defect M6a fixed and this file keeps fixed: a caller that relied on the
default rather than passing the setting abstained on a threshold the deployment
had already lowered. Measured before the fix, on the golden-path question:

    settings (0.22)   top=0.3355  abstained=False
    runtime  (0.35)   top=0.3355  abstained=True

The same query, the same corpus, two answers -- decided by which copy of the
number the caller happened to reach.

This is the same shape as the M2 bare tool names and the M5 ingest probe: a
value *copied* where it should have been *referenced*. A guard is the only thing
that stops the fourth copy appearing, because nothing else fails when one does.

## The second half of the property

A value that is *referenced* can still be frozen at the wrong moment. A
module-level constant -- even one assigned from the setting -- is resolved when
the module is imported, so it cannot follow a setting an operator moves
afterwards, and it is a second place a copy of the number can live.

So the property this file keeps is two-sided, and it covers **three** modules
now: ``agents/runtime.py``, ``worker/loop.py`` and
``adapters/retrieval/search.py``. Each resolves the threshold through a helper
that reads the settings, at the point of use; none of them holds a threshold
constant at module level.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from opspilot.settings import get_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"

# Modules that legitimately hold a *reference* to the setting. Anything outside
# this set that mentions the threshold is a second definition.
_ALLOWED = frozenset({"settings.py", "api/app.py"})

#: The modules that resolve the threshold, and the helper each resolves it
#: through. Every one of them must have no threshold constant of its own, and
#: must read the setting when the threshold is needed -- not at import.
_RESOLVERS: tuple[tuple[str, str], ...] = (
    ("agents/runtime.py", "_settings_min_score"),
    ("worker/loop.py", "_settings_retrieval_min_score"),
    ("adapters/retrieval/search.py", "_default_min_score"),
)


def _threshold_constants() -> list[tuple[pathlib.Path, int, float]]:
    """Every float constant in ``src/`` whose name looks like the threshold.

    Found by parsing rather than by grepping so a value spelled differently
    (``0.35``, ``3.5e-1``) is still caught, and so a *name* match without a
    literal -- the fix, which reads the setting -- does not match.
    """
    found: list[tuple[pathlib.Path, int, float]] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # ``stmt`` is the only node kind carrying ``lineno``; ``ast.Assign`` and
        # ``ast.AnnAssign`` are both subclasses, so narrowing once here keeps the
        # walk below free of casts.
        for node in ast.walk(tree):
            targets: list[str] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    targets = [node.targets[0].id]
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    targets = [node.target.id]
                value = node.value
            else:
                continue
            if value is None or not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, float):
                continue
            if not any("MIN_SCORE" in name or "min_score" in name for name in targets):
                continue
            found.append((path, node.lineno, value.value))
    return found


def _module_level_threshold_names(module_path: str) -> list[str]:
    """Every module-level name in ``module_path`` that looks like the threshold.

    Module level only, and that is the point: a name assigned at module scope
    is resolved when the module is imported, so it is frozen at import and
    cannot follow a setting an operator moves afterwards. A name assigned
    inside a function is resolved when the function runs, and cannot go stale
    that way.
    """
    source = (SRC / module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)
    found: list[str] = []
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        found.extend(name for name in targets if "MIN_SCORE" in name or "min_score" in name)
    return found


def _calls_to(module_path: str, name: str) -> list[ast.Call]:
    """Every call to ``name`` anywhere in ``module_path``."""
    source = (SRC / module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _module_level_calls(module_path: str, name: str) -> list[int]:
    """The line numbers of the calls to ``name`` made at module level.

    A call at module level is a call at import time, which is what freezes a
    value.
    """
    source = (SRC / module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)
    lines: list[int] = []
    for node in tree.body:
        # A function body runs when the function is called, not at import, so
        # the calls inside it are not module-level calls.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == name
            ):
                lines.append(child.lineno)
    return lines


def _function_source(module_path: str, name: str) -> str | None:
    """The source of the function ``name``, or ``None`` if it is not there."""
    source = (SRC / module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node)
    return None


def test_the_threshold_is_defined_once_in_the_source() -> None:
    """No module under ``src/`` may carry its own float for the threshold."""
    offenders = [
        (str(path.relative_to(REPO_ROOT)), line, value)
        for path, line, value in _threshold_constants()
        if path.name not in _ALLOWED
    ]
    assert not offenders, (
        "RETRIEVAL_MIN_SCORE is defined as a literal in more than one place: "
        f"{offenders}. Each copy is a value a deployment can move in one place "
        "and not the other, and the symptom is a run that abstains or answers "
        "depending on which module the caller reached. Reference "
        "Settings.retrieval_min_score instead -- see "
        "agents.runtime._settings_min_score."
    )


def test_the_resolvers_are_the_modules_this_file_claims_to_cover() -> None:
    """The three modules named in the module docstring are the ones checked.

    An empty ``_RESOLVERS`` would make the two parametrised tests below
    *skip* rather than fail -- pytest reports an empty parameter set as a
    skip -- and the loop in ``test_the_setting_is_what_the_helpers_return``
    would run zero times. The file would then be green while checking
    nothing, which is the state this file's own docstring says it exists to
    prevent.

    A list that had quietly lost a module would be worse in a quieter way:
    that module's threshold would stop being guarded while this file still
    claimed to cover it.
    """
    assert _RESOLVERS, (
        "no resolver is listed. The two parametrised tests below would be "
        "skipped rather than fail, and the loop in "
        "test_the_setting_is_what_the_helpers_return would run zero times."
    )
    covered = {module_path for module_path, _helper in _RESOLVERS}
    expected = {
        "agents/runtime.py",
        "worker/loop.py",
        "adapters/retrieval/search.py",
    }
    assert covered == expected, (
        f"the resolvers are {sorted(covered)}, but this file claims to cover "
        f"{sorted(expected)}. A module that has dropped out of the list is a "
        "module whose threshold is no longer guarded."
    )
    for module_path, _helper in _RESOLVERS:
        assert (SRC / module_path).is_file(), f"{module_path} does not exist"


def test_the_setting_is_what_the_helpers_return() -> None:
    """Every resolver returns the configured value, not a remembered one."""
    import importlib

    configured = get_settings().retrieval_min_score
    for module_path, helper in _RESOLVERS:
        body = _function_source(module_path, helper)
        assert body is not None, f"{module_path} no longer defines {helper}"
        assert "get_settings()" in body, f"{helper} must read the threshold through get_settings()"
        assert "retrieval_min_score" in body, f"{helper} must read Settings.retrieval_min_score"
        dotted = module_path.removesuffix(".py").replace("/", ".")
        module = importlib.import_module(f"opspilot.{dotted}")
        assert getattr(module, helper)() == configured, (
            f"{helper}() does not return the configured threshold"
        )


@pytest.mark.parametrize(("module_path", "helper"), _RESOLVERS)
def test_no_module_holds_a_threshold_constant(module_path: str, helper: str) -> None:
    """A threshold constant at module scope is frozen at import.

    Not even one derived from the setting: a module-level assignment is
    resolved when the module is imported, so it cannot follow a setting an
    operator moves afterwards, and it is a second place a copy of the number
    can live. The module reads the setting at the point of use instead.
    """
    offenders = _module_level_threshold_names(module_path)
    assert not offenders, (
        f"{module_path} defines {offenders} at module level. A threshold constant "
        "at module scope is frozen at import, so it cannot follow a setting an "
        "operator moves afterwards, and it is a second copy of the number to "
        f"keep in step. Resolve the threshold where it is used instead -- {helper}()."
    )


@pytest.mark.parametrize(("module_path", "helper"), _RESOLVERS)
def test_each_module_resolves_the_threshold_when_it_is_used(module_path: str, helper: str) -> None:
    """The threshold is read at the point of use, never at import.

    Three things, and each is load-bearing:

    * the helper exists and reads ``get_settings().retrieval_min_score`` -- a
      call into the settings, not a number;
    * it is called from inside a function -- so the value is resolved when the
      run needs it;
    * it is **not** called at module level -- so nothing about the threshold is
      resolved when the module is imported.

    The last one is the difference between "the threshold has one definition"
    and "the threshold has one definition, frozen at import".
    """
    uses = _calls_to(module_path, helper)
    assert uses, f"{module_path} never calls {helper}(); the threshold is never read"

    at_import = _module_level_calls(module_path, helper)
    assert not at_import, (
        f"{module_path} calls {helper}() at module level (line(s) "
        f"{at_import}), which resolves the threshold at import time and freezes "
        "it. Call it where the threshold is needed instead."
    )
