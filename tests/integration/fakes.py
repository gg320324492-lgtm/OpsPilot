"""In-memory fakes for the store ports, used by the API integration tests.

Responsibility: implement ``TicketStore``, ``RunStore`` and ``ApprovalStore`` with
dicts, so the API tests exercise the *routers* without the persistence layer --
which is a separate deliverable, written in parallel, and which would otherwise
make these tests untestable until it lands.

The fakes also carry the spies the milestone is judged on: ``tool_spy`` records
every tool execution attempted through the API, so a test can assert the approve
handler executed nothing, and ``worker_spy`` records any call into the worker.

Layer: ``tests``. Depends only on ``opspilot.domain`` and ``opspilot.ports``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus


class SimulatedRunInsertFailure(RuntimeError):
    """Raised by ``FakeRunStore.create`` when a test sets ``fail_create``.

    A named class rather than a bare ``RuntimeError("...")`` so the test that
    triggers it can assert on the type, and so the message is not a stray string.
    """

    def __init__(self) -> None:
        super().__init__("run insert failed (simulated)")


class Spy:
    """A call recorder. Any attribute access returns a recording callable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def record(self, name: str, *args: object, **kwargs: object) -> None:
        """Record one call under ``name``."""
        self.calls.append((name, args, kwargs))

    @property
    def call_count(self) -> int:
        """How many calls were recorded in total."""
        return len(self.calls)

    def __bool__(self) -> bool:
        return True


class TicketRow:
    """A ticket as the fake stores it."""

    def __init__(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None
    ) -> None:
        self.id: UUID = uuid4()
        self.subject = subject
        self.body = body
        self.customer_email = customer_email
        self.external_id = external_id
        self.created_at: datetime = datetime.now(UTC)


class ToolCallRow:
    """A tool call as the fake run store returns it for the detail payload."""

    def __init__(self, *, run_id: UUID, tool_name: str, arguments: dict[str, object]) -> None:
        self.id: UUID = uuid4()
        self.run_id = run_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.permission: Permission = Permission.READ
        self.status: ToolCallStatus = ToolCallStatus.PROPOSED
        self.result: dict[str, object] | None = None
        self.latency_ms: int | None = None
        self.idempotency_key: str | None = None
        self.error: str | None = None


class FakeTicketStore:
    """In-memory ``TicketStore``, plus the reads the ticket routes make."""

    def __init__(self, *, fail_after_create: bool = False) -> None:
        self.rows: dict[UUID, TicketRow] = {}
        # When true, the *next* call after ``create`` (i.e. the run insert in the
        # ticket handler) is made to fail, so a test can assert both rows roll
        # back. The fake models the atomicity the real store's transaction
        # provides by discarding the ticket when ``rollback`` is called.
        self.fail_after_create = fail_after_create

    async def create(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None = None
    ) -> UUID:
        """Insert a ticket and return its id."""
        row = TicketRow(
            subject=subject, body=body, customer_email=customer_email, external_id=external_id
        )
        self.rows[row.id] = row
        return row.id

    async def rollback(self, ticket_id: UUID) -> None:
        """Discard a ticket -- the fake's stand-in for a transaction rollback."""
        self.rows.pop(ticket_id, None)

    async def get(self, ticket_id: UUID) -> TicketRow | None:
        """Fetch a ticket by id."""
        return self.rows.get(ticket_id)

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[TicketRow]:
        """List tickets, newest first."""
        ordered = sorted(self.rows.values(), key=lambda row: row.created_at, reverse=True)
        return ordered[offset : offset + limit]


