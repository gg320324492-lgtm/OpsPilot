"""Fixtures for the adversarial security suite.

Every test in ``tests/security/`` drives the *real* gate (``agents.runtime``),
the real policy engine (``domain.policies``) and the real SQLite repositories --
there is nothing faked except the model and the tool *transport*, because the
controls under test are exactly the code those real components run.

The database is a single in-memory SQLite ``sessionmaker`` shared by every store
and by the ``TraceRecorder``. Sharing one factory matters: a second factory over
``:memory:`` would open a *different* empty database, the approval row written at
gate 5 would be invisible to ``has_approved``, and a test that is supposed to
prove "no approval -> parked" would pass for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.agents.state import RunContext
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolSpec
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema.

    ``Settings`` is constructed with the in-memory URL so the boundary is
    explicit and no ambient environment can redirect it to a real database.
    """
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


class SpyGateway:
    """A ``ToolGateway`` that records every dispatch and never performs I/O.

    The spy is the instrument the attacks are judged on: a control that refuses a
    proposal must leave ``dispatched`` empty, so "never dispatched" is a positive
    assertion rather than an inference from the absence of an error.

    It returns a canned success by default, so a test that *wants* execution to
    happen (the idempotency attack, which needs one real-ish execution) can assert
    the count of dispatches. A per-name ``responses`` map lets a test shape the
    return.
    """

    def __init__(self, responses: dict[str, ToolResult] | None = None) -> None:
        self.dispatched: list[tuple[str, dict[str, object]]] = []
        self._responses = responses or {}

    async def list_tools(self) -> list[ToolSpec]:
        """Return no tools: the registry, not the gateway, is authoritative."""
        return []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Record the dispatch and return the canned result for ``name``."""
        self.dispatched.append((name, dict(arguments)))
        if name in self._responses:
            return self._responses[name]
        return ToolResult(
            tool_name=name, ok=True, result={"refund_id": "REF-10091", "replayed": False}
        )

    @property
    def dispatch_count(self) -> int:
        """How many dispatch attempts were made."""
        return len(self.dispatched)


@pytest.fixture
def spy() -> SpyGateway:
    """A recording gateway that never touches an MCP server."""
    return SpyGateway()


class Stores:
    """The four repository stores plus the run they operate on.

    Bundled so a test constructs one object and gets a coherent database. All
    four share the one ``sessionmaker`` fixture, so the run, its tool calls and
    its approvals are visible to each other and to ``TraceRecorder``.
    """

    def __init__(self, factory: sessionmaker[Session], run: AgentRun) -> None:
        """Bind the stores to ``factory`` and record the run under test."""
        self.factory = factory
        self.run = run
        self.run_store = SqlRunStore(factory)
        self.tool_call_store = SqlToolCallStore(factory)
        self.approval_store = SqlApprovalStore(factory)


async def make_run(stores_factory: sessionmaker[Session]) -> Stores:
    """Create a ticket and a run in ``EXECUTING``, and bundle the stores.

    The run is moved to ``EXECUTING`` because that is the state a tool call is
    gated *from*: ``PLANNING -> EXECUTING`` then the gate drives it to
    ``WAITING_APPROVAL`` on a high-risk proposal
    (``docs/agent-state-machine.md`` §2).
    """
    ticket_store = SqlTicketStore(stores_factory)
    ticket_id = await ticket_store.create(
        subject="Duplicate charge",
        body="Charged twice for October.",
        customer_email="b@acme.example",
    )
    run_store = SqlRunStore(stores_factory)
    run = await run_store.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    await run_store.set_status(run.id, RunStatus.EXECUTING)
    fetched = await run_store.get(run.id)
    assert fetched is not None
    return Stores(stores_factory, fetched)


def make_context(stores: Stores) -> RunContext:
    """Build a ``RunContext`` around ``stores.run``."""
    return RunContext(
        run=stores.run,
        ticket_subject="Duplicate charge",
        ticket_body="Charged twice for October.",
        customer_email="b@acme.example",
        ticket_id=stores.run.ticket_id,
    )


__all__ = [
    "SpyGateway",
    "Stores",
    "make_context",
    "make_run",
]
