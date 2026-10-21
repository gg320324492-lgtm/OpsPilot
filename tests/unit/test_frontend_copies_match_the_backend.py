"""The dashboard's hand-copied tables must still agree with the backend's.

## Why this file exists

Two facts live in the backend and are **copied** into the dashboard, because
the dashboard needs them in a form the API does not send:

* ``TOOL_REGISTRY``'s permission per tool -> ``web/src/lib/risk.ts``'s
  ``TOOL_PERMISSION``, the fallback the approval card uses when the run
  detail -- which carries the authoritative ``permission`` -- is not loaded.
* the error ``code`` vocabulary -> ``web/src/lib/api/client.ts``'s
  ``ApiErrorCode`` union, the values a client branches on.

A copy is a second place for the same fact to live, and the two can disagree.
**Both copies had already drifted.** ``TOOL_PERMISSION`` was missing three of
the nine registered tools -- ``crm.get_account``, ``crm.get_subscription`` and
``issues.search`` -- so a call to one of them read as "risk unknown" instead
of "read", and the file's own docstring claimed every registered tool was in
it. The ``ApiErrorCode`` union was missing four codes the API can send:
``illegal_transition``, ``approval_arguments_changed``, ``policy_denied`` and
``unknown_tool``.

Nothing caught either, because the only test that compares the frontend to
the backend -- ``tests/integration/test_web_client_types.py`` -- compares the
**generated** ``types.ts`` against the live OpenAPI schema, and neither of
these tables is in it. The generated file covers what the API *publishes*;
these two are copies of what the API *knows*.

## What this file asserts

* the frontend's permission table has exactly the backend's tool names, with
  exactly the backend's permissions;
* the frontend's error-code union names every code the backend can send, and
  names no code the backend cannot.

The second is asserted in **both** directions on purpose. A code the backend
sends and the frontend does not name arrives as an unrecognised string, and
the dashboard's own code has to say something sensible about it; a code the
frontend names and the backend never sends is a branch that can never be
taken, which is a claim about the API that is false.

## How the backend's side is read

The codes are string literals in ``src/opspilot/api/`` -- there is no
canonical list to import, which is the other half of the problem. They are
read with an AST walk that recognises the three shapes they are written in:
``code="..."`` as a keyword, ``ApiError(status, "code", ...)`` as a positional
argument, and ``return "..."`` inside ``_code_for_status``. Reading them by
parsing rather than by grepping means a code spelled with single quotes or
split across lines is still found, and that a *variable* named ``code`` is not
mistaken for one.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

from opspilot.domain.tools import TOOL_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_API_DIR = REPO_ROOT / "src" / "opspilot" / "api"
_RISK_TS = REPO_ROOT / "web" / "src" / "lib" / "risk.ts"
_CLIENT_TS = REPO_ROOT / "web" / "src" / "lib" / "api" / "client.ts"

#: The three permission levels, as the backend spells them.
_PERMISSIONS = frozenset({"read", "safe_write", "high_risk_write"})

#: A code is a lowercase snake_case word. Anything else in the sources is not
#: one, and matching the shape is what keeps the scan from collecting prose.
_CODE_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def _backend_tool_permissions() -> dict[str, str]:
    """``TOOL_REGISTRY``'s name -> permission, read from the registry itself."""
    return {name: spec.permission.value for name, spec in TOOL_REGISTRY.items()}


def _frontend_tool_permissions() -> dict[str, str]:
    """The dashboard's copy of the same table, read out of ``risk.ts``.

    The table is a plain object literal, so the pairs are found by pattern
    rather than by parsing TypeScript: a tool name and a permission are both
    quoted strings on one line, and the only quoted pairs in the file of that
    shape are the table's entries.
    """
    source = _RISK_TS.read_text(encoding="utf-8")
    block = _object_literal(source, "TOOL_PERMISSION")
    pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', block)
    return dict(pairs)


def _object_literal(source: str, name: str) -> str:
    """The body of the object literal assigned to ``name``.

    Found by brace counting from the assignment, so a comment or a docstring
    above it cannot be mistaken for the table and a table that follows cannot
    be mistaken for it either.
    """
    marker = f"const {name}"
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"the object literal for {name} is not closed")


