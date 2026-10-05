"""FastAPI dependencies that supply the store ports to the routers.

Responsibility: the seam between the routers and the persistence adapters. A
router declares ``RunStoreDep`` and receives whatever the app factory (or a test)
has bound; it never imports ``opspilot.adapters.persistence``. That is what lets
the persistence layer be implemented in parallel -- and lets the tests inject an
in-memory fake -- without changing a single route.

Layer: ``api`` (router package).

The providers raise on a missing override rather than returning ``None``: a route
that runs with no store bound is a wiring bug, and a ``None`` would surface as an
``AttributeError`` inside a request rather than at start.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from opspilot.ports.stores import ApprovalStore, RunStore, TicketStore


class StoreNotBound(RuntimeError):
    """A store dependency was resolved with nothing bound on ``app.state``.

    A wiring bug, not a client error: it means the app was built without calling
    ``create_app``'s store injection. The message names the attribute so the
    missing piece is obvious.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"no {name!r} bound on app.state; the app factory must set it "
            "(see opspilot.api.app.create_app)"
        )


def _require(request: Request, name: str) -> object:
    """Read a store off ``app.state`` by name, or raise naming the missing piece.

    Args:
        request: The incoming request, whose ``app.state`` holds the stores.
        name: The attribute on ``app.state``.

    Returns:
        The bound store.

    Raises:
        StoreNotBound: If nothing is bound.
    """
    store = getattr(request.app.state, name, None)
    if store is None:
        raise StoreNotBound(name)
    return store


async def get_run_store(request: Request) -> RunStore:
    """The injected ``RunStore`` port."""
    return _require(request, "run_store")  # type: ignore[return-value]


async def get_ticket_store(request: Request) -> TicketStore:
    """The injected ``TicketStore`` port."""
    return _require(request, "ticket_store")  # type: ignore[return-value]


async def get_approval_store(request: Request) -> ApprovalStore:
    """The injected ``ApprovalStore`` port."""
    return _require(request, "approval_store")  # type: ignore[return-value]


RunStoreDep = Annotated[RunStore, Depends(get_run_store)]
TicketStoreDep = Annotated[TicketStore, Depends(get_ticket_store)]
ApprovalStoreDep = Annotated[ApprovalStore, Depends(get_approval_store)]
