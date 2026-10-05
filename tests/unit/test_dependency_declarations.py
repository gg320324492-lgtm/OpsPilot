"""Every third-party package the code imports must be a declared dependency.

This test exists because the M1 persistence work shipped with a real defect of
exactly this shape: ``models.py`` imported ``pgvector`` at module scope while
``pgvector`` was absent from ``pyproject.toml``. It had been installed into the
development virtualenv by hand, so the suite passed locally -- and a fresh
``pip install -e .`` would have produced a package that could not import its own
persistence layer.

The failure is invisible from inside the development environment, which is what
makes it worth a guard rather than a note in a review. A dependency that only the
author's machine happens to have is a dependency the package does not really have.

Scope, stated so the test is not over-read: it checks *declared vs imported* for
the packages this project depends on directly. It does not resolve transitive
dependencies, and it does not verify versions.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "mcp_servers"]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Import name -> distribution name, where they differ. `pip` installs the
# distribution; `import` uses the module name, and the two are not always equal.
IMPORT_TO_DISTRIBUTION = {
    "yaml": "pyyaml",
    "pkg_resources": "setuptools",
    "dotenv": "python-dotenv",
    "PIL": "pillow",
    "dateutil": "python-dateutil",
}

# Standard library, third-party-but-optional, and this project's own packages.
# The optional entry is `langgraph`: it is imported only inside the orchestration
# adapter, which is allowed to be absent when a deployment uses the linear
# orchestrator instead (ADR-0002). Its absence is handled at the call site.
OPTIONAL_IMPORTS = frozenset({"langgraph"})

# This project's own top-level packages.
FIRST_PARTY = frozenset({"opspilot", "mcp_servers"})


def _declared_dependencies() -> set[str]:
    """Normalised names of every declared requirement, across all extras."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]

    requirements: list[str] = list(project.get("dependencies", []))
    for extra_requirements in project.get("optional-dependencies", {}).values():
        requirements.extend(extra_requirements)

    names: set[str] = set()
    for requirement in requirements:
        # Strip a version specifier, an extra marker (`uvicorn[standard]`) and
        # an environment marker, leaving the bare project name.
        name = requirement.split(";")[0]
        name = name.split("[")[0]
        for separator in (">=", "<=", "==", "~=", "!=", ">", "<"):
            name = name.split(separator)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def _imported_roots() -> dict[str, set[pathlib.Path]]:
    """Map each third-party top-level module name to where it is imported."""
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, set[pathlib.Path]] = {}

    for source_root in SOURCE_ROOTS:
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]

                for root in roots:
                    if root in stdlib or root in FIRST_PARTY or root in OPTIONAL_IMPORTS:
                        continue
                    if root.startswith("_"):
                        continue
                    found.setdefault(root, set()).add(path.relative_to(REPO_ROOT))

    return found


def _distribution_for(module_name: str) -> str:
    return IMPORT_TO_DISTRIBUTION.get(module_name, module_name).lower().replace("_", "-")


def test_every_imported_package_is_declared() -> None:
    """No module imports a third-party package the project does not declare."""
    declared = _declared_dependencies()
    imported = _imported_roots()

    undeclared: list[str] = []
    for module_name, paths in sorted(imported.items()):
        if _distribution_for(module_name) not in declared:
            where = ", ".join(str(p) for p in sorted(paths))
            undeclared.append(f"{module_name} (imported in {where})")

    assert not undeclared, (
        "these packages are imported by the source but are not declared in "
        "pyproject.toml (dependencies or an extra). A package that is present "
        "only because someone installed it by hand will be missing on a fresh "
        "clone:\n  " + "\n  ".join(undeclared)
    )


def test_the_guard_can_see_imports_at_all() -> None:
    """The scan found real imports, so a passing result means something.

    A guard that passes because it inspected nothing is worse than a failing one,
    because the pass is the part people read. This asserts the scan is not
    vacuous by requiring it to have found the dependencies the project is
    obviously built on.
    """
    imported = _imported_roots()
    for expected in ("sqlalchemy", "pydantic", "fastapi"):
        assert expected in imported, (
            f"the dependency scan did not find an import of {expected!r}, so it is "
            f"not reading the source tree it claims to check"
        )


@pytest.mark.parametrize(
    "module_name",
    ["pgvector", "sqlalchemy", "pydantic", "fastapi", "alembic"],
)
def test_core_dependencies_are_declared(module_name: str) -> None:
    """The specific packages whose absence breaks the package.

    Named individually rather than left to the scan above so that the failure
    message says which capability is lost: `pgvector` missing means
    `knowledge_chunks.embedding` cannot be declared as a vector column.
    """
    assert _distribution_for(module_name) in _declared_dependencies(), (
        f"{module_name} is not declared in pyproject.toml"
    )
