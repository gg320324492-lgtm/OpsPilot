"""Ports layer: the Protocols that invert dependencies on external systems.

Responsibility: define the boundaries -- ``ModelProvider``, ``ToolGateway``,
``RunStore``/``ToolCallStore``/``ApprovalStore``/``TicketStore``, ``VectorStore``
and ``Orchestrator`` -- that ``agents/`` and ``domain/`` depend on, and that
``adapters/`` implement.

Layer: ``ports``. Imports only ``typing`` and ``opspilot.domain`` -- never an
adapter, never a concrete SDK. This is the dependency-inversion seam described
in ``docs/architecture.md`` §3-4: because the runtime depends on these Protocols
and not on LangGraph, Postgres or MCP, replacing any of those changes only the
matching adapter.
"""

from __future__ import annotations
