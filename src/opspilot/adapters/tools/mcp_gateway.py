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

Transport choice: in-process dispatch, or stdio
-----------------------------------------------

The gateway calls ``await server.call_tool(name, arguments)`` on whatever object
the server map holds. Both transports satisfy that one method, and the gateway
cannot tell them apart -- which is the property ``docs/architecture.md`` §8
claims for this layer, so a second dispatch path would be the transport leaking.

**In-process is the default** (``MCP_TRANSPORT=inprocess``, and
:func:`build_in_process_servers`): it builds each MCP server in this process
(``billing.server.create_server()`` and its siblings) and calls it directly. That
path goes through the server's registered tool, the generated JSON Schema, the
``structured_output`` serialisation and the ``CallToolResult`` envelope -- the
whole MCP layer is genuinely exercised -- while skipping only the stdio pipe.
It is not the default by accident:

1. **It must not change what the tests exercise.** The whole suite builds the
   gateway in-process. Flipping the default would replace the thing most tests
   are testing with a subprocess, which makes them pass for the wrong reason.
2. **Hermeticity.** A subprocess server owns its own on-disk store, so a test
   that issues a refund mutates a file shared with the next test unless every
   test gets a private ``OPSPILOT_MCP_DATA_DIR``. In-process construction lets a
   test inject an isolated ``Store`` (see :func:`build_in_process_servers`),
   which is what makes the gateway unit-testable without cross-test leakage.
3. **Cost.** Each subprocess start is interpreter boot plus an MCP handshake;
   the contract suite would pay it per test.

**stdio is opt-in** (``MCP_TRANSPORT=stdio``): the gateway reads
``MCP_CRM_COMMAND`` and its siblings and spawns each as a real child process
(:mod:`opspilot.adapters.tools.mcp_stdio`). It is what in-process does **not**
cover -- stdio framing, the subprocess handshake, the wire serialisation -- and
it is therefore the only path that can honestly claim these are MCP servers
rather than functions with a decorator (``docs/mcp-sdk-notes.md`` §6). So it is
the path `tests/integration/test_mcp_stdio_transport.py` exercises for real, and
the path an operator selects when they want the transport to be the thing under
test rather than a detail skipped for speed.

The trade is real and is stated rather than hidden: a stdio server dying
mid-run is a closed pipe, which is a worse failure to diagnose than an
in-process traceback. That is exactly why ``adapters/tools/mcp_stdio.py`` gives
the spawn its own named error and why the gateway below converts it into the
same ``mcp_unavailable`` result it would produce for any other transport fault.

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

import contextlib
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult

from opspilot.domain.tools import TOOL_REGISTRY, ToolSpec
from opspilot.ports.tool_gateway import ToolResult

if TYPE_CHECKING:
    from opspilot.adapters.tools.mcp_stdio import StdioServerProcess
    from opspilot.settings import Settings

__all__ = ["MCPToolGateway", "build_in_process_servers"]

# The SDK's ``call_tool`` return union. Named so the narrowing helper and the
# call site agree on the two arms without repeating the union.
CallToolReturn = CallToolResult | InputRequiredResult


