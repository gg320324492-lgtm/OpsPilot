"""The Anthropic adapter must not spell its structured-output request twice.

``generate_structured`` honours the ``structured_output`` setting: it either asks
for ``output_config`` (correct for real Anthropic) or forces a tool (what a
gateway that ignores ``output_config`` needs).

``choose_tool`` did not. It built its own request, hardcoded ``output_config``,
and so ignored the setting entirely. Measured against a gateway that silently
drops ``output_config``: ``generate_structured`` worked and ``choose_tool``
failed on every call -- same adapter, same configuration, opposite outcomes --
because half the file asked one way and half asked the other.

This is the third instance in this project of one concept written twice with only
one copy exercised (the two ``response_format`` spellings in the OpenAI adapter,
the two provider-construction sites). The guard is what stops a fourth.

Asserted structurally against the source rather than by calling the method,
because the failure is *a second request builder existing*, and a behavioural
test would pass the moment the two happened to agree.
"""

from __future__ import annotations

import ast
import pathlib

_MODULE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "opspilot"
    / "adapters"
    / "models"
    / "anthropic_provider.py"
)

#: The settings field the adapter is configured by, and the two values it accepts.
_SETTING_FIELD = "structured_output"


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


def _function_node(name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    """The AST node for a top-level-or-method function named ``name``."""
    tree = ast.parse(_source(), filename=str(_MODULE))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_MODULE.name}; it was renamed or removed")


def test_choose_tool_does_not_build_its_own_request() -> None:
    """``choose_tool`` must not call the API directly.

    It delegates to ``generate_structured``, which is the one place the
    structured-output mechanism is chosen. A direct ``messages.create`` here is
    the defect this guards, whatever options it happens to pass today.
    """
    node = _function_node("choose_tool")
    calls = [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create"
    ]
    assert not calls, (
        "choose_tool issues its own API request. It must delegate to "
        "generate_structured, which is the single place `structured_output` is "
        "honoured -- otherwise the two methods can disagree about the mechanism, "
        "which is exactly what happened: output_config was hardcoded here and "
        "ignored the setting, so every call failed against a gateway while "
        "generate_structured succeeded."
    )


def test_choose_tool_does_not_hardcode_a_mechanism() -> None:
    """No ``output_config`` / ``tool_choice`` literal may appear in ``choose_tool``.

    The delegation already guarantees this, but asserted separately because the
    tempting repair for a broken ``choose_tool`` is to add the *other* mechanism
    inline rather than to delegate -- which recreates the divergence in the
    opposite direction.
    """
    node = _function_node("choose_tool")
    names = {
        n.attr if isinstance(n, ast.Attribute) else n.id
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute | ast.Name)
    }
    offenders = sorted(names & {"output_config", "tool_choice", "input_schema"})
    assert not offenders, (
        f"choose_tool references {offenders}, so it is choosing a mechanism "
        "itself. The choice belongs to generate_structured, driven by "
        f"{_SETTING_FIELD!r}."
    )


def test_the_setting_is_read_in_exactly_one_place() -> None:
    """``structured_output`` is consulted once, so both methods obey it.

    Two reads would mean two decision points, which can disagree. One read in
    ``generate_structured`` plus delegation is what makes ``choose_tool`` honour
    the configured mechanism without knowing about it.
    """
    tree = ast.parse(_source(), filename=str(_MODULE))
    readers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == f"_{_SETTING_FIELD}"
    ]
    # `__init__` assigns `self._structured_output = structured_output`, and
    # `generate_structured` reads it once to branch. More than that means a
    # second decision point.
    assert len(readers) <= 2, (
        f"self._{_SETTING_FIELD} is referenced {len(readers)} times. Expected at "
        "most two: the assignment in __init__ and the single branch in "
        "generate_structured. Each additional reference is another place the "
        "mechanism is chosen, and two choices can disagree."
    )

    generate = _function_node("generate_structured")
    assert any(
        isinstance(n, ast.Attribute) and n.attr == f"_{_SETTING_FIELD}"
        for n in ast.walk(generate)
    ), "generate_structured no longer consults the setting at all"
