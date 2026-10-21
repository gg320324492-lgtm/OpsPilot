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
from opspilot.api.routers.deps import RunStoreDep, TicketIntakeDep, TicketStoreDep
from opspilot.api.schemas import (
    RunRef,
    TicketCreateRequest,
    TicketCreateResponse,
    TicketDetail,
    TicketDetailResponse,
    TicketListResponse,
)
from opspilot.domain.errors import TicketExists
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

    **``rollback`` used to be declared here and is gone.** It described a
    compensating delete the route used to attempt between two writes, and the
    SQL store does not answer it -- so the compensation was dead code and the
    orphan ticket was real. The route's two writes are now one transaction
    (the ``TicketIntake`` port), and the only thing that still has a
    ``rollback`` is the *test* double, which needs it to model the atomicity
    the real store provides for free.
    """

    async def get(self, ticket_id: UUID) -> TicketRow | None: ...

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[TicketRow]: ...


def _settings() -> Settings:
    """The process settings, as a FastAPI dependency (patchable in tests)."""
    return get_settings()


async def _read_ticket(tickets: object, ticket_id: UUID) -> TicketRow | None:
    """Read back the ticket a duplicate ``external_id`` already belongs to.

    Probed with ``getattr`` for the same reason every other optional read in
    this module is: a store that cannot answer is not a reason to answer the
    client with a 500, and ``GET /api/tickets/{id}`` already degrades the same
    way. The 409 still stands without it -- only the copy of the ticket is
    thinner.
    """
    getter = getattr(tickets, "get", None)
    return await getter(ticket_id) if getter is not None else None


def _ticket_exists(exc: TicketExists, existing: TicketRow | None) -> ApiError:
    """Build the contract's 409 ``ticket_exists``, carrying the existing ticket.

    ``docs/api-contract.md`` §9 promises the duplicate comes back *with the ticket
    that holds the ``external_id``*, so it is carried in ``details.ticket`` and
    serialized through ``TicketDetail`` -- the same model §2 defines the created
    ticket with, so a client that parses a ``ticket`` object already knows the
    shape and cannot be handed a second, subtly different one.

    Nothing else is added to the envelope: ``code``, ``message`` and ``details``
    are the three fields §6 defines, and the status is 409 because that is the
    status ``errors.py`` registers for this code.
    """
    details: dict[str, object] = {"external_id": exc.external_id, "ticket_id": str(exc.ticket_id)}
    if existing is not None:
        details["ticket"] = TicketDetail(
            id=existing.id,
            external_id=existing.external_id,
            subject=existing.subject,
            body=existing.body,
            customer_email=existing.customer_email,
            created_at=existing.created_at,
        ).model_dump(mode="json")
    return ApiError(
        409,
        "ticket_exists",
        f"A ticket with external_id {exc.external_id!r} already exists.",
        details,
    )


@router.post("/tickets", status_code=201, response_model=TicketCreateResponse)
async def create_ticket(
    request: TicketCreateRequest,
    intake: TicketIntakeDep,
    tickets: TicketStoreDep,
    settings: Settings = Depends(_settings),  # noqa: B008 -- FastAPI dependency marker
) -> TicketCreateResponse:
    """Enqueue a ticket and its run, in one transaction, and return 201.

    The ticket insert and the ``RECEIVED`` run insert are one unit of work: both
    rows land or neither does. The store's transaction boundary is what makes
    that true -- a ticket with no run would be silently never investigated, which
    is worse than a failed request the client retries.

    It used to be two writes with a compensating delete between them, and the
    compensation was a ``getattr(tickets, "rollback", None)`` that the SQL store
    does not answer -- so the two writes were two transactions and the
    compensation was dead code. The rollback existed on paper and the orphan
    ticket existed in the database.

    Args:
        request: The ticket to create.
        intake: The ``TicketIntake`` port: the ticket and its run, as one unit
            of work.
        tickets: The ticket store port, for reading the duplicate back.
        settings: Process settings, for the model provider/name the run records.

    Returns:
        The nested ``ticket`` + ``run`` shape, with the run in ``received``.

    Raises:
        ApiError: 409 ``ticket_exists`` if the ``external_id`` is already taken.
    """
    model_name = settings.model_name or settings.model_provider
    try:
        ticket_id, run = await intake.create(
            subject=request.subject,
            body=request.body,
            customer_email=request.customer_email,
            external_id=request.external_id,
            model_provider=settings.model_provider,
            model_name=model_name,
        )
    except TicketExists as exc:
        # The duplicate is detected by the unique index on ``tickets.external_id``,
        # inside the store -- not by a read this handler could have skipped and got
        # wrong under concurrency -- and reaches here as a named domain condition
        # rather than as the driver's ``IntegrityError``. The router is what turns
        # it into the contract's ``409``: this module already raises ``ApiError``
        # for every other failure it owns, so a second mechanism (an exception
        # handler in ``errors.py``) would give the same code two shapes.
        raise _ticket_exists(exc, await _read_ticket(tickets, exc.ticket_id)) from exc
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

    **It degrades rather than raising.** The ``TicketStore`` port does not
    require a ``list``, so a store that does not offer one is served an empty
    page instead of a 500: this endpoint is a convenience, not part of the
    workflow, and a store that cannot list has no tickets to be missing.
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
    # ``total`` counts every ticket, not the ones on this page. It used to be
    # ``len(items)``, which made a paginated list report a total that did not
    # count the tickets it did not return -- and the dashboard read that number
    # as "how many tickets there are".
    #
    # The count is a *required* port method, so there is no fallback to the
    # page length: a store that cannot count is a store that does not satisfy
    # the contract this endpoint publishes (contract §10).
    total = await tickets.count()
    return TicketListResponse(items=items, total=total)


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
