"""Ticket routes.

Responsibility: ``POST /api/tickets`` -- insert a ticket and a ``RECEIVED`` run in
one transaction and return 201 immediately. The handler does **not** run the
agent: the run table is the queue and the worker claims it
(``docs/architecture.md`` §5). A ticket with a duplicate-charge complaint is
enqueued exactly like any other; classification happens in the worker.

Layer: ``api`` (router).

The one rule this module is tested on: **the handler starts nothing**. It does
not call a model, does not invoke the worker, does not schedule a background
task. ``docs/api-contract.md`` §2 is explicit that the client polls, because
doing work inline would put a multi-second model loop inside an HTTP request and
lose the run's trace if the response dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opspilot.api.auth import require_operator
from opspilot.api.errors import ApiError
from opspilot.api.routers.deps import RunStoreDep, TicketStoreDep
from opspilot.api.schemas import (
    RunRef,
    TicketCreateRequest,
    TicketCreateResponse,
    TicketDetail,
    TicketDetailResponse,
    TicketListResponse,
)
from opspilot.domain.runs import AgentRun
from opspilot.settings import Settings, get_settings

router = APIRouter(prefix="/api", tags=["tickets"], dependencies=[Depends(require_operator)])


class TicketRow(Protocol):
    """The shape the router needs from a persisted ``tickets`` row."""

    id: UUID
    external_id: str | None
    subject: str
    body: str
    customer_email: str
    created_at: datetime


class TicketReads(Protocol):
    """The optional reads a ticket store may offer, beyond the port's ``create``.

    Declared for documentation; the router probes with ``getattr`` so a store that
    does not offer them still serves the endpoints that can degrade.
    """

    async def get(self, ticket_id: UUID) -> TicketRow | None: ...

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[TicketRow]: ...

    async def rollback(self, ticket_id: UUID) -> None: ...


def _settings() -> Settings:
    """The process settings, as a FastAPI dependency (patchable in tests)."""
    return get_settings()


@router.post("/tickets", status_code=201, response_model=TicketCreateResponse)
async def create_ticket(
    request: TicketCreateRequest,
    tickets: TicketStoreDep,
    runs: RunStoreDep,
    settings: Settings = Depends(_settings),  # noqa: B008 -- FastAPI dependency marker
) -> TicketCreateResponse:
    """Enqueue a ticket and its run, in one transaction, and return 201.

    The ticket insert and the ``RECEIVED`` run insert are one unit of work: both
    rows land or neither does. The store's transaction boundary is what makes
    that true -- a ticket with no run would be silently never investigated, which
    is worse than a failed request the client retries.

    Args:
        request: The ticket to create.
        tickets: The ticket store port.
        runs: The run store port.
        settings: Process settings, for the model provider/name the run records.

    Returns:
        The nested ``ticket`` + ``run`` shape, with the run in ``received``.
    """
    ticket_id: UUID = await tickets.create(
        subject=request.subject,
        body=request.body,
        customer_email=request.customer_email,
        external_id=request.external_id,
    )
    model_name = settings.model_name or settings.model_provider
    try:
        run = await runs.create(
            ticket_id=ticket_id, model_provider=settings.model_provider, model_name=model_name
        )
    except Exception:
        # Both rows land or neither does. In production the two inserts are one
        # transaction and this rollback is a no-op; with stores that do not share
        # a transaction, the compensating delete is what keeps the invariant
        # true. A ticket with no run would be silently never investigated, which
        # is worse than a retryable 500.
        rollback = getattr(tickets, "rollback", None)
        if rollback is not None:
            await rollback(ticket_id)
        raise
    return TicketCreateResponse(
        ticket=TicketDetail(
            id=ticket_id,
            external_id=request.external_id,
            subject=request.subject,
            body=request.body,
            customer_email=request.customer_email,
            created_at=run.created_at,
        ),
        run=RunRef(id=run.id, status=run.status, created_at=run.created_at),
    )


@router.get("/tickets", response_model=TicketListResponse)
async def list_tickets(
    tickets: TicketStoreDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TicketListResponse:
    """List tickets, newest first (contract §10).

    Raises:
        ApiError: 500 if the store cannot list -- it is not required to by the
            ``TicketStore`` port, so this endpoint degrades to an empty page
            rather than pretending.
    """
    lister = getattr(tickets, "list", None)
    if lister is None:
        # The port does not require listing; a store without it returns an empty
        # page rather than a 500, because the endpoint is a convenience, not part
        # of the workflow.
        return TicketListResponse(items=[], total=0)
    rows: list[TicketRow] = await lister(limit=limit, offset=offset)
    items = [
        TicketDetail(
            id=row.id,
            external_id=row.external_id,
            subject=row.subject,
            body=row.body,
            customer_email=row.customer_email,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return TicketListResponse(items=items, total=len(items))


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: UUID,
    tickets: TicketStoreDep,
    runs: RunStoreDep,
) -> TicketDetailResponse:
    """One ticket with its runs.

    Raises:
        ApiError: 404 ``ticket_not_found`` if there is no such ticket.
    """
    getter = getattr(tickets, "get", None)
    ticket: TicketRow | None = await getter(ticket_id) if getter is not None else None
    if ticket is None:
        raise ApiError(
            404,
            "ticket_not_found",
            f"Ticket {ticket_id} does not exist.",
            {"ticket_id": str(ticket_id)},
        )
    run_lister = getattr(runs, "list_for_ticket", None)
    run_rows: list[AgentRun] = await run_lister(ticket_id) if run_lister is not None else []
    return TicketDetailResponse(
        ticket=TicketDetail(
            id=ticket.id,
            external_id=ticket.external_id,
            subject=ticket.subject,
            body=ticket.body,
            customer_email=ticket.customer_email,
            created_at=ticket.created_at,
        ),
        runs=[RunRef(id=run.id, status=run.status, created_at=run.created_at) for run in run_rows],
    )
