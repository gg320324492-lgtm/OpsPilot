"""Run routes.

Responsibility: read a run and its trace -- status, steps, tool calls, and the
citations that support its answer. Read-only in Phase 1: runs are created
through the ticket endpoint and advanced by the worker, never driven by an HTTP
request (which is what keeps the agent loop out of the request path).

Layer: ``api`` (router).

A run parked in ``WAITING_APPROVAL`` is a normal, non-terminal state to return,
not an error.
"""

from __future__ import annotations

from uuid import UUID

from opspilot.api.schemas import RunSummary, ToolCallSummary


async def get_run(run_id: UUID) -> RunSummary:
    """Fetch a run's summary. M0 stub."""
    raise NotImplementedError


async def list_run_steps(run_id: UUID) -> list[dict[str, object]]:
    """Return the ordered step trace for a run. M0 stub."""
    raise NotImplementedError


async def list_run_tool_calls(run_id: UUID) -> list[ToolCallSummary]:
    """Return the tool calls made by a run. M0 stub."""
    raise NotImplementedError
