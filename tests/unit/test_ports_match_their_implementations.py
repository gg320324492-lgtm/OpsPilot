"""A port must say what its implementation actually does.

## Why this file exists

``src/opspilot/ports/`` is a set of ``Protocol`` classes: the seams the runtime
and the API depend on, and the contracts ``src/opspilot/adapters/`` implements.
A Protocol is a *claim* -- "an implementer of this port has these methods,
with these parameters, and these failures" -- and a claim nobody checks is a
claim that rots.

Two things had rotted, in the same shape:

* **Signatures.** A port declares ``async def count(self, *, status: RunStatus
  | None = None) -> int``; an adapter implements ``async def count(self, status:
  RunStatus | None = None) -> int``. Every *call* still works (the router and
  the runtime all pass by keyword), so nothing fails -- but the port no longer
  describes the code it is supposed to describe, and the next reader of the
  port is reading a fiction. The parameter name is part of the contract: a
  caller who writes ``count(status=...)`` is relying on it.

* **Exceptions.** ``ApprovalStore.decide`` documents two failures --
  ``ApprovalAlreadyDecided`` and ``NotFoundError`` -- and the adapter raises
  three. The third, ``ApprovalExpired``, is the one the API answers with a
  *different* 409 (``approval_expired`` rather than
  ``approval_already_decided``, because "somebody else was here first" and
  "nobody was here at all" are different facts). A caller who reads the port
  catches ``LookupError`` and lets ``ApprovalExpired`` escape as a 500 --
  because the port never told them it existed.

  The same hole was in ``TicketStore.create`` (the adapter raises
  ``TicketExists``; the port said nothing), in ``RunStore.set_status`` and in
  ``ToolCallStore.set_status``.

## What this file asserts

1. **Every port method is implemented by the adapter that claims to implement
   it, with the same signature**: the same parameter names, in the same order,
   with the same keyword-only-ness, the same defaults, and the same return
   annotation.

2. **Every domain error an implementation can raise from a port method is
   named in that port method's ``Raises:`` section.** The domain's error
   vocabulary (``opspilot.domain.errors``) is shared between the ports and
   their adapters, so the port *can* name these exceptions -- and a caller
   reading only the port has to be able to tell which failures it must
   handle. Errors that are not domain errors are outside this file's business:
   a provider's ``StructuredOutputError`` is an adapter's own vocabulary, and
   a port that does not name it is not lying.

The pairing of a port with its implementation is written down in
``IMPLEMENTATIONS`` below, and it is the pairing that matters: the point is
not that a Protocol has *some* implementation, it is that *this* implementation
matches *this* port.

## What it does not do

It does not check that an adapter's *behaviour* matches its port's prose --
that is a question about meaning, and a guard that guessed would be a guard
that misfired. It checks the shape: names, kinds, defaults, return types, and
the exception vocabulary.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import sys
import textwrap
import types
import typing
from typing import Any, cast

import pytest

from opspilot.domain import errors as domain_errors
from opspilot.ports import model_provider, orchestrator, stores, tool_gateway, vector_store

#: Each port, the module its implementation lives in, and the implementation's
#: name. This is the pairing the whole file is about: *this* port and *that*
#: implementation.
IMPLEMENTATIONS: tuple[tuple[type, str, str], ...] = (
    (stores.TicketStore, "opspilot.adapters.persistence.repositories", "SqlTicketStore"),
    (stores.TicketIntake, "opspilot.adapters.persistence.repositories", "SqlTicketIntake"),
    (stores.RunStore, "opspilot.adapters.persistence.repositories", "SqlRunStore"),
    (stores.ToolCallStore, "opspilot.adapters.persistence.repositories", "SqlToolCallStore"),
    (stores.ApprovalStore, "opspilot.adapters.persistence.repositories", "SqlApprovalStore"),
    (stores.CitationStore, "opspilot.adapters.persistence.repositories", "SqlCitationStore"),
    (
        stores.KnowledgeDocumentStore,
        "opspilot.adapters.persistence.repositories",
        "SqlKnowledgeDocumentStore",
    ),
    (
        model_provider.ModelProvider,
        "opspilot.adapters.models.anthropic_provider",
        "AnthropicModelProvider",
    ),
    (
        model_provider.ModelProvider,
        "opspilot.adapters.models.openai_provider",
        "OpenAIModelProvider",
    ),
    (model_provider.ModelProvider, "opspilot.adapters.models.fake", "FakeModelProvider"),
    (tool_gateway.ToolGateway, "opspilot.adapters.tools.composite_gateway", "CompositeToolGateway"),
    (tool_gateway.ToolGateway, "opspilot.adapters.tools.knowledge_tool", "KnowledgeTool"),
    (tool_gateway.ToolGateway, "opspilot.adapters.tools.mcp_gateway", "MCPToolGateway"),
    (tool_gateway.ToolGateway, "opspilot.adapters.tools.stub_gateway", "StubGateway"),
    (vector_store.VectorStore, "opspilot.adapters.retrieval.memory_store", "InMemoryVectorStore"),
    (vector_store.VectorStore, "opspilot.adapters.retrieval.pgvector_store", "PgVectorStore"),
    (orchestrator.Orchestrator, "opspilot.adapters.orchestration.linear", "LinearOrchestrator"),
)

#: A ``Raises:`` entry: an exception name and a colon.
_RAISES_ENTRY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")


def _port_methods(port: type) -> list[tuple[str, types.FunctionType]]:
    """The protocol's own methods, as ``(name, function)`` pairs."""
    found: list[tuple[str, types.FunctionType]] = []
    for name, member in vars(port).items():
        if name.startswith("_"):
            continue
        if inspect.isfunction(member):
            found.append((name, member))
    return sorted(found)


