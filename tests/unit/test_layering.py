"""The dependency rule, enforced as a test rather than as a convention.

``docs/architecture.md`` §3 states that dependencies point downward only:
``domain/`` must not know that FastAPI, SQLAlchemy, MCP or LangGraph exist, and
nothing above the adapter layer may import an adapter. Without a test, that rule
holds until the first person in a hurry writes ``from sqlalchemy import select``
at the top of ``domain/policies.py`` -- and the reason the rule exists is that it
is *not* obvious at the moment of the violation.

This is a static check over the source text, not an import-time check, because
the failure it prevents is a module that imports cleanly today and couples the
layers silently. An import-time check would only fire when the module is loaded
*and* the dependency happens to be missing.

Implemented now (M0) even though ``domain/`` is mostly stubs: the rule is easiest
to keep once it has never been broken, and this test is what makes the M1-M6
work keep it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "opspilot"

# What each layer may not import, and why the restriction is not arbitrary.
FORBIDDEN: dict[str, frozenset[str]] = {
    # The domain is pure business logic: no I/O, no ORM, no framework. This is
    # what makes the state machine and the permission table testable in
    # microseconds and what makes replacing LangGraph a change to one adapter.
    "domain": frozenset(
        {
            "sqlalchemy",
            "fastapi",
            "mcp",
            "langgraph",
            "httpx",
            "openai",
            "anthropic",
            "alembic",
            "opspilot.adapters",
            "opspilot.api",
            "opspilot.worker",
        }
    ),
    # Ports are Protocol definitions. A port that imports an implementation is
    # not a port.
    "ports": frozenset(
        {
            "sqlalchemy",
            "fastapi",
            "mcp",
            "langgraph",
            "httpx",
            "openai",
            "anthropic",
            "opspilot.adapters",
            "opspilot.api",
            "opspilot.worker",
        }
    ),
    # The agent runtime orchestrates through ports. It may not reach for a
    # concrete adapter -- that is the inversion the whole design rests on, and
    # the LangGraph adapter is the case that would tempt a violation.
    "agents": frozenset(
        {
            "sqlalchemy",
            "fastapi",
            "mcp",
            "langgraph",
            "opspilot.adapters",
        }
    ),
}

# The MCP SDK and the provider SDKs are allowed ONLY in these adapter modules,
# and inside them only as function-local imports. A module-level `import mcp`
# would make the package unimportable without the SDK, which would break the
# property that a fresh clone with no extras can still run the test suite.
FUNCTION_LOCAL_ONLY = frozenset({"openai", "anthropic"})


def _modules(layer: str) -> list[pathlib.Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def _imported_roots(path: pathlib.Path) -> set[tuple[str, int, bool]]:
    """Return ``(module_name, line, is_module_level)`` for every import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[tuple[str, int, bool]] = set()

    # Module-level imports: walk only the top-level statements, so an import
    # inside a function body is classified as function-local.
    module_level_lines: set[int] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            module_level_lines.add(stmt.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno, node.lineno in module_level_lines))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add((node.module, node.lineno, node.lineno in module_level_lines))
    return found


def _violates(module_name: str, forbidden: frozenset[str]) -> str | None:
    """Which forbidden root ``module_name`` falls under, if any."""
    for bad in forbidden:
        # Match the root exactly or as a package prefix, so that
        # `opspilot.adapters.persistence` is caught by `opspilot.adapters` and
        # `sqlalchemy.orm` by `sqlalchemy`.
        if module_name == bad or module_name.startswith(bad + "."):
            return bad
    return None


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_imports_nothing_forbidden(layer: str) -> None:
    """No module in ``layer`` imports a package it must not depend on."""
    violations: list[str] = []

    for path in _modules(layer):
        for module_name, line, _ in sorted(_imported_roots(path)):
            bad = _violates(module_name, FORBIDDEN[layer])
            if bad is not None:
                rel = path.relative_to(PACKAGE_ROOT.parent.parent)
                violations.append(f"{rel}:{line} imports {module_name} (forbidden root: {bad})")

    assert not violations, (
        "Layering violation in "
        f"{layer}/ -- dependencies must point downward only "
        f"(docs/architecture.md §3):\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("sdk", sorted(FUNCTION_LOCAL_ONLY))
def test_provider_sdk_is_imported_lazily(sdk: str) -> None:
    """The provider SDKs are never imported at module scope.

    A module-level ``import anthropic`` would make
    ``opspilot.adapters.models.anthropic_provider`` unimportable without the SDK
    installed, and because the adapter package is walked by the test suite that
    would take the whole suite down on a machine with no extras. The import
    lives inside the method that uses it, so the module imports cleanly and only
    fails if the provider is actually selected.
    """
    offenders: list[str] = []

    for path in PACKAGE_ROOT.rglob("*.py"):
        for module_name, line, is_module_level in sorted(_imported_roots(path)):
            if module_name.split(".")[0] != sdk:
                continue
            if is_module_level:
                rel = path.relative_to(PACKAGE_ROOT.parent.parent)
                offenders.append(f"{rel}:{line} imports {module_name} at module scope")

    assert not offenders, (
        f"'{sdk}' must be imported inside a function, never at module level, so the "
        f"package imports without the SDK installed:\n  " + "\n  ".join(offenders)
    )


def test_every_layer_directory_exists() -> None:
    """The layers this test guards are the ones that actually exist.

    Without this, renaming a directory would make every parametrised case above
    vacuous -- a guard that passes because it inspected nothing is worse than a
    failing one, because the pass is the part people read.
    """
    missing = [layer for layer in FORBIDDEN if not (PACKAGE_ROOT / layer).is_dir()]
    assert not missing, f"layers named in FORBIDDEN but absent from the tree: {missing}"

    for layer in FORBIDDEN:
        assert list(_modules(layer)), f"{layer}/ contains no Python modules to check"