class FakeRunStore:
    """In-memory ``RunStore``, plus the reads the run routes make."""

    def __init__(self, *, fail_create: bool = False) -> None:
        self.rows: dict[UUID, AgentRun] = {}
        self.fail_create = fail_create
        self.steps: dict[UUID, list[object]] = {}
        self.tool_calls: dict[UUID, list[ToolCallRow]] = {}
        self.citations: dict[UUID, list[object]] = {}
        self.tool_spy = Spy()  # records any tool execution attempted
        self.worker_spy = Spy()  # records any call into the worker

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED``."""
        if self.fail_create:
            raise SimulatedRunInsertFailure
        run = AgentRun(
            id=uuid4(),
            ticket_id=ticket_id,
            status=RunStatus.RECEIVED,
            model_provider=model_provider,
            model_name=model_name,
            created_at=datetime.now(UTC),
        )
        self.rows[run.id] = run
        return run

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id."""
        return self.rows.get(run_id)

    async def claim_next(self, *, worker_id: str) -> AgentRun | None:
        """Claim the oldest claimable run."""
        self.worker_spy.record("claim_next", worker_id=worker_id)
        for run in sorted(self.rows.values(), key=lambda r: r.created_at):
            if run.status is RunStatus.RECEIVED:
                return run
        return None

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update a run's status in place."""
        run = self.rows[run_id]
        run.status = status
        if failure_reason is not None:
            run.failure_reason = failure_reason
        if completed_at is not None:
            run.completed_at = completed_at

    async def list(
        self, *, status: RunStatus | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[AgentRun]:
        """List runs, optionally filtered by status.

        The return annotation is ``Sequence`` rather than ``list`` because this
        method is named ``list``, and within the class body that name shadows the
        builtin in annotations -- a `list[AgentRun]` here would be read as a
        subscript of this very function.
        """
        rows = [row for row in self.rows.values() if status is None or row.status is status]
        ordered = sorted(rows, key=lambda row: row.created_at, reverse=True)
        return ordered[offset : offset + limit]

    async def list_for_ticket(self, ticket_id: UUID) -> Sequence[AgentRun]:
        """List a ticket's runs."""
        return [row for row in self.rows.values() if row.ticket_id == ticket_id]

    async def list_steps(self, run_id: UUID) -> Sequence[object]:
        """Return a run's steps."""
        return self.steps.get(run_id, [])

    async def list_tool_calls(self, run_id: UUID) -> Sequence[ToolCallRow]:
        """Return a run's tool calls."""
        return self.tool_calls.get(run_id, [])

    async def list_citations(self, run_id: UUID) -> Sequence[object]:
        """Return a run's citations."""
        return self.citations.get(run_id, [])

    def execute_tool(self, name: str, **kwargs: object) -> None:
        """Stand-in for any tool execution; records the attempt."""
        self.tool_spy.record(name, **kwargs)


class FakeApprovalStore:
    """In-memory ``ApprovalStore`` with conditional-update ``decide``.

    ``decide`` reproduces the contract's conditional update: it only moves a
    ``pending`` row, so a second call is observably a no-op that returns the
    already-decided row, which the router turns into a 409. The fake therefore
    tests the *router's* handling of the lost race, which is the point.
    """

    def __init__(self, run_store: FakeRunStore | None = None) -> None:
        self.rows: dict[UUID, ApprovalRequest] = {}
        self.run_store = run_store

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a pending approval."""
        self.rows[approval.id] = approval
        return approval

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequest | None:
        """Fetch the approval bound to a tool call."""
        for row in self.rows.values():
            if row.tool_call_id == tool_call_id:
                return row
        return None

    async def get(self, approval_id: UUID) -> ApprovalRequest | None:
        """Fetch an approval by id."""
        return self.rows.get(approval_id)

    async def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ApprovalRequest]:
        """List approvals, optionally filtered by status."""
        rows = [row for row in self.rows.values() if status is None or row.status is status]
        ordered = sorted(rows, key=lambda row: row.created_at, reverse=True)
        return ordered[offset : offset + limit]

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        note: str | None = None,
        decided_at: datetime,
    ) -> ApprovalRequest:
        """Conditional update: only a ``pending`` row moves.

        Returns the row either way -- decided or unchanged -- so the router sees
        the resulting status and can distinguish "we decided it" from "it was
        already decided". It also moves the run, which is the store's job in
        production; the router reads the run back and asserts the transition.
        """
        approval = self.rows[approval_id]
        if approval.status is not ApprovalStatus.PENDING:
            return approval
        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.decided_at = decided_at
        approval.decided_by = decided_by
        # ``note`` is accepted to match the port; the domain model has no field
        # for it, which is the store adapter's business, not the API's.
        _ = note
        if self.run_store is not None:
            run = self.run_store.rows[approval.run_id]
            run.status = RunStatus.EXECUTING if approved else RunStatus.RESPONDING
        return approval

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether an approved request exists for this tool call."""
        for row in self.rows.values():
            if row.tool_call_id == tool_call_id and row.status is ApprovalStatus.APPROVED:
                return True
        return False