def _render(annotation: object) -> str:
    """An annotation as text, for comparison."""
    if annotation is inspect.Signature.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    return str(annotation)


def _resolved_hints(func: types.FunctionType, globalns: dict[str, Any]) -> dict[str, Any]:
    """The function's annotations resolved, or an empty mapping if they cannot be."""
    try:
        return typing.get_type_hints(func, globalns=globalns)
    except Exception:  # pragma: no cover - an unresolvable annotation is not a failure here
        return {}


def _annotations_compatible(port: object, impl: object) -> bool:
    """Whether two annotations may be the same contract.

    ``Any`` is compatible with everything -- a provider that takes
    ``dict[str, Any]`` where the port says ``dict[str, object]`` is accepting a
    *wider* set of arguments, which is exactly what a port may allow. Two
    annotations that resolve to the same object (an import alias of the same
    class) are the same contract.
    """
    if port is inspect.Signature.empty or impl is inspect.Signature.empty:
        return _render(port) == _render(impl)
    if port is impl:
        return True
    port_text, impl_text = _render(port), _render(impl)
    if port_text == impl_text:
        return True
    return "Any" in port_text or "Any" in impl_text


def _signature_diff(
    port_func: types.FunctionType,
    impl_func: types.FunctionType,
    port_ns: dict[str, Any],
    impl_ns: dict[str, Any],
) -> list[str]:
    """Human-readable differences between two signatures; empty when they agree."""
    port_sig = inspect.signature(port_func)
    impl_sig = inspect.signature(impl_func)
    port_params = [p for p in port_sig.parameters.values() if p.name != "self"]
    impl_params = [p for p in impl_sig.parameters.values() if p.name != "self"]

    problems: list[str] = []
    if [p.name for p in port_params] != [p.name for p in impl_params]:
        problems.append(
            f"parameter names differ: port {tuple(p.name for p in port_params)} "
            f"vs implementation {tuple(p.name for p in impl_params)}"
        )
    for port_p, impl_p in zip(port_params, impl_params, strict=False):
        if port_p.kind is not impl_p.kind:
            problems.append(
                f"parameter {port_p.name!r} is {port_p.kind.name} on the port "
                f"and {impl_p.kind.name} in the implementation"
            )
        port_default = (
            inspect.Parameter.empty
            if port_p.default is inspect.Parameter.empty
            else repr(port_p.default)
        )
        impl_default = (
            inspect.Parameter.empty
            if impl_p.default is inspect.Parameter.empty
            else repr(impl_p.default)
        )
        if port_default != impl_default:
            problems.append(
                f"parameter {port_p.name!r} defaults to {port_default} on the port "
                f"and {impl_default} in the implementation"
            )

    port_hints = _resolved_hints(port_func, port_ns)
    impl_hints = _resolved_hints(impl_func, impl_ns)
    for port_p, impl_p in zip(port_params, impl_params, strict=False):
        port_ann = port_hints.get(port_p.name, port_p.annotation)
        impl_ann = impl_hints.get(impl_p.name, impl_p.annotation)
        if not _annotations_compatible(port_ann, impl_ann):
            problems.append(
                f"parameter {port_p.name!r} is annotated {_render(port_ann)} on the port "
                f"and {_render(impl_ann)} in the implementation"
            )

    port_ret = port_hints.get("return", port_sig.return_annotation)
    impl_ret = impl_hints.get("return", impl_sig.return_annotation)
    if not _annotations_compatible(port_ret, impl_ret):
        problems.append(
            f"the port returns {_render(port_ret)} and the implementation returns "
            f"{_render(impl_ret)}"
        )
    return problems


