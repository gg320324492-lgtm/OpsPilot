"""Every error code the API can send, read out of the API's own source.

## Why this is a shared module

Two guards need the same thing: the one that holds the **dashboard's** code
union to the API's, and the one that holds the **contract's** §6 table to the
API's. A third copy of an AST walk would be a third answer to drift from.

## Why it is an AST walk and not a grep

The codes are written in three shapes, and only one of them is a distinctive
string in the source:

* ``code="..."`` as a keyword argument,
* ``ApiError(status, "...")`` with the code positionally,
* ``return "..."`` inside ``_code_for_status``.

A grep for a quoted lowercase word would collect every message that happens to
be one word. Parsing means a code spelled with single quotes, or split across
lines, is still found — and that a *variable* named ``code`` is not mistaken for
one.
"""

from __future__ import annotations

import ast
import pathlib
import re

#: The API's source tree.
API_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "opspilot" / "api"

#: A code is a lowercase snake_case word. Anything else in the sources is not
#: one, and matching the shape is what keeps the scan from collecting prose.
CODE_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def _codes_from_call(node: ast.Call) -> set[str]:
    """The codes a call can carry: ``code="..."`` or ``ApiError(status, "...")``."""
    found: set[str] = set()
    for keyword in node.keywords:
        if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, str):
                found.add(value)
    function = node.func
    if (
        isinstance(function, ast.Name)
        and function.id == "ApiError"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    ):
        value = node.args[1].value
        if isinstance(value, str):
            found.add(value)
    return found


def _codes_returned(node: ast.FunctionDef) -> set[str]:
    """The literal codes ``_code_for_status`` can return."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
            value = child.value.value
            if isinstance(value, str):
                found.add(value)
    return found


def _status_and_code_from_call(node: ast.Call) -> tuple[int, str] | None:
    """The ``(status, code)`` a call raises, when both are literals."""
    function = node.func
    if not (
        isinstance(function, ast.Name)
        and function.id == "ApiError"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[1], ast.Constant)
    ):
        return None
    status, code = node.args[0].value, node.args[1].value
    if isinstance(status, int) and isinstance(code, str):
        return status, code
    return None


def backend_error_codes() -> set[str]:
    """Every error code the API can send."""
    codes: set[str] = set()
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                codes.update(_codes_from_call(node))
            elif isinstance(node, ast.FunctionDef) and node.name == "_code_for_status":
                codes.update(_codes_returned(node))
    return {code for code in codes if CODE_SHAPE.match(code)}


def backend_codes_by_status() -> dict[int, set[str]]:
    """Every ``(status, code)`` the API raises with a literal pair.

    The ``ApiError(status, code)`` calls are where the API says, in one place,
    "this status carries this code" — so they are the authority for the
    status-to-code mapping, rather than the contract's table of it.
    """
    by_status: dict[int, set[str]] = {}
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                pair = _status_and_code_from_call(node)
                if pair is not None:
                    status, code = pair
                    if CODE_SHAPE.match(code):
                        by_status.setdefault(status, set()).add(code)
    return by_status


def backend_handler_codes_by_status() -> dict[int, set[str]]:
    """Every ``(status, code)`` the exception handlers serve.

    The handlers do not raise ``ApiError`` -- they build the envelope
    directly, through ``_json_error`` -- so :func:`backend_codes_by_status`
    does not see them. They are where the codes that are **not** a router's
    fault live: ``unauthorized``, ``method_not_allowed``, ``not_found``,
    ``illegal_transition``, ``approval_arguments_changed``, ``policy_denied``,
    ``unknown_tool``, ``schema_invalid`` and ``internal_error``.
    """
    by_status: dict[int, set[str]] = {}
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (isinstance(function, ast.Name) and function.id == "_json_error"):
                continue
            status: object = None
            code: object = None
            for keyword in node.keywords:
                if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant):
                    status = keyword.value.value
                if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                    code = keyword.value.value
            if isinstance(status, int) and isinstance(code, str) and CODE_SHAPE.match(code):
                by_status.setdefault(status, set()).add(code)
    return by_status


def backend_field_names() -> set[str]:
    """Every field name the API's models declare.

    Read from the model classes' annotations. A name in this set is a **field**,
    not a code — and the contract's §6 table mentions field names beside the
    codes ("keyed by a `trace_id` returned in `details`"), so a table cell that
    is read without this distinction collects field names as though they were
    codes.
    """
    names: set[str] = set()
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for statement in node.body:
                    if isinstance(statement, ast.AnnAssign) and isinstance(
                        statement.target, ast.Name
                    ):
                        names.add(statement.target.id)
    return names
