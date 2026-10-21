"""Tool gateway adapters: MCP over stdio, and a stub for tests.

Responsibility: concrete ``ToolGateway`` implementations. The **production
path is a composite**: ``CompositeToolGateway`` routes on the registry's
``server`` field, so eight tools go to ``MCPToolGateway`` -- which reaches the
three demo MCP servers (``crm``, ``billing``, ``issues``) -- and the ninth,
``knowledge.search``, is served in-process by ``KnowledgeTool``. Which
transport the MCP half uses is ``MCP_TRANSPORT``, and it defaults to
``inprocess``: the servers are built inside the worker, with no process
boundary, and ``stdio`` is the opt-in that puts the pipe itself under test.
``StubGateway`` is an in-memory gateway for unit tests that never spawn a
process.

Layer: ``adapters``. The MCP transport lives *only* here; ``domain/`` and
``agents/`` see the port and never the protocol. See ``docs/architecture.md`` §8.
"""

from __future__ import annotations
