"""Adapters layer: concrete implementations of the ports.

Responsibility: everything that talks to the outside world -- model SDKs, the
MCP transport, SQLAlchemy/Postgres, pgvector, and the LangGraph executor. Each
adapter implements exactly one port from ``opspilot.ports``.

Layer: ``adapters``. This is the only layer permitted to import third-party
SDKs (``openai``, ``anthropic``, ``mcp``, ``langgraph``, ``sqlalchemy``,
``fastapi``). Dependencies point *up* into adapters from ``domain``/``ports``,
never the reverse. See ``docs/architecture.md`` §3.
"""

from __future__ import annotations
