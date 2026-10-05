"""``KnowledgeTool``: the in-process ``knowledge.search`` dispatcher.

Responsibility: dispatch the one registered tool that is *not* an MCP server --
``knowledge.search`` -- and return the same :class:`ToolResult` shape every other
tool returns, so the runtime's ``_gate_and_execute`` does not care which kind it
is calling. It lives beside ``MCPToolGateway`` because it is the in-process half
of the same boundary: "execute a tool that already passed the five gates".

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``, so a
caller may hold either it or ``MCPToolGateway`` through the port. It imports the
retrieval adapter (search + vector store) because retrieval *is* its backend.

Why this is not an MCP server
-----------------------------

``docs/mcp-contracts.md`` §4 is titled **"The ``knowledge`` tool is not an MCP
server"** and states the contract directly::

    `knowledge.search` is registered in the tool registry with permission `READ`
    and `server="internal"`. It is implemented in-process against pgvector
    rather than as a fourth MCP server.

The reasoning is in the same section: retrieval is not an external system. It
feeds the *prompt assembly* step, not the *action* step, and modelling it as a
remote service "would obscure" that. A previous slice built a fourth MCP server
(a real ``MCPServer`` object with its own ``call_tool`` envelope); its nine tests
passed because they verified the server was built *correctly*, never whether it
should be built at all. This module is the replacement: the same tool, dispatched
in-process, with no MCP envelope and no ``MCPServer`` object.

The permission machinery still covers it
-----------------------------------------

It is still a registered ``ToolSpec`` (``TOOL_REGISTRY["knowledge.search"]`` with
``Permission.READ`` and ``server="internal"``), so gate 1 validates its arguments
with ``KnowledgeSearchArgs``, gate 2 finds its spec in the registry and gate 3
reads its static ``READ`` permission exactly as for any MCP tool. Only the
*dispatch* differs -- and dispatch happens after the gates, never instead of
them (``docs/tool-permissions.md`` §3).

Errors are results, not exceptions
-----------------------------------

The gateway's contract is that a failure is a ``ToolResult`` with ``ok=False``
and a machine-stable ``error``, never a raised exception, because a raise becomes
an opaque transport error with the code lost and the agent can then not branch on
it. This mirrors that: an unconfigured backend and a backend that raises are both
results, and the codes are distinct so "no index bound" is never confused with
"the index found nothing".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from opspilot.adapters.retrieval.search import RetrievalOutcome
from opspilot.ports.tool_gateway import ToolResult

__all__ = ["KnowledgeTool"]

# The registered name this tool dispatches. A constant so a typo cannot quietly
# make the tool answer for a name gate 2 would reject anyway.
_TOOL_NAME: Final[str] = "knowledge.search"

# The error code when no retrieval backend is bound. The M5 knowledge server used
# the same token; it is kept because the distinction it draws is the one the
# server's docstring argued for -- "index not configured" is not "found nothing".
_NOT_CONFIGURED: Final[str] = "not_configured"

# The error code when the backend raised. A stable token rather than the
# exception's text, so a caller can branch on it.
_RETRIEVAL_FAILED: Final[str] = "retrieval_failed"

# The retriever's shape: given a query and a bounded ``top_k``, return the shared
# retrieval outcome. ``functools.partial`` of ``retrieval.search.retrieve`` with
# its ``embedder`` and ``store`` bound satisfies this. Structural rather than
# imported from anywhere, so binding the backend stays the deployment's job.
type Retriever = Callable[[str, int], Awaitable[RetrievalOutcome]]

# The default ``top_k``. It matches ``KnowledgeSearchArgs``'s default (5) and its
# 1..50 bound is enforced again here, so the argument schema is not the only
# place the bound lives.
_DEFAULT_TOP_K: Final[int] = 5
_MAX_TOP_K: Final[int] = 50


class KnowledgeTool:
    """Dispatches ``knowledge.search`` in-process, without an MCP server.

    Args:
        retriever: The shared search -- a ``functools.partial`` of
            ``opspilot.adapters.retrieval.search.retrieve`` with its ``embedder``
            and ``store`` bound, taking ``(query, top_k)`` and returning a
            ``RetrievalOutcome``. When ``None`` the tool still exists and is
            callable and every call returns a ``not_configured`` refusal: it does
            not raise, and it does not pretend an empty index found nothing.

    It implements the ``ToolGateway`` port, so it is a drop-in for the run
    runtime's ``gateway`` argument; ``list_tools`` returns the static registry
    for the same reason ``MCPToolGateway``'s does.
    """

    def __init__(self, *, retriever: Retriever | None = None) -> None:
        self._retriever = retriever

    async def list_tools(self) -> list[object]:
        """Return the static registry, not a server-derived list.

        The registry is the source of truth for permissions; this deliberately
        does not build a second registry. Typed as ``list[object]`` because this
        half only ever dispatches ``knowledge.search`` and has no opinion about
        the other tools -- a caller wanting the full registry reads
        ``TOOL_REGISTRY``.
        """
        from opspilot.domain.tools import TOOL_REGISTRY

        return list(TOOL_REGISTRY.values())

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Dispatch ``knowledge.search`` in-process and return a ``ToolResult``.

        Only ever reached after the five gates pass. A name that is not
        ``knowledge.search`` is refused as ``unknown_tool`` rather than raising:
        this tool answers for exactly one registered name.
        """
        if name != _TOOL_NAME:
            return ToolResult(tool_name=name, ok=False, error="unknown_tool")

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                tool_name=name,
                ok=False,
                error="validation_error",
                result={"code": "validation_error", "message": "query is required"},
            )

        raw_top_k = arguments.get("top_k", _DEFAULT_TOP_K)
        top_k = raw_top_k if isinstance(raw_top_k, int) else _DEFAULT_TOP_K
        bounded = max(1, min(top_k, _MAX_TOP_K))

        if self._retriever is None:
            return ToolResult(
                tool_name=name,
                ok=False,
                error=_NOT_CONFIGURED,
                result={"code": _NOT_CONFIGURED},
            )

        try:
            outcome = await self._retriever(query, bounded)
        except Exception:
            # Errors are results, never exceptions. The backend's failure is a
            # stable token the caller can branch on, not the exception's text.
            return ToolResult(
                tool_name=name,
                ok=False,
                error=_RETRIEVAL_FAILED,
                result={"code": _RETRIEVAL_FAILED},
            )

        return ToolResult(
            tool_name=name,
            ok=True,
            result={
                "code": None,
                "hits": [
                    {
                        "document_slug": hit.document_slug,
                        "anchor": hit.anchor,
                        "score": hit.score,
                        "rank": hit.rank,
                    }
                    for hit in outcome.hits
                ],
                "count": len(outcome.hits),
                # Abstention is a *success*, not a refusal: the index answered,
                # and the answer was "nothing scores above the threshold"
                # (``docs/architecture.md`` §9).
                "abstained": outcome.abstained,
            },
        )