def _backend_error_codes() -> set[str]:
    """Every error code the API can send, read out of ``src/opspilot/api/``.

    The three shapes are the three the codes are written in; anything else is
    not a code and is not collected.
    """
    codes: set[str] = set()
    for path in sorted(_API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                codes.update(_codes_from_call(node))
            elif isinstance(node, ast.FunctionDef) and node.name == "_code_for_status":
                codes.update(_codes_returned(node))
    return {code for code in codes if _CODE_SHAPE.match(code)}


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


def _frontend_error_codes() -> set[str]:
    """The codes the dashboard's ``ApiErrorCode`` union names.

    The union's members are one per line, each written ``| "code"``. The lines
    that are comments are skipped first, because the union carries comments
    that *mention* codes it does not declare -- ``not_ready`` is one of them,
    and it is a word in a sentence about a code the API cannot send. Reading
    the comments as members would make the union look like it names a status
    it does not.
    """
    source = _CLIENT_TS.read_text(encoding="utf-8")
    start = source.index("export type ApiErrorCode")
    body = source[start : source.index(";", start)]
    members: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        members.update(re.findall(r'\|\s*"([a-z][a-z0-9_]*)"', line))
    return members


def test_the_dashboard_permission_table_is_the_registry() -> None:
    """The frontend's fallback table is the backend's registry, tool for tool.

    Both halves matter. A name the backend has and the frontend does not is a
    tool whose risk level reads as "unknown" -- which is the dashboard
    declining to say, where the API could have said "read". A name the
    frontend has and the backend does not is a permission the dashboard will
    confidently display for a tool that does not exist.
    """
    backend = _backend_tool_permissions()
    frontend = _frontend_tool_permissions()

    missing = sorted(set(backend) - set(frontend))
    assert not missing, (
        "these registered tools are missing from the dashboard's permission table, so a "
        f"call to one of them reads as 'risk unknown' instead of its real permission: "
        f"{missing}"
    )

    extra = sorted(set(frontend) - set(backend))
    assert not extra, (
        f"the dashboard's permission table names tools that are not registered: {extra}"
    )

    wrong = {
        name: (backend[name], frontend[name])
        for name in backend
        if frontend.get(name) != backend[name]
    }
    assert not wrong, (
        "these tools have the wrong permission in the dashboard's table "
        "(backend, frontend): "
        f"{wrong}"
    )

    assert set(frontend.values()) <= _PERMISSIONS, (
        f"the dashboard's table uses a permission level the API does not have: "
        f"{sorted(set(frontend.values()) - _PERMISSIONS)}"
    )


def test_the_dashboard_knows_every_code_the_api_can_send() -> None:
    """The frontend's code vocabulary covers the API's, and no more than it.

    A code the API sends and the dashboard does not name arrives as a string
    the dashboard has no branch for; a code the dashboard names and the API
    never sends is a branch that can never be taken.
    """
    backend = _backend_error_codes()
    frontend = _frontend_error_codes()

    assert backend, "the scan found no error codes at all; the scan is broken, not the code"
    assert frontend, "the dashboard's union names no codes; the parse is broken"

    unknown = sorted(backend - frontend)
    assert not unknown, (
        "the API can send these codes and the dashboard does not name them, so each one "
        f"arrives as an unrecognised string: {unknown}"
    )

    dead = sorted(frontend - backend)
    assert not dead, f"the dashboard branches on codes the API never sends: {dead}"


def test_the_dashboard_has_a_label_for_every_status_the_backend_sends() -> None:
    """The catalogues' status labels cover the backend's enums exactly.

    The dashboard localises the *labels* for four contract enums — the run
    status, the approval status, the tool-call status and the permission — and
    it renders them through ``web/src/i18n/enums.ts``. A status with no label
    does not break the page: ``labelFor`` catches the missing key and falls
    back to ``humanise(value)``, which is the honest degradation the module
    docstring promises.

    **The degradation is the problem.** ``humanise("proposed")`` is "Proposed",
    which is right in English and *wrong on a Chinese page* — the operator gets
    one English word where the rest of the screen is Chinese, on the one screen
    they are deciding on. The fallback exists for a deployment running a newer
    API than this dashboard, not for a status this dashboard has simply
    forgotten.

    This is asserted in **both** directions, and the second half is the more
    interesting one. A label the backend never sends is a claim about the API
    that is false — and it is how the hole was hidden: the catalogue had
    ``status.toolCall.pending``, a status the backend does not have, where the
    backend's ``proposed`` was missing. The extra key made the group *look*
    complete to a reader counting keys, and the missing one made every proposed
    tool call render as "Proposed" in English on a Chinese page.
    """
    from opspilot.domain.approvals import ApprovalStatus
    from opspilot.domain.runs import RunStatus
    from opspilot.domain.tools import Permission, ToolCallStatus

    backend_by_group: dict[str, set[str]] = {
        "run": {status.value for status in RunStatus},
        "approval": {status.value for status in ApprovalStatus},
        "toolCall": {status.value for status in ToolCallStatus},
        "permission": {permission.value for permission in Permission},
    }

    for locale in ("en", "zh-CN"):
        catalogue = json.loads(
            (REPO_ROOT / "web" / "src" / "i18n" / "dictionaries" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        labels = catalogue.get("status", {})
        for group, backend_values in backend_by_group.items():
            labelled = set(labels.get(group, {}))
            assert labelled == backend_values, (
                f"the {locale} catalogue's `status.{group}` labels are "
                f"{sorted(labelled)}, and the backend's {group} values are "
                f"{sorted(backend_values)}. A status with no label renders as "
                "the English word on a translated page; a label for a status "
                "the backend never sends is a false claim about the API."
            )