def _documented_exceptions(docstring: str | None) -> set[str]:
    """The exception names in a docstring's ``Raises:`` section."""
    if not docstring:
        return set()
    names: set[str] = set()
    in_section = False
    section_indent = 0
    for line in docstring.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not in_section:
            if stripped == "Raises:":
                in_section = True
                section_indent = indent
            continue
        if not stripped:
            continue
        if indent <= section_indent:
            break
        match = _RAISES_ENTRY.match(stripped)
        if match:
            names.add(match.group(1))
    return names


def _raised_exception_names(func: types.FunctionType) -> list[str]:
    """The names of the exception classes ``func`` can raise.

    Found by parsing the source rather than by running it, so a raise that only
    happens on an error path is still counted. A bare ``raise`` (a re-raise of
    the exception being handled) is skipped: it raises whatever the handler
    caught, which is not a name this function chooses.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):  # pragma: no cover - a source-less function raises nothing
        return []
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _public_name(exc_type: type) -> str | None:
    """The name of ``exc_type`` or of the nearest public ancestor of it.

    The adapter's own private subclasses (``_MissingApproval``) are not part of
    any contract a caller can be expected to name; the public base they stand
    for (``NotFoundError``) is.
    """
    for base in exc_type.__mro__:
        name = base.__name__
        if name.startswith("_") or base in (object, BaseException, Exception):
            continue
        return name
    return None


@pytest.mark.parametrize(
    ("port", "module_name", "class_name"),
    IMPLEMENTATIONS,
    ids=[f"{port.__name__}-{class_name}" for port, _, class_name in IMPLEMENTATIONS],
)
def test_a_port_method_is_implemented_with_the_same_signature(
    port: type, module_name: str, class_name: str
) -> None:
    """The adapter implements the port's method, with the port's signature.

    A port that describes a method the implementation does not have, or has
    with different parameter names, keyword-only-ness, defaults or return
    type, is a port that no longer describes the code.
    """
    module = importlib.import_module(module_name)
    implementation = getattr(module, class_name)
    port_ns = vars(sys.modules[port.__module__])

    problems: list[str] = []
    for name, port_func in _port_methods(port):
        impl_func = getattr(implementation, name, None)
        if impl_func is None:
            problems.append(f"{name} is declared on the port but not implemented")
            continue
        diff = _signature_diff(port_func, impl_func, port_ns, vars(module))
        problems.extend(f"{name}: {problem}" for problem in diff)
    assert not problems, (
        f"{class_name} does not implement {port.__name__} as the port declares it: "
        f"{problems}. A port is the contract; an implementation that drifts from "
        "it silently changes what callers may rely on."
    )


@pytest.mark.parametrize(
    ("port", "module_name", "class_name"),
    IMPLEMENTATIONS,
    ids=[f"{port.__name__}-{class_name}" for port, _, class_name in IMPLEMENTATIONS],
)
def test_every_domain_error_a_port_method_raises_is_documented_on_the_port(
    port: type, module_name: str, class_name: str
) -> None:
    """An implementation cannot fail in a way the port does not name.

    The domain's error vocabulary is shared, so a caller reading only the port
    must be able to see which failures it has to handle. ``ApprovalExpired`` is
    the one this file was written for: the port named two of the three failures
    ``decide`` can produce, and the one it left out is the one the API answers
    with a *different* status code.
    """
    module = importlib.import_module(module_name)
    implementation = getattr(module, class_name)

    problems: list[str] = []
    for name, port_func in _port_methods(port):
        impl_func = getattr(implementation, name, None)
        if impl_func is None:
            continue
        documented = _documented_exceptions(inspect.getdoc(port_func))
        for raised in _raised_exception_names(cast("types.FunctionType", impl_func)):
            exc_type = getattr(module, raised, None)
            if not isinstance(exc_type, type):
                continue
            if not issubclass(exc_type, domain_errors.DomainError):
                # Not the shared domain vocabulary: an adapter's own error type
                # (a provider's ``StructuredOutputError``) is not something the
                # port is expected to name.
                continue
            public = _public_name(exc_type)
            if public is None:
                continue
            if public not in documented:
                problems.append(
                    f"{name} can raise {raised} (a {public}) but the port's "
                    f"``Raises:`` section names {sorted(documented) or 'nothing'}"
                )
    assert not problems, (
        f"{class_name} can fail in ways {port.__name__} does not document: {problems}. "
        "A caller that reads the port catches the failures the port names; one it "
        "does not name escapes as a 500 instead of the answer the contract promises."
    )
