"""Tool gateway adapters: MCP over stdio, and a stub for tests.

Responsibility: concrete ``ToolGateway`` implementations. ``MCPToolGateway`` is
the Phase 1 production path -- it launches the three demo MCP servers
(``crm``, ``billing``, ``issues``) and calls their tools. ``StubGateway`` is an
in-memory gateway for unit tests that never spawn a process.

Layer: ``adapters``. The MCP transport lives *only* here; ``domain/`` and
``agents/`` see the port and never the protocol. See ``docs/architecture.md`` §8.
"""

from __future__ import annotations
