"""``StubGateway``: an in-memory ``ToolGateway`` for tests.

Responsibility: let unit tests exercise the runtime and the gates without
launching an MCP server. Responses are registered per tool name up front, so a
test asserts on gate behaviour rather than on a subprocess.

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``.

This is a *test* adapter, not a production fallback: the golden workflow's
integration tests run against the real MCP servers.

**Its ``call_tool`` records every call**, which is the whole point of it: a
test that asserts a proposal was refused *before dispatch* asserts on
``calls`` being empty. A stub that did not record would make that assertion
vacuous -- the test would pass because nothing had been dispatched, and
nothing had been dispatched because the stub raised.
"""

from __future__ import annotations

from typing import Any

from opspilot.domain.tools import ToolSpec
from opspilot.ports.tool_gateway import ToolResult


class StubGateway:
    """A gateway backed by a dict of canned responses."""

    def __init__(
        self,
        tools: list[ToolSpec] | None = None,
        results: dict[str, ToolResult] | None = None,
    ) -> None:
        """Register the tools this stub offers, and the answers it gives.

        Args:
            tools: The tools ``list_tools`` reports.
            results: The canned answer per tool name. A tool with no entry
                gets a successful empty result, so a test that only cares
                about *which* tools were dispatched does not have to say so.
        """
        self._tools = tools or []
        self._results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[ToolSpec]:
        """Return the tools registered on this stub."""
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Record the call and return its canned result.

        The call is recorded **before** the answer is produced, so a test that
        asserts on ``calls`` sees the dispatch even when the canned result is
        a refusal.
        """
        self.calls.append((name, dict(arguments)))
        result = self._results.get(name)
        if result is not None:
            return result
        return ToolResult(tool_name=name, ok=True, result={})
