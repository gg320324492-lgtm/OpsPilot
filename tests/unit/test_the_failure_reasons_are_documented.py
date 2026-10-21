"""Every ``failure_reason`` the code can write must be a documented token.

## Why this file exists

A run's ``failure_reason`` is the one thing an operator reads when a run did
not finish, so the set of tokens it can hold is a **contract** -- and the
contract was in three places that did not agree.

The runtime wrote five: ``interrupted`` (from the boot sweep),
``mcp_unavailable``, ``schema_invalid``, ``tool_error`` and
``max_steps_exceeded``. The worker wrote **four more** -- ``model_unavailable``,
``model_rejected``, ``model_invalid_response`` and ``model_error`` -- when a
provider SDK raised, and those four were in no document at all. The API
returns the column verbatim and the dashboard renders it verbatim, so a run
that failed because the provider refused our key came back with a token a
reader could not place.

The four are not the same failure: ``model_unavailable`` is a network,
``model_rejected`` is a credential, and a dashboard that renders both as "the
model failed" throws the distinction away.

## What this file asserts

1. **The code's tokens are the document's tokens** -- both directions. A new
   token that no document names is a run whose failure nobody can interpret,
   and a documented token no code can write is a promise about a failure that
   cannot happen.
2. **The document's table is a table of tokens**, not prose -- so a row that
   lost its backticks is caught rather than silently ignored.

The tokens are read out of the code by parsing it, so a reason spelled
differently is still found, and a **constant** (``reason=MODEL_UNAVAILABLE``)
is resolved to the string it holds rather than to its name.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opspilot"
DOC = REPO_ROOT / "docs" / "agent-state-machine.md"

#: The keyword the runtime and the worker pass a reason under.
_REASON_KEYWORD = "reason"

#: The two calls that write the run's ``failure_reason``. The other
#: ``reason=`` in the codebase is a **rejection** reason -- the gate that
#: refused a call, which lands in ``tool_calls.rejection_reason`` and has a
#: different vocabulary entirely (``unknown_tool``, ``tool_denylisted``,
#: ``refund_above_ceiling``, ``idempotency_key_already_executed``). The two
#: are different columns and the documents keep them apart.
_FAILURE_WRITERS = frozenset({"_fail_run", "_mark_failed"})

#: The table that maps a provider SDK's exception names to the reason the run
#: is marked with. Its values are constants (``MODEL_UNAVAILABLE`` and
#: friends), so the table is read by resolving the names it references.
_SDK_REASON_TABLE = "_SDK_STATUS_REASONS"

#: The two functions that resolve a model failure's reason. The catch-all
#: (``MODEL_ERROR``) is what they *return*, not what a keyword argument
#: carries, so these are read by their bodies.
_MODEL_REASON_FUNCTIONS = frozenset({"_model_failure_reason", "_model_failure_reasons"})

#: A table row whose first cell is a backticked token. The row may be indented:
#: §3's table sits inside a list item.
_ROW = re.compile(r"^\s*\|\s*`([a-z][a-z0-9_]*)`\s*\|")


def _module_constants(path: pathlib.Path) -> dict[str, str]:
    """The module-level string constants a file holds.

    ``reason=MODEL_UNAVAILABLE`` names a constant; the *token* is the string
    it holds. Resolving the name is what keeps the scan from reporting
    ``MODEL_UNAVAILABLE`` as a token.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                constants[node.target.id] = node.value.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def _tokens_in(path: pathlib.Path) -> set[str]:
    """Every ``failure_reason`` token this file can write."""
    constants = _module_constants(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        # ``return MODEL_ERROR`` -- the catch-all a model failure resolves to.
        if isinstance(node, ast.FunctionDef) and node.name in _MODEL_REASON_FUNCTIONS:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in constants:
                    value = constants[child.id]
                    if value.startswith("model_"):
                        found.add(value)
        # ``_fail_run(..., reason="schema_invalid")``
        elif isinstance(node, ast.Call):
            function = node.func
            if not (isinstance(function, ast.Name) and function.id in _FAILURE_WRITERS):
                continue
            for keyword in node.keywords:
                if keyword.arg != _REASON_KEYWORD:
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
                elif isinstance(value, ast.Name) and value.id in constants:
                    found.add(constants[value.id])
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets: list[ast.expr] = (
                list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            )
            # ``_SDK_STATUS_REASONS = (("APIConnectionError", MODEL_UNAVAILABLE), ...)``
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                if targets[0].id == _SDK_REASON_TABLE:
                    for child in ast.walk(node.value):
                        if isinstance(child, ast.Name) and child.id in constants:
                            found.add(constants[child.id])
            # ``row.failure_reason = "interrupted"``
            for target in targets:
                attribute = getattr(target, "attr", None)
                if attribute != "failure_reason":
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    found.add(node.value.value)
    return found


def _code_tokens() -> set[str]:
    """Every token the source can write."""
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        found |= _tokens_in(path)
    return found


def _documented_tokens() -> set[str]:
    """Every token the state machine's §3 table names."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith("## 3. Failure semantics")
    )
    tokens: set[str] = set()
    for line in lines[start:]:
        if line.startswith("## ") and not line.startswith("## 3."):
            break
        match = _ROW.match(line)
        if match is not None:
            tokens.add(match.group(1))
    return tokens


def test_the_documented_tokens_are_exactly_the_tokens_the_code_writes() -> None:
    """The two lists agree, in both directions."""
    code = _code_tokens()
    documented = _documented_tokens()

    assert code, "no failure_reason token was found in the source; the scan is broken"

    undocumented = sorted(code - documented)
    assert not undocumented, (
        f"the code can write {undocumented} and docs/agent-state-machine.md §3 "
        "does not name them. A failure an operator cannot interpret is a "
        "failure the dashboard can only render as an unexplained string."
    )

    unreachable = sorted(documented - code)
    assert not unreachable, (
        f"docs/agent-state-machine.md §3 names {unreachable} and no code path "
        "can write them. A documented failure that cannot happen is a promise "
        "about the contract that nothing keeps."
    )
    print(f"\nfailure_reason tokens: {len(code)}")


def test_the_documented_table_is_a_table_of_tokens() -> None:
    """The §3 table is found, and it is a table.

    A guard that reads nothing checks nothing, and a table that lost its
    backticks would make the guard above vacuously green.
    """
    documented = _documented_tokens()
    assert len(documented) >= 5, (
        f"§3 names only {len(documented)} failure reasons; the table was "
        "probably reformatted into prose"
    )
    assert "interrupted" in documented
    assert "max_steps_exceeded" in documented
