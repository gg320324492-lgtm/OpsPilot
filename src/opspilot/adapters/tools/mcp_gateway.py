"""``MCPToolGateway``: the ``ToolGateway`` port over MCP servers.

Responsibility: launch and talk to the three deterministic demo MCP servers
(``crm-mcp``, ``billing-mcp``, ``issues-mcp``) and expose their tools through
the ``ToolGateway`` Protocol. In Docker the servers are siblings on the network;
locally they are subprocesses (``MCP_CRM_COMMAND`` etc.).

Layer: ``adapters``. Implements ``opspilot.ports.tool_gateway.ToolGateway``.

This module is the single named relaxation of mypy's ``disallow_untyped_decorators``
(see ``pyproject.toml``) because the MCP SDK's type stubs are incomplete. The
MCP transport is invisible above this file.

Execution note: ``call_tool`` is only reached *after* the five gates pass.
``billing.issue_refund`` mutates its own store and is idempotent on
``idempotency_key`` -- the same key twice returns the same ``refund_id`` and
creates no second refund.
"""

from __future__ import annotations

from typing import Any

from opspilot.domain.tools import ToolSpec
from opspilot.ports.tool_gateway import ToolResult


class MCPToolGateway:
    """Executes tools by calling the MCP servers."""

    def __init__(self, *, server_commands: dict[str, str]) -> None:
        self._server_commands = server_commands

    async def list_tools(self) -> list[ToolSpec]:
        """List tools exposed by the MCP servers. M0 stub."""
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Dispatch a tool call to the owning MCP server. M0 stub."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Shut down the server subprocesses/sessions. M0 stub."""
        raise NotImplementedError
