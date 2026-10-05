"""``MCPToolGateway``: the ``ToolGateway`` port over the MCP servers.

Responsibility: own the mapping from a registered tool name to the MCP server
that exposes it, dispatch a call through that server's *own* ``call_tool``, and
translate the protocol result into the port's :class:`ToolResult`. The MCP
transport is invisible above this file -- ``domain/`` and ``agents/`` see only
``opspilot.ports.tool_gateway`` (``docs/architecture.md`` §8).

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``.

This module is the single named relaxation of mypy's
``disallow_untyped_decorators`` (see ``pyproject.toml``) because the MCP SDK's
type stubs are incomplete.

Transport choice: in-process dispatch
-------------------------------------

The gateway builds each MCP server in-process (``billing.server.create_server()``
and its siblings) and calls ``await server.call_tool(name, arguments)``. That
path goes through the server's registered tool, the generated JSON Schema, the
``structured_output`` serialisation and the ``CallToolResult`` envelope -- the
whole MCP layer is genuinely exercised -- while skipping only the stdio pipe.

The alternative is a real subprocess over stdio
(``MCP_BILLING_COMMAND`` and friends), which is closer to the Compose
deployment. It is not the default here for three reasons:

1. **Hermeticity.** A subprocess server owns its own on-disk store, so a test
   that issues a refund mutates a file shared with the next test unless every
   test gets a private ``OPSPILOT_MCP_DATA_DIR``. In-process construction lets a
   test inject an isolated ``Store`` (see :func:`build_in_process_servers`),
   which is what makes the gateway unit-testable without cross-test leakage.
2. **Cost.** Each subprocess start is tens of milliseconds of interpreter boot
   plus an MCP handshake; the contract suite would pay it per test.
3. **Failure surface.** A broken pipe is an opaque ``BrokenPipeError`` far from
   the call that caused it, which is a worse failure than an in-process one for
   the layer whose whole job is to *return* errors rather than raise them.

What in-process does **not** cover is the transport itself -- stdio framing,
the subprocess handshake, the wire serialisation. That is why
``tests/integration/test_mcp_*.py`` starts each server as a real subprocess and
lists its tools over the wire once. In-process for behaviour, out-of-process for
the claim that these are MCP servers rather than functions with a decorator
(``docs/mcp-sdk-notes.md`` §6).

Errors are results
------------------

A tool that fails is a :class:`ToolResult` with ``ok=False`` and the server's
structured error ``code`` (``not_found``, ``invalid_state``,
``amount_exceeds_transaction``, ``validation_error``) in ``error`` -- never a
raised exception. A raised exception becomes an opaque SDK transport error with
the code lost (``docs/mcp-sdk-notes.md`` §5), and the agent could then not tell
``invalid_state`` from a crash, which is exactly the distinction the
"already refunded -> do not refund again" scenario depends on.

The one exception is a *protocol-layer* rejection, such as a call omitting the
schema-required ``idempotency_key``: the SDK raises ``ToolError`` before our code
runs (gate 1 enforced by the transport). That is caught here and returned as a
``validation_error`` result, so the caller still sees a result rather than a
raise.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult

from opspilot.domain.tools import TOOL_REGISTRY, ToolSpec
from opspilot.ports.tool_gateway import ToolResult

if TYPE_CHECKING:
    from opspilot.adapters.retrieval.search import RetrievalOutcome

__all__ = ["MCPToolGateway", "build_in_process_servers"]

# The knowledge retriever the gateway may be handed: given a query and a bounded
# top-k, return the shared retrieval outcome. Structural rather than imported
# from ``mcp_servers.knowledge.server`` so this adapter does not depend on a
# server module at module scope (the server import is function-local, like the
# other three); the shape is the server's ``Retriever`` alias.
type _Retriever = Callable[[str, int], Awaitable["RetrievalOutcome"]]

# The SDK's ``call_tool`` return union. Named so the narrowing helper and the
# call site agree on the two arms without repeating the union.
CallToolReturn = CallToolResult | InputRequiredResult

# Every server the gateway can dispatch to. ``crm`` / ``billing`` / ``issues``
# are the external demo servers; ``knowledge`` is in-process rather than a
# sibling process (``docs/mcp-contracts.md`` §4). A ``server`` value that is not
# here (or an alias below) is a misconfiguration and is refused as a result
# rather than raised.
_KNOWN_SERVERS: frozenset[str] = frozenset({"crm", "billing", "issues", "knowledge"})

# ``knowledge.search``'s ``TOOL_REGISTRY`` spec names its server ``internal``
# (``domain/tools.py``, transcribed from ``docs/tool-permissions.md`` §2), to say
# "not one of the three external MCP servers". The gateway's server map is keyed
# by the MCP server name, which is ``knowledge``. Translating here is what lets
# gate 2's registry lookup and the dispatch agree without editing the static
# registry -- a registry key is a permission-bearing fact and is not the place to
# accommodate a transport detail.
_SERVER_ALIASES: dict[str, str] = {"internal": "knowledge"}

# The structured error code every refusal honours. Present, non-``None``, on a
# failed result and ``None`` on a success (``docs/mcp-contracts.md`` §2).
_ERROR_CODE_FIELD = "code"


def build_in_process_servers(
    data_dir: object = None,
    *,
    retriever: _Retriever | None = None,
) -> dict[str, MCPServer]:
    """Build the in-process MCP servers, each backed by an isolated store.

    Args:
        data_dir: Directory for each external server's JSON store file. When
            ``None`` each server builds its default store, which honours
            ``OPSPILOT_MCP_DATA_DIR``. A test passes ``tmp_path`` so a mutation
            cannot leak between tests.
        retriever: Optional ``(query, top_k) -> RetrievalOutcome`` callable for
            the knowledge server. When ``None`` the knowledge server is still
            built and callable and answers ``not_configured``
            (``mcp_servers/knowledge/server.py``); the application supplies the
            real one, which binds the embedder and vector store in the
            deployment.

    Returns:
        ``{"billing": ..., "crm": ..., "issues": ..., "knowledge": ...}``.

    Notes:
        ``billing`` and ``issues`` expose a ``create_server(store)`` factory;
        ``crm`` exposes only a module-level ``server`` built at import time. The
        factory path is preferred where it exists because it is what lets a test
        inject an isolated store; for ``crm`` the module-level server is used as
        is, since it is read-only and therefore cannot leak a mutation.

        The imports are local to the function so importing this adapter does not
        import four server modules (and their Pydantic models) at process start.
    """
    from pathlib import Path

    from mcp_servers._store import Store
    from mcp_servers.billing import server as billing_server
    from mcp_servers.crm import server as crm_server
    from mcp_servers.issues import server as issues_server
    from mcp_servers.knowledge import server as knowledge_server

    servers: dict[str, MCPServer] = {"crm": crm_server.server}

    directory = Path(str(data_dir)) if data_dir is not None else None
    billing = billing_server.create_server(
        Store(
            Path(billing_server.__file__).parent / "seed.json",
            filename="billing.json",
            data_dir=directory,
        )
    )
    issues = issues_server.create_server(
        Store(
            Path(issues_server.__file__).parent / "seed.json",
            filename="issues.json",
            data_dir=directory,
        )
    )
    servers["billing"] = billing
    servers["issues"] = issues
    servers["knowledge"] = knowledge_server.create_server(retriever=retriever)
    return servers


class MCPToolGateway:
    """Executes registered tools by calling the MCP servers.

    Args:
        servers: Optional pre-built map of server name to ``MCPServer``. When
            omitted, the gateway builds all in-process servers -- the three
            external ones and the internal knowledge one -- on first use
            (lazily, so constructing the gateway is cheap and cannot fail on a
            missing seed file at import).

    A gateway instance is used from one event loop; it holds no per-call state
    beyond the server map, so it is safe to reuse across calls within a run.
    """

    def __init__(
        self,
        *,
        servers: Mapping[str, MCPServer] | None = None,
        server_factory: Callable[[], Mapping[str, MCPServer]] | None = None,
    ) -> None:
        self._servers: Mapping[str, MCPServer] | None = servers
        self._server_factory = server_factory or build_in_process_servers

    async def list_tools(self) -> list[ToolSpec]:
        """Return the ``ToolSpec``s from the static ``TOOL_REGISTRY``.

        The registry is the source of truth for permissions -- the servers
        provide behaviour, not authority. This deliberately does **not** build a
        second registry by reading the servers' ``list_tools``: a permission
        inferred from what a server happens to expose is a permission that a
        server change could widen. The servers' tool lists are cross-checked
        against the registry by ``tests/integration/test_mcp_contract_names.py``
        instead.
        """
        return list(TOOL_REGISTRY.values())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Dispatch ``name`` to its owning server and return a ``ToolResult``.

        Only ever reached after the five gates pass. A refusal -- whether a
        structured server error or a protocol-layer validation rejection -- is
        returned as a result with ``ok=False``, never raised.
        """
        spec = TOOL_REGISTRY.get(name)
        started = time.perf_counter()

        if spec is None:
            return ToolResult(
                tool_name=name,
                ok=False,
                error="unknown_tool",
                latency_ms=_elapsed_ms(started),
            )

        server = self._server_for(spec.server)
        if server is None:
            return ToolResult(
                tool_name=name,
                ok=False,
                error="server_unavailable",
                latency_ms=_elapsed_ms(started),
            )

        try:
            raw = await server.call_tool(name, dict(arguments))
        except ToolError as exc:
            # A protocol-layer rejection: the arguments failed the tool's
            # generated JSON Schema (e.g. a missing required ``idempotency_key``)
            # and the SDK raised before our tool body ran. It is gate 1 firing at
            # the transport, so it is a validation_error result, not a crash.
            return ToolResult(
                tool_name=name,
                ok=False,
                error="validation_error",
                result={"code": "validation_error", "message": str(exc)},
                latency_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            # Any other transport failure is reported as a result carrying a
            # machine-stable token, so the runtime can map it to
            # ``FAILED(mcp_unavailable)`` without seeing an exception type it was
            # not written to catch. The narrow-``Exception`` catch is deliberate:
            # the layer's contract is "errors are results", and a leaked
            # exception here would violate it.
            return ToolResult(
                tool_name=name,
                ok=False,
                error="mcp_unavailable",
                result={"code": "mcp_unavailable", "message": str(exc)},
                latency_ms=_elapsed_ms(started),
            )

        result = _narrow(raw)
        return _to_tool_result(name, result, started)

    async def aclose(self) -> None:
        """Release in-process servers.

        In-process servers hold no transport resources, so there is nothing to
        close; the method exists so a caller (the worker's shutdown path) can
        ``await`` a uniform cleanup whether it holds an in-process or a
        subprocess gateway. It is intentionally a no-op rather than an error, so
        shutdown is never blocked by this adapter.
        """
        self._servers = None

    # -- internals -------------------------------------------------------

    def _server_for(self, server_name: str) -> MCPServer | None:
        """Return the server for a ``ToolSpec.server`` value, building on first use.

        ``server_name`` is the registry's ``server`` field, not necessarily the
        MCP server's own name: ``knowledge.search``'s spec says ``internal`` and
        the alias map translates that to the ``knowledge`` server. An unknown
        name returns ``None``, which the caller reports as ``server_unavailable``
        rather than raising.
        """
        resolved = _SERVER_ALIASES.get(server_name, server_name)
        if resolved not in _KNOWN_SERVERS:
            return None
        if self._servers is None:
            self._servers = dict(self._server_factory())
        return self._servers.get(resolved)


def _narrow(raw: CallToolReturn) -> CallToolResult:
    """Narrow the ``call_tool`` union to ``CallToolResult``.

    ``call_tool`` is declared as returning ``CallToolResult |
    InputRequiredResult`` (the latter is the elicitation path -- a server asking
    the client for more input). None of OpsPilot's tools elicit, so a value of
    the other arm is a contract violation; it is reported as a structured error
    result rather than raised, keeping the "errors are results" invariant.
    """
    if isinstance(raw, CallToolResult):
        return raw
    # The remaining arm is the elicitation path. It is reported as a structured
    # error result rather than raised, keeping the "errors are results" invariant.
    # The union is exhaustive, so no third arm exists to handle.
    return CallToolResult(  # pragma: no cover - no OpsPilot tool elicits
        content=[],
        is_error=True,
        structured_content={
            "code": "elicitation_unsupported",
            "message": "server requested elicitation; OpsPilot tools never elicit",
        },
    )


def _to_tool_result(name: str, result: CallToolResult, started: float) -> ToolResult:
    """Project a ``CallToolResult`` onto the port's ``ToolResult``.

    A structured error is one whose ``structured_content`` carries a non-``None``
    ``code`` (the shape every server's refusal uses, per
    ``docs/mcp-contracts.md`` §2). Such a result is ``ok=False`` with the code as
    ``error``. A success carries ``code: None`` and is ``ok=True``.
    """
    payload = result.structured_content if isinstance(result.structured_content, dict) else None
    code = payload.get(_ERROR_CODE_FIELD) if payload is not None else None

    if result.is_error or code:
        return ToolResult(
            tool_name=name,
            ok=False,
            result=payload,
            error=str(code) if code else "tool_error",
            latency_ms=_elapsed_ms(started),
        )
    return ToolResult(
        tool_name=name,
        ok=True,
        result=payload,
        latency_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> int:
    """Milliseconds elapsed since ``started`` (a ``perf_counter`` reading)."""
    return int((time.perf_counter() - started) * 1000)
