"""``CompositeToolGateway``: one gateway that serves every registered tool.

Responsibility: hold the two halves of tool dispatch -- the in-process
:class:`~opspilot.adapters.tools.knowledge_tool.KnowledgeTool` and the
:class:`~opspilot.adapters.tools.mcp_gateway.MCPToolGateway` -- and route each
call to whichever one owns the tool's ``server``, so the runtime's single
``gateway.call_tool`` site reaches every registered tool.

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``, like
both halves.

Why a composite rather than routing inside ``MCPToolGateway``
-----------------------------------------------------------

``docs/mcp-contracts.md`` §4 fixes ``knowledge.search`` as ``server="internal"``:
in-process, not a fourth MCP server. The MCP gateway honours that by *refusing*
the tool -- ``_server_for("internal")`` returns ``None`` and the call becomes
``server_unavailable`` -- and
``tests/integration/test_knowledge_is_not_mcp.py`` asserts that refusal on
purpose. Teaching the gateway to serve the tool would contradict both the
document and its test.

So the routing has to live somewhere that is not the MCP gateway, and this is
that somewhere: it is the composition root's own concern ("this deployment has
these backends"), which is why it belongs in ``adapters/`` beside the two halves
rather than in ``agents/`` (which must not import adapters) or ``domain/``.

What the routing key is, and is not
-----------------------------------

The key is ``TOOL_REGISTRY[name].server``, read at call time from the one static
registry that already holds every tool's authority. It is deliberately *not* a
name allow-list inside this module: a second list would be a second place to
forget a tool, and the registry is the thing
``tests/security/test_permission_immutability.py`` already pins. A name that is
not registered at all falls through to the MCP half, which refuses it with
``unknown_tool`` -- the same answer it gave before this existed.

Both halves are asked for their tool lists and the answers are *unioned*, not
replaced. Neither half is told it owns the registry: that is what makes the
union a real cross-check (a tool listed by neither half would be visible here)
rather than a second registry with extra steps.
"""

from __future__ import annotations

import contextlib
from typing import Final

from opspilot.domain.tools import TOOL_REGISTRY, ToolSpec
from opspilot.ports.tool_gateway import ToolGateway, ToolResult

__all__ = ["CompositeToolGateway"]

# The ``ToolSpec.server`` value that marks a tool as dispatched in-process rather
# than through an MCP server. Read from the registry, so a new internal tool is
# routed without editing this module.
_INTERNAL_SERVER: Final[str] = "internal"


class CompositeToolGateway:
    """Routes each registered tool to the half that owns its ``server``.

    Args:
        internal: The gateway for ``server="internal"`` tools -- in practice a
            :class:`KnowledgeTool`. It is asked first, and it answers for nothing
            but the tools it implements; every other name goes to ``mcp``.
        mcp: The MCP gateway, for the tools whose ``server`` names one of the
            three external servers. It also receives unregistered names, so its
            ``unknown_tool`` refusal still happens where it always did.
    """

    def __init__(self, *, internal: ToolGateway, mcp: ToolGateway) -> None:
        self._internal = internal
        self._mcp = mcp

    async def list_tools(self) -> list[ToolSpec]:
        """Return the union of both halves' tool lists, without duplicates.

        Both halves currently answer with the static registry, so a naive
        concatenation would list every tool twice and ``list_tools`` would look
        like it discovered two gateways' worth of tools. Deduplicating by name --
        keeping the first occurrence, so the order is deterministic rather than
        set-ordered -- is what makes the union honest.

        The result is still *derived* from the halves rather than returned from
        ``TOOL_REGISTRY`` directly: the registry says what a tool *is*, and
        neither gateway is claiming authority over it, but this is a list of the
        tools that can be executed and a half that stopped serving one is a fact
        a caller of this method would want to see.
        """
        union: dict[str, ToolSpec] = {}
        for tools in (await self._internal.list_tools(), await self._mcp.list_tools()):
            for spec in tools:
                union.setdefault(spec.name, spec)
        return list(union.values())

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Dispatch ``name`` to the half that owns its ``server``.

        Only ever reached after the five gates have passed -- the composite is a
        router, not a gate, and it makes no decision the gates have not already
        made. It reads the registry to choose a half and delegates entirely,
        which is why both halves' "errors are results" contracts survive it.
        """
        spec = TOOL_REGISTRY.get(name)
        if spec is not None and spec.server == _INTERNAL_SERVER:
            return await self._internal.call_tool(name, arguments)
        return await self._mcp.call_tool(name, arguments)

    async def aclose(self) -> None:
        """Close both halves, tolerating one that has nothing to close.

        Duck-typed per half for the reason ``worker/__main__.py::_close_gateway``
        documents: ``ToolGateway`` is a ``Protocol`` whose two methods say
        nothing about a close, and the in-process knowledge tool genuinely has no
        transport to release. Each half's own ``aclose`` already swallows its own
        teardown errors; this method's only job is to reach both, because on the
        stdio transport the MCP half owns child processes that must not outlive
        the worker.
        """
        for half in (self._internal, self._mcp):
            close = getattr(half, "aclose", None)
            if close is None:
                continue
            with contextlib.suppress(Exception):
                await close()
