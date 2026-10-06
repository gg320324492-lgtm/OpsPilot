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


def test_the_runtime_and_worker_defaults_follow_settings() -> None:
    """Both runtime defaults equal the setting, not a remembered literal."""
    from opspilot.agents.runtime import _DEFAULT_RETRIEVAL_MIN_SCORE
    from opspilot.worker.loop import _DEFAULT_MIN_SCORE

    configured = get_settings().retrieval_min_score
    assert configured == _DEFAULT_RETRIEVAL_MIN_SCORE
    assert configured == _DEFAULT_MIN_SCORE


@pytest.mark.parametrize(
    ("module_path", "attribute"),
    [
        ("opspilot/agents/runtime.py", "_DEFAULT_RETRIEVAL_MIN_SCORE"),
        ("opspilot/worker/loop.py", "_DEFAULT_MIN_SCORE"),
    ],
)
def test_each_default_is_derived_from_settings_not_written(
    module_path: str, attribute: str
) -> None:
    """The module reaches the setting rather than restating its value.

    Asserted structurally rather than by value: a test comparing two numbers
    passes when both are stale in the same way, which is exactly what happened.
    This reads the module's source and requires that the assignment to
    ``attribute`` is a call, not a number.
    """
    source = (REPO_ROOT / "src" / module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and attribute in ast.dump(node)
        and isinstance(node.value, ast.Call)
    ]
    assert calls, (
        f"{module_path} must assign {attribute} from a call that reads "
        f"Settings.retrieval_min_score, not from a literal"
    )
    assert "get_settings" in source, (
        f"{module_path} reads the threshold without consulting settings"
    )
