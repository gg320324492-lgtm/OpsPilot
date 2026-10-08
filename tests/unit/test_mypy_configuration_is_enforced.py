"""The configured mypy file set and the command people run must not diverge.

``pyproject.toml`` sets ``[tool.mypy] files = [...]`` and, once set, bare ``mypy``
checks exactly that list. The failure this guards against is specific, and it
happened: the config named four directories, but every brief and every
contributor ran ``mypy --strict src`` -- which checks ``src`` *only*, and passes.
The configured set ``tests`` was therefore checked by nobody, and eighteen real
errors sat in ``tests/`` behind a green ``--strict`` run.

The defect is the same species the rest of this suite hunts: a claim (the config
says these directories are type-checked) that the executed command did not
honour. A green ``mypy --strict src`` is not evidence for a config that lists
``tests``, in exactly the way a docstring asserting an invariant is not the
invariant.

Why this asserts the *configuration* and not by running mypy
------------------------------------------------------------
The stronger test would shell out to ``mypy`` and require zero errors. That is
the check itself, not a guard on the check, and running it inside pytest costs
the full mypy pass (~20s, and worse cold) on every run -- for a fact that changes
only when ``pyproject.toml`` or a source file changes. It would also make the
suite fail with a wall of mypy output that duplicates what CI already runs.

So this test asserts the two cheap, load-bearing relationships that make the
real check meaningful:

1. the configured ``files`` still names every first-party root the project means
   to type-check -- so the list cannot silently lose ``tests`` (or gain a
   directory nobody intended) without a red test; and
2. the command ``README.md`` documents is bare ``mypy`` -- so a contributor is
   not told to pass a path that would narrow the check and hide a whole tree.

What it deliberately does not do: it does not verify mypy is error-free (that is
``mypy`` itself, run in CI) and it does not check ``mypy``'s version resolution.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"

#: The first-party roots this project means to type-check. ``src`` and
#: ``mcp_servers`` are the shipped packages, ``tests`` and ``evals`` are the
#: spec-as-code that proves them. Listed here rather than read from the config,
#: because a test that read its expectation from the thing it checks would pass
#: no matter what the config said -- the config must match *this* list.
EXPECTED_CHECKED_ROOTS = frozenset({"src", "mcp_servers", "tests", "evals"})

# A fenced ```bash block whose first line is a mypy invocation.
_MYPY_INVOCATION = re.compile(r"^\s*mypy(?P<args>.*)$", re.MULTILINE)


def _configured_files() -> list[str]:
    """The ``[tool.mypy] files`` list, exactly as written."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    mypy = data.get("tool", {}).get("mypy", {})
    files = mypy.get("files")
    assert isinstance(files, list), (
        "[tool.mypy] files is not a list. Without it, bare `mypy` checks the "
        "directory it is run from -- which is not the same set the project "
        "documents, and is how an entire test tree stayed unchecked."
    )
    return files


def test_the_configured_files_cover_every_first_party_root() -> None:
    """``[tool.mypy] files`` names exactly the roots the project means to check."""
    configured = _configured_files()
    missing = EXPECTED_CHECKED_ROOTS - set(configured)
    assert not missing, (
        f"[tool.mypy] files omits {sorted(missing)}. If `tests` is dropped, bare "
        "`mypy` stops checking it and the only command that catches test-file "
        "type errors is the one nobody is told to run -- which is exactly how "
        "this project shipped eighteen unchecked test errors. If the omission is "
        "deliberate, remove the root from EXPECTED_CHECKED_ROOTS in this test so "
        "the decision is recorded rather than silent."
    )
    unexpected = set(configured) - EXPECTED_CHECKED_ROOTS
    assert not unexpected, (
        f"[tool.mypy] files checks {sorted(unexpected)}, which is not a first-party "
        "root this test knows about. Add it to EXPECTED_CHECKED_ROOTS deliberately "
        "-- a directory that appears in the check without anyone deciding to is "
        "how coverage of the *intended* files gets confused with coverage of all "
        "files."
    )


def test_the_documented_command_is_bare_mypy() -> None:
    """``README.md`` tells contributors to run ``mypy``, with no path argument.

    The command and the config are two halves of one check. The moment README
    says ``mypy --strict src``, the documented command and the configured set
    disagree, and the person following the README checks a subtree while
    believing they checked the project.
    """
    match = _MYPY_INVOCATION.search(README.read_text(encoding="utf-8"))
    assert match is not None, (
        "README.md documents no `mypy` command. The type check is part of the "
        "definition of done; if it is not written down, the command people run "
        "drifts from [tool.mypy] files, which is the divergence this file exists "
        "to catch."
    )
    args = match.group("args").strip()
    # A path positional is the failure mode: it narrows the check to that path,
    # overriding the configured `files` list entirely.
    positional_paths = [
        token
        for token in args.split()
        if not token.startswith("-") and ("/" in token or token in EXPECTED_CHECKED_ROOTS)
    ]
    assert not positional_paths, (
        f"README.md documents `mypy {args}`, which passes {positional_paths} as a "
        "path. mypy then checks only that path and ignores [tool.mypy] files, so "
        "the documented command and the configured set no longer agree. Run bare "
        "`mypy` and let the config name the files."
    )


def test_ruff_checks_the_scripts_tree() -> None:
    """``scripts/`` must be inside ruff's configured paths.

    The same defect this file exists for, found again in a different tool. Two
    shipped scripts sit outside ruff's ``src`` list: ``openapi_schema.py``,
    which generates the dashboard's types from the live OpenAPI document, and
    ``demo_golden_path.py``, which produces the README's demo. Nothing checked
    either -- ``ruff check src tests`` names two of the four configured trees and
    ``scripts`` was not a fifth.

    They happened to be clean, which is exactly what made the gap survivable:
    a scope that is wrong and empty looks identical to a scope that is right and
    clean until the day someone adds a file.
    """
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    paths = config["tool"]["ruff"]["src"]

    assert "scripts" in paths, (
        f"ruff's configured paths are {paths}, which excludes scripts/. CI runs "
        "`ruff check` bare, so anything added there is never linted. Add it."
    )

    shipped = sorted(p.name for p in (REPO_ROOT / "scripts").glob("*.py") if p.is_file())
    assert shipped, "scripts/ is empty, so the setting names nothing"
