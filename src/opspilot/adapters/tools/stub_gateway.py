"""``StubGateway``: an in-memory ``ToolGateway`` for tests.

Responsibility: let unit tests exercise the runtime and the gates without
launching an MCP server. Responses are registered per tool name up front, so a
test asserts on gate behaviour rather than on a subprocess.

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``.

This is a *test* adapter, not a production fallback: the golden workflow's
integration tests run against the real MCP servers. Its ``call_tool`` records
every call so a test can assert that a rejected proposal was never dispatched.
"""

from __future__ import annotations

from typing import Any

from opspilot.domain.tools import ToolSpec
from opspilot.ports.tool_gateway import ToolResult


class StubGateway:
    """A gateway backed by a dict of canned responses."""

    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools = tools or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[ToolSpec]:
        """Return the tools registered on this stub."""
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Record the call and return its canned result. M0 stub."""
        raise NotImplementedError
