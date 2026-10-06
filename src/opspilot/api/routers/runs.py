"""Run routes.

Responsibility: read a run and its trace -- status, steps, tool calls, and the
citations that support its answer. Read-only in Phase 1: runs are created
through the ticket endpoint and advanced by the worker, never driven by an HTTP
request (which is what keeps the agent loop out of the request path).

Layer: ``api`` (router).

A run parked in ``WAITING_APPROVAL`` is a normal, non-terminal state to return,
not an error.

Reads that the ``RunStore`` port does not name -- listing runs, the citation
join, the steps of a run -- are reached through optional methods when the
concrete store offers them, so this router stays bound to the port while still
serving the dashboard's payload. Where a store does not offer one, the field is
an empty list rather than a 500: a missing citations panel is a smaller failure
than a run-detail screen that will not render.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opspilot.api.auth import require_operator
from opspilot.api.errors import ApiError
from opspilot.api.routers.deps import RunStoreDep, TicketStoreDep
from opspilot.api.schemas import (
    CitationDetail,
    CustomerReply,
    PendingApproval,
    RunCreateRequest,
    RunDetail,
    RunListResponse,
    RunRef,
    RunSummary,
    StepDetail,
    ToolCallDetail,
    TraceResponse,
    TraceStep,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.settings import Settings, get_settings

router = APIRouter(prefix="/api", tags=["runs"], dependencies=[Depends(require_operator)])


class StepRow(Protocol):
    """The shape the router needs from a persisted ``agent_steps`` row.

    A local Protocol rather than a concrete model: it names only the fields the
    trace renders, so the persistence adapter (written in parallel) can satisfy it
    with any object carrying these attributes.
    """

    sequence: int
    step_type: str
    output: dict[str, object] | None
    latency_ms: int | None
    started_at: datetime


class ToolCallRow(Protocol):
    """The shape the router needs from a persisted ``tool_calls`` row."""

    id: UUID
    tool_name: str
    arguments: dict[str, object]
    permission: Permission
    status: ToolCallStatus
    result: dict[str, object] | None
    latency_ms: int | None
    idempotency_key: str | None
    error: str | None


class CitationRow(Protocol):
    """The shape the router needs from a persisted ``citations`` row."""

    document: str
    chunk: str
    score: float
    rank: int


def _settings() -> Settings:
    """The process settings, as a FastAPI dependency (patchable in tests)."""
    return get_settings()


def _optional_method(store: object, name: str) -> Any:  # noqa: ANN401 -- duck-typed port extension
    """Fetch an optional method off a store, or ``None`` if it is not offered.

    The store ports name the methods the *worker* needs. The dashboard needs a
    few more (a list, a join). Rather than widen the port -- which the persistence
    agent owns -- this router probes for them, so an in-memory fake in a test and
    the SQL adapter in production can each provide exactly what they have.
    """
    return getattr(store, name, None)


def _run_summary(run: AgentRun) -> RunSummary:
    """Project a domain ``AgentRun`` to its list/detail summary."""
    return RunSummary(
        id=run.id,
        ticket_id=run.ticket_id,
        status=run.status,
        model_provider=run.model_provider,
        model_name=run.model_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
    )


@router.post("/runs", status_code=201, response_model=RunRef)
async def create_run(
    request: RunCreateRequest,
    runs: RunStoreDep,
    tickets: TicketStoreDep,
    settings: Settings = Depends(_settings),  # noqa: B008 -- FastAPI dependency marker
) -> RunRef:
    """Enqueue a new run for an existing ticket (contract §9).

    Re-running an investigation is a legitimate action, so this always creates a
    new run rather than deduplicating.

    Raises:
        ApiError: 404 ``ticket_not_found`` if the ticket does not exist.
    """
    getter = _optional_method(tickets, "get")
    ticket = await getter(request.ticket_id) if getter is not None else None
    if ticket is None:
        raise ApiError(
            404,
            "ticket_not_found",
            f"Ticket {request.ticket_id} does not exist.",
            {"ticket_id": str(request.ticket_id)},
        )
    model_name = settings.model_name or settings.model_provider
    run = await runs.create(
        ticket_id=request.ticket_id,
        model_provider=settings.model_provider,
        model_name=model_name,
    )
    return RunRef(id=run.id, status=run.status, created_at=run.created_at)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    runs: RunStoreDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    """List runs, optionally filtered by status (contract §1, §10).

    Raises:
        ApiError: 400 ``validation_error`` if ``status`` is not a ``RunStatus``.
    """
    parsed: RunStatus | None = None
    if status is not None:
        try:
            parsed = RunStatus(status)
        except ValueError as exc:
            raise ApiError(
                400,
                "validation_error",
                f"{status!r} is not a valid run status.",
                {"status": status, "allowed": [s.value for s in RunStatus]},
            ) from exc
    lister = _optional_method(runs, "list")
    if lister is None:
        return RunListResponse(items=[], total=0)
    rows = await lister(status=parsed, limit=limit, offset=offset)
    return RunListResponse(items=[_run_summary(run) for run in rows], total=len(rows))


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: UUID, runs: RunStoreDep) -> RunDetail:
    """The run-detail payload the dashboard renders (contract §3).

    ``pending_approval`` is populated only when the run is parked in
    ``WAITING_APPROVAL``; ``customer_reply`` only from a completed run's response
    step. Both are fetched through optional store methods so a store that does
    not offer them still serves the run.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)

    steps = await _load_steps(runs, run_id)
    tool_calls = await _load_tool_calls(runs, run_id)
    citations = await _load_citations(runs, run_id)
    pending = await _load_pending_approval(runs, run)
    reply = await _load_customer_reply(runs, run)

    return RunDetail(
        id=run.id,
        ticket_id=run.ticket_id,
        status=run.status,
        model_provider=run.model_provider,
        model_name=run.model_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        steps=steps,
        tool_calls=tool_calls,
        citations=citations,
        pending_approval=pending,
        customer_reply=reply,
    )


