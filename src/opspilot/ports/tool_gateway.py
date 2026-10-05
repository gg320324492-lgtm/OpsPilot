"""The ``ToolGateway`` port.

Responsibility: the boundary through which tools are *listed* and *called*. The
MCP transport is never visible to ``domain/`` or ``agents/`` -- both see only
this Protocol. Because it is a port, a ``RESTToolGateway`` in Phase 2 needs no
domain change.

Layer: ``ports``. Imports only ``typing`` and ``opspilot.domain``.

Critical constraint: ``call_tool`` is never reached directly by the agent
runtime. Every call passes through ``agents/runtime._gate_and_execute``, which
runs the five gates first. The gateway is the thing that executes, not the thing
that decides. See ``docs/tool-permissions.md`` §3.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from opspilot.domain.tools import ToolSpec


class ToolResult(BaseModel):
    """The outcome of executing a tool call through the gateway."""

    tool_name: str
    ok: bool
    result: dict[str, object] | None = None
    error: str | None = None
    latency_ms: int = 0


@runtime_checkable
class ToolGateway(Protocol):
    """Lists registered tools and executes tool calls."""

    async def list_tools(self) -> list[ToolSpec]:
        """List the tools this gateway can execute.

        Used to cross-check the static registry against what the servers
        actually expose; the static registry, not this list, is authoritative
        for permissions.
        """
        ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Execute a tool call.

        Only ever called by ``_gate_and_execute`` *after* the five gates have
        passed. Write tools are idempotent on ``idempotency_key`` so a replayed
        call does not duplicate a side effect.
        """
        ...
