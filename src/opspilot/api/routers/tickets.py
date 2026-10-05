"""Ticket routes.

Responsibility: ``POST /api/tickets`` -- insert a ticket and a ``RECEIVED`` run in
one transaction and return 201 immediately. The handler does **not** run the
agent: the run table is the queue and the worker claims it
(``docs/architecture.md`` §5). A ticket with a duplicate-charge complaint is
enqueued exactly like any other; classification happens in the worker.

Layer: ``api`` (router).
"""

from __future__ import annotations

from opspilot.api.schemas import TicketCreateRequest, TicketCreateResponse


async def create_ticket(request: TicketCreateRequest) -> TicketCreateResponse:
    """Enqueue a ticket and its run. M0 stub."""
    raise NotImplementedError