@router.get("/runs/{run_id}/trace", response_model=TraceResponse)
async def get_run_trace(run_id: UUID, runs: RunStoreDep) -> TraceResponse:
    """The ordered timeline only, without tool arguments or model payloads.

    Each step is projected to a human-readable ``label`` and a short ``detail``
    server-side, so the client renders without deciding what a step means.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)
    loader = _optional_method(runs, "list_steps")
    rows = await loader(run_id) if loader is not None else []
    timeline = [_to_trace_step(row) for row in rows]
    timeline.sort(key=lambda step: step.sequence)
    return TraceResponse(run_id=run_id, steps=timeline)


# -- loaders -----------------------------------------------------------------


async def _load_steps(store: object, run_id: UUID) -> list[StepDetail]:
    """Load a run's steps as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_steps")
    if loader is None:
        return []
    rows: list[StepRow] = await loader(run_id)
    return [
        StepDetail(
            sequence=row.sequence,
            step_type=row.step_type,
            output=row.output,
            latency_ms=row.latency_ms,
            started_at=row.started_at,
        )
        for row in rows
    ]


async def _load_tool_calls(store: object, run_id: UUID) -> list[ToolCallDetail]:
    """Load a run's tool calls as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_tool_calls")
    if loader is None:
        return []
    rows: list[ToolCallRow] = await loader(run_id)
    return [
        ToolCallDetail(
            id=row.id,
            tool_name=row.tool_name,
            arguments=row.arguments,
            permission=row.permission,
            status=row.status,
            result=row.result,
            latency_ms=row.latency_ms,
            idempotency_key=row.idempotency_key,
            error=row.error,
        )
        for row in rows
    ]


async def _load_citations(store: object, run_id: UUID) -> list[CitationDetail]:
    """Load a run's citations as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_citations")
    if loader is None:
        return []
    rows: list[CitationRow] = await loader(run_id)
    return [
        CitationDetail(
            document=row.document,
            chunk=row.chunk,
            score=float(row.score),
            rank=int(row.rank),
        )
        for row in rows
    ]


async def _load_pending_approval(store: object, run: AgentRun) -> PendingApproval | None:
    """Load the nested pending-approval payload when, and only when, parked."""
    if run.status is not RunStatus.WAITING_APPROVAL:
        return None
    loader = _optional_method(store, "get_pending_approval")
    if loader is None:
        return None
    result: PendingApproval | None = await loader(run.id)
    return result


async def _load_customer_reply(store: object, run: AgentRun) -> CustomerReply | None:
    """Load the customer reply, which is set only at ``COMPLETED``."""
    if run.status is not RunStatus.COMPLETED:
        return None
    loader = _optional_method(store, "get_customer_reply")
    if loader is None:
        return None
    result: CustomerReply | None = await loader(run.id)
    return result


def _run_not_found(run_id: UUID) -> ApiError:
    """Build the contract's 404 ``run_not_found`` for a missing run."""
    return ApiError(
        404,
        "run_not_found",
        f"Run {run_id} does not exist.",
        {"run_id": str(run_id)},
    )


def _to_trace_step(row: StepRow) -> TraceStep:
    """Project a step row into the trace shape, assembling the label server-side."""
    step_type = row.step_type
    output = row.output
    return TraceStep(
        sequence=row.sequence,
        step_type=step_type,
        label=_label_for(step_type),
        detail=_detail_for(step_type, output),
        latency_ms=row.latency_ms,
        at=row.started_at,
    )


_LABELS: dict[str, str] = {
    "state_change": "State changed",
    "classification": "Ticket classified",
    "retrieval": "Policy retrieved",
    "planning": "Next action proposed",
    "response": "Reply composed",
}


def _label_for(step_type: str) -> str:
    """The human-readable label for a step type."""
    return _LABELS.get(step_type, step_type.replace("_", " ").capitalize() or "Step")


def _detail_for(step_type: str, output: object) -> str:
    """A short summary string for a step, derived from its output.

    Every key read here is asserted against a real recorded step by
    ``tests/integration/test_run_detail_against_the_real_store.py``. Both of the
    keys this used to read -- ``"to"`` for a state change, and a whole
    ``tool_call`` branch -- were wrong: every writer emits ``to_status``, and no
    code path in ``src/`` records a ``tool_call`` step at all. Dead branches are
    worse than missing ones, because a missing branch fails loudly when the
    event finally arrives and a dead one silently renders ``to None``.
    """
    if not isinstance(output, dict):
        return output if isinstance(output, str) else ""
    if step_type == "state_change":
        to = output.get("to_status")
        return f"to {to}" if to else ""
    if step_type == "classification":
        category = output.get("category", "")
        confidence = output.get("confidence")
        return f"{category} ({confidence})" if confidence is not None else str(category)
    if step_type == "planning":
        # The proposal the model made, which is the step a reader most wants to
        # see: it names the action before the trace records whether it worked.
        tool = str(output.get("tool_name", "") or "")
        if output.get("done"):
            return "no further action needed" if not tool else f"{tool} (done)"
        return tool
    if step_type == "retrieval":
        return f"{output.get('count', '')} chunks".strip()
    if step_type == "response":
        chars = output.get("chars")
        escalated = bool(output.get("escalated"))
        length = f"{chars} chars" if isinstance(chars, int) else ""
        if escalated:
            return "escalated reply, " + length if length else "escalated reply"
        return length
    return ""