@runtime_checkable
class DispatchableServer(Protocol):
    """What the gateway needs from a server in order to dispatch a tool.

    Declared here rather than typed as ``MCPServer`` because the stdio path's
    server is not one: it is a ``StdioServerProcess`` that spawns a child and
    forwards the call over a pipe. Both satisfy ``call_tool``, and that is the
    whole contract -- ``docs/architecture.md`` §8 says the transport is invisible
    above this adapter, so the gateway dispatches on this and learns nothing
    about how the tool was actually executed.

    ``aclose`` is deliberately **not** part of this protocol. An in-process
    ``MCPServer`` has no close method at all -- it holds no transport resource,
    which is why the gateway's own ``aclose`` was originally a no-op -- so
    requiring it here would exclude the default path by typing alone. Shutdown
    is instead duck-typed by :meth:`MCPToolGateway.aclose`, which asks each
    server whether it can be closed and leaves one that cannot alone. That is the
    honest shape of the difference: an in-process server has nothing to release,
    a spawned one has a child to reap.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolReturn: ...


# Every server the gateway can dispatch to. Exactly the three external demo
# servers. There is deliberately **no** ``knowledge`` entry: ``knowledge.search``
# is ``server="internal"`` in ``TOOL_REGISTRY`` and is dispatched in-process by
# the knowledge tool, not over MCP (``docs/mcp-contracts.md`` §4 -- "The
# ``knowledge`` tool is not an MCP server"). A ``server`` value that is not here
# is a misconfiguration and is refused as a result rather than raised.
_KNOWN_SERVERS: frozenset[str] = frozenset({"crm", "billing", "issues"})

# The setting name suffix each server's command is read from. The settings are
# ``MCP_CRM_COMMAND`` etc., and the gateway looks them up by this table rather
# than by ``getattr(settings, f"mcp_{name}_command")`` so that adding a server to
# ``_KNOWN_SERVERS`` without giving it a setting is a ``KeyError`` at startup
# rather than a server that silently has no command.
_SERVER_SETTING_SUFFIX: dict[str, str] = {
    "crm": "MCP_CRM_COMMAND",
    "billing": "MCP_BILLING_COMMAND",
    "issues": "MCP_ISSUES_COMMAND",
}

# The module each server is run as under the stdio transport. Used only to
# rebuild a command for a setting left blank; a server added to
# ``_KNOWN_SERVERS`` without a module here is a ``KeyError`` at startup rather
# than a server whose default command silently names the wrong thing.
_SERVER_MODULE: dict[str, str] = {
    "crm": "mcp_servers.crm.server",
    "billing": "mcp_servers.billing.server",
    "issues": "mcp_servers.issues.server",
}

# The structured error code every refusal honours. Present, non-``None``, on a
# failed result and ``None`` on a success (``docs/mcp-contracts.md`` §2).
_ERROR_CODE_FIELD = "code"


def build_stdio_servers_from_settings(settings: object) -> dict[str, StdioServerProcess]:
    """Read the ``MCP_*_COMMAND`` settings into lazily-started server handles.

    This is the function that makes those settings real: it is what the
    composition root calls when ``MCP_TRANSPORT=stdio``, and the values it reads
    are the only place ``MCP_CRM_COMMAND`` & co. are consumed anywhere in the
    code base.

    Each handle parses its command eagerly and spawns nothing until its first
    tool call, so a gateway can be constructed outside an event loop and a
    deployment with three servers does not pay three interpreter boots to answer
    one read.

    Args:
        settings: An ``opspilot.settings.Settings``. Typed as ``object`` for the
            same reason the sibling builders are: the adapter module must not
            import the settings object at module scope, because the settings
            module is the one thing every layer imports and this one is reached
            from a lazily-built worker.

    Returns:
        A map the gateway can use in place of :func:`build_in_process_servers`.

    Raises:
        MCPServerSpawnError: If any command is empty or unbalanced, naming the
            setting that is wrong.
    """
    # Imported here, not at module scope, for the same reason the in-process
    # builder imports its servers inside the function: a deployment that never
    # selects the stdio transport must not pay for importing the MCP *client*
    # half of the SDK at every process start.
    from opspilot.adapters.tools.mcp_stdio import build_stdio_servers
    from opspilot.settings import Settings

    assert isinstance(settings, Settings)
    commands = {name: _command_for(settings, name) for name in sorted(_KNOWN_SERVERS)}
    return build_stdio_servers(commands)


def _command_for(settings: Settings, server_name: str) -> str:
    """A server's configured command, or the default when it was left blank.

    ``.env.example`` ships all three as empty -- an operator with no opinion
    writes an empty line -- so blank has to mean "the obvious thing" rather than
    an error, exactly as ``retrieval_min_score`` and ``worker_id`` already treat
    it. The default is rebuilt here rather than read from the field because
    ``pydantic-settings`` substitutes the field default for a variable that is
    *absent*, which an empty line in a ``.env`` is not: it arrives as ``""``.
    """
    from opspilot.settings import default_mcp_server_command

    configured = str(getattr(settings, _setting_field(server_name))).strip()
    return configured or default_mcp_server_command(_SERVER_MODULE[server_name])


def _setting_field(server_name: str) -> str:
    """The ``Settings`` attribute name for a server's command.

    Derived from ``_SERVER_SETTING_SUFFIX`` by lower-casing the environment name,
    so the table above is the single place the two spellings are kept in step.
    """
    return _SERVER_SETTING_SUFFIX[server_name].lower()


def build_in_process_servers(data_dir: object = None) -> dict[str, MCPServer]:
    """Build the in-process MCP servers, each backed by an isolated store.

    Args:
        data_dir: Directory for each external server's JSON store file. When
            ``None`` each server builds its default store, which honours
            ``OPSPILOT_MCP_DATA_DIR``. A test passes ``tmp_path`` so a mutation
            cannot leak between tests.

    Returns:
        ``{"billing": ..., "crm": ..., "issues": ...}``.

    Notes:
        ``billing`` and ``issues`` expose a ``create_server(store)`` factory;
        ``crm`` exposes only a module-level ``server`` built at import time. The
        factory path is preferred where it exists because it is what lets a test
        inject an isolated store; for ``crm`` the module-level server is used as
        is, since it is read-only and therefore cannot leak a mutation.

        The imports are local to the function so importing this adapter does not
        import three server modules (and their Pydantic models) at process start.
    """
    from pathlib import Path

    from mcp_servers._store import Store
    from mcp_servers.billing import server as billing_server
    from mcp_servers.crm import server as crm_server
    from mcp_servers.issues import server as issues_server

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
    return servers


def _default_server_factory() -> Callable[[], Mapping[str, DispatchableServer]]:
    """The in-process factory, named so the ``or`` in the constructor can go.

    ``build_in_process_servers`` takes an optional ``data_dir``, so its type is
    ``Callable[[object], ...]`` rather than ``Callable[[], ...]`` and it is not
    assignable to the factory attribute directly. Wrapping it here is narrower
    than widening the constructor's parameter type to accept both shapes, which
    would let a caller pass a factory that ignores its only argument.
    """

    def factory() -> Mapping[str, DispatchableServer]:
        return build_in_process_servers()

    return factory


class MCPToolGateway:
    """Executes registered tools by calling the MCP servers.

    Args:
        servers: Optional pre-built map of server name to something satisfying
            :class:`DispatchableServer`. When omitted, the gateway builds the
            three external servers on first use (lazily, so constructing the
            gateway is cheap and cannot fail on a missing seed file at import).
        server_factory: The factory used when ``servers`` is omitted. Defaults
            to the in-process path; :func:`build_stdio_servers_from_settings`
            is the opt-in one, and the two produce interchangeable maps.

    A gateway instance is used from one event loop; it holds no per-call state
    beyond the server map, so it is safe to reuse across calls within a run.
    """

    def __init__(
        self,
        *,
        servers: Mapping[str, DispatchableServer] | None = None,
        server_factory: Callable[[], Mapping[str, DispatchableServer]] | None = None,
    ) -> None:
        self._servers: Mapping[str, DispatchableServer] | None = servers
        self._server_factory: Callable[[], Mapping[str, DispatchableServer]] = (
            server_factory if server_factory is not None else _default_server_factory()
        )

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
        """Release every server this gateway holds.

        In-process servers hold no transport resources and have no close method,
        so they are left alone. A spawned stdio server is a child process and is
        terminated here: this is what stops a stdio deployment leaving a
        subprocess per run, and it is why the worker's shutdown path calls it.

        Duck-typed rather than declared on :class:`DispatchableServer` because
        ``MCPServer`` has no such method, and making the protocol require it would
        exclude the default transport by typing alone. Idempotent, and safe when
        no tool was ever dispatched -- nothing was spawned and there is nothing
        to reap.
        """
        servers, self._servers = self._servers, None
        if servers is None:
            return
        for server in servers.values():
            close = getattr(server, "aclose", None)
            if close is None:
                continue
            with contextlib.suppress(Exception):
                await close()

    # -- internals -------------------------------------------------------

    def _server_for(self, server_name: str) -> DispatchableServer | None:
        """Return the server for a ``ToolSpec.server`` value, building on first use.

        ``server_name`` is the registry's ``server`` field. A value that is not
        one of the three external servers -- including ``knowledge.search``'s
        ``internal`` -- returns ``None``, which the caller reports as
        ``server_unavailable`` rather than raising. This is correct for
        ``knowledge.search``: it is dispatched in-process by the knowledge tool
        (``docs/mcp-contracts.md`` §4), never by this gateway, so a dispatch
        attempt reaching here for it is a wiring bug that should fail loudly
        rather than silently gain an MCP server.
        """
        if server_name not in _KNOWN_SERVERS:
            return None
        if self._servers is None:
            self._servers = dict(self._server_factory())
        return self._servers.get(server_name)


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
            result=payload if payload is not None else {"message": _error_text(result)},
            error=str(code) if code else "tool_error",
            latency_ms=_elapsed_ms(started),
        )
    return ToolResult(
        tool_name=name,
        ok=True,
        result=payload,
        latency_ms=_elapsed_ms(started),
    )


def _error_text(result: CallToolResult) -> str:
    """The message on an ``is_error`` result that carried no structured payload.

    Needed because the two transports reject a schema-invalid call at *different
    layers*. In-process, the SDK raises ``ToolError`` before the tool body runs
    and :meth:`MCPToolGateway.call_tool` maps it to ``validation_error``. Over
    stdio the same rejection is a normal ``CallToolResult`` with ``is_error`` set
    and no ``structured_content``, because it happened in the child's transport
    rather than in ours. Reporting the second as a bare ``tool_error`` would mean
    the same mistake -- calling ``billing.issue_refund`` with no
    ``idempotency_key`` -- got two different codes depending on a deployment's
    transport, which is precisely the kind of drift this layer exists to prevent.

    The message is carried through rather than discarded so the failure is still
    diagnosable; it is what names the offending argument.
    """
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return "the server returned an error with no detail"


def _elapsed_ms(started: float) -> int:
    """Milliseconds elapsed since ``started`` (a ``perf_counter`` reading)."""
    return int((time.perf_counter() - started) * 1000)
