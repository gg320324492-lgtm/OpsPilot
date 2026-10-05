"""Repository classes implementing the store ports.

Responsibility: concrete ``RunStore``, ``ToolCallStore``, ``ApprovalStore`` and
``TicketStore`` over SQLAlchemy, mapping between ORM rows and domain objects.
The worker's claim query (``FOR UPDATE SKIP LOCKED``) and the partial unique
idempotency index live behind this layer.

Layer: ``adapters``. Implements ``opspilot.ports.stores``; imports SQLAlchemy
and ``opspilot.domain``.

Two rules this module keeps, both asserted by tests:

- The audit ledger is append-only by convention -- no method here updates or
  deletes an ``AuditEvent``.
- A run's status update and its ``AgentStep(step_type='state_change')`` insert
  happen in **one transaction**, so a status change without its step is never
  observable (``docs/agent-state-machine.md`` §4).

Every method takes a ``Session`` and **never commits**. Ending the transaction
is ``db.session_scope``'s job, so a caller can compose several repository calls
-- creating a ticket and its run, or parking a run and writing its approval --
into one atomic unit.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.domain.approvals import ApprovalRequest as ApprovalRequestDomain
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import ApprovalArgumentsChanged, NotFoundError
from opspilot.domain.runs import CLAIMABLE, AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus

# A repository is bound either to a live ``Session`` (so a caller can compose
# several repository calls in one transaction -- the ticket+run creation the API
# needs) or to a ``sessionmaker`` (each call gets its own committed transaction,
# which is how the FastAPI app's default wiring builds the stores). The
# ``Session`` form is the one the port contract is written around; the factory
# form exists so the API's dependency construction works without a wiring shim.
type SessionLike = Session | sessionmaker[Session]


class _MissingRun(NotFoundError):
    """An agent run id was referenced but no row exists."""

    def __init__(self, run_id: UUID) -> None:
        super().__init__(f"agent run {run_id} does not exist")


class _MissingToolCall(NotFoundError):
    """A tool call id was referenced but no row exists."""

    def __init__(self, tool_call_id: UUID) -> None:
        super().__init__(f"tool call {tool_call_id} does not exist")


class _MissingApproval(NotFoundError):
    """An approval id was referenced but no row exists."""

    def __init__(self, approval_id: UUID) -> None:
        super().__init__(f"approval request {approval_id} does not exist")


class _FrozenToolCall(ApprovalArgumentsChanged):
    """An executed call's arguments may not change."""

    def __init__(self, tool_call_id: UUID) -> None:
        super().__init__(f"tool call {tool_call_id} is already executed; its arguments are frozen")


# Claimable statuses come from the domain enum, never retyped: if a state is
# added to the machine and not excluded there, the worker starts claiming it --
# the safe direction, and ``test_waiting_approval_is_not_claimed`` guards it.
_CLAIMABLE_VALUES: tuple[str, ...] = tuple(sorted(s.value for s in CLAIMABLE))


class _SessionBound:
    """Shared plumbing: resolve a bound ``Session`` or open one from a factory.

    When bound to a ``Session`` the store uses it directly and never commits --
    the surrounding ``db.session_scope`` owns the transaction. When bound to a
    ``sessionmaker`` the store opens (and commits) a transaction per call, which
    is the standalone form the API's default wiring uses.
    """

    def __init__(self, session_or_factory: SessionLike) -> None:
        """Bind to a live ``Session`` or a ``sessionmaker``."""
        self._bound = session_or_factory

    @contextmanager
    def _scope(self) -> Iterator[Session]:
        """Yield the store's session, committing only if the store owns it."""
        if isinstance(self._bound, Session):
            yield self._bound
            return
        with db.session_scope(self._bound) as session:
            yield session


def _now() -> datetime:
    """A timezone-aware UTC ``now``."""
    return datetime.now(UTC)


def _to_agent_run(row: models.AgentRun) -> AgentRun:
    """Map an ``agent_runs`` row to the domain ``AgentRun``."""
    return AgentRun(
        id=row.id,
        ticket_id=row.ticket_id,
        status=RunStatus(row.status),
        model_provider=row.model_provider,
        model_name=row.model_name,
        started_at=row.started_at,
        completed_at=row.completed_at,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
    )


def _to_approval(row: models.ApprovalRequest) -> ApprovalRequestDomain:
    """Map an ``approval_requests`` row to the domain ``ApprovalRequest``."""
    return ApprovalRequestDomain(
        id=row.id,
        run_id=row.run_id,
        tool_call_id=row.tool_call_id,
        status=ApprovalStatus(row.status),
        reason=row.reason,
        risk_explanation=row.risk_explanation,
        arguments_snapshot=dict(row.arguments_snapshot),
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
    )


class SqlTicketStore(_SessionBound):
    """SQLAlchemy ``TicketStore``."""

    async def create(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None = None
    ) -> UUID:
        """Insert a ticket and return its id."""
        with self._scope() as session:
            ticket = models.Ticket(
                subject=subject,
                body=body,
                customer_email=customer_email,
                external_id=external_id,
            )
            session.add(ticket)
            session.flush()
            return ticket.id


class SqlRunStore(_SessionBound):
    """SQLAlchemy ``RunStore``, including the worker's claim query."""

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED`` and return it."""
        with self._scope() as session:
            run = models.AgentRun(
                ticket_id=ticket_id,
                status=RunStatus.RECEIVED.value,
                model_provider=model_provider,
                model_name=model_name,
            )
            session.add(run)
            session.flush()
            return _to_agent_run(run)

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id."""
        with self._scope() as session:
            row = session.get(models.AgentRun, run_id)
            return _to_agent_run(row) if row is not None else None

    async def claim_next(self, *, worker_id: str) -> AgentRun | None:  # noqa: ARG002
        """Claim the oldest claimable run, or return ``None``.

        On Postgres this is ``SELECT ... WHERE status IN (<claimable>) ORDER BY
        created_at FOR UPDATE SKIP LOCKED LIMIT 1`` -- ``SKIP LOCKED`` lets two
        workers take different rows without blocking. SQLite has no ``SKIP
        LOCKED``, so the same statement runs without the locking clause and the
        claim is single-threaded only; the two-worker concurrency test is
        ``@pytest.mark.postgres`` and runs in CI (ADR-0004).

        ``worker_id`` is part of the port signature; the claim does not need to
        store it, since the row's holder is the open transaction.
        """
        with self._scope() as session:
            statement = (
                select(models.AgentRun)
                .where(models.AgentRun.status.in_(_CLAIMABLE_VALUES))
                .order_by(models.AgentRun.created_at)
                .limit(1)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = session.execute(statement).scalars().first()
            return _to_agent_run(row) if row is not None else None

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update status and, in the same transaction, write the state-change step.

        The previous status is read to record ``(from_status, to_status)`` in the
        step. ``started_at`` is set on the first transition out of ``RECEIVED``;
        ``completed_at`` on entry to a terminal state.
        """
        with self._scope() as session:
            row = session.get(models.AgentRun, run_id)
            if row is None:
                raise _MissingRun(run_id)

            from_status = row.status
            row.status = status.value
            if failure_reason is not None:
                row.failure_reason = failure_reason
            if from_status == RunStatus.RECEIVED.value and row.started_at is None:
                row.started_at = _now()
            if status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                row.completed_at = completed_at or _now()

            session.add(
                models.AgentStep(
                    run_id=run_id,
                    sequence=self._next_sequence(session, run_id),
                    step_type="state_change",
                    output={
                        "from_status": from_status,
                        "to_status": status.value,
                        "at": _now().isoformat(),
                    },
                    started_at=_now(),
                )
            )
            session.flush()

    def _next_sequence(self, session: Session, run_id: UUID) -> int:
        """Allocate the next step sequence for a run, monotonic within it.

        ``SELECT COALESCE(MAX(sequence), 0) + 1`` in the same transaction as the
        insert, so two writers on one run cannot allocate the same number. There
        is only ever one writer per run because the claim takes the row first.
        """
        highest = session.execute(
            select(models.AgentStep.sequence)
            .where(models.AgentStep.run_id == run_id)
            .order_by(models.AgentStep.sequence.desc())
            .limit(1)
        ).scalar()
        return (highest or 0) + 1


class SqlToolCallStore(_SessionBound):
    """SQLAlchemy ``ToolCallStore``."""

    async def record_proposed(
        self, *, run_id: UUID, tool_name: str, arguments: dict[str, object], permission: str
    ) -> UUID:
        """Insert a ``proposed`` tool call and return its id."""
        with self._scope() as session:
            call = models.ToolCall(
                run_id=run_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                permission=permission,
                status=ToolCallStatus.PROPOSED.value,
            )
            session.add(call)
            session.flush()
            return call.id

    async def set_status(
        self,
        tool_call_id: UUID,
        status: ToolCallStatus,
        *,
        result: dict[str, object] | None = None,
        error: str | None = None,
        rejection_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """Move a tool call to a new status and record its outcome.

        ``arguments`` are immutable once an ``ApprovalRequest`` references the
        call: this method never writes them, so an approved call's recorded
        arguments cannot drift from what the human approved. An executed call is
        finished and a further terminal transition is refused.
        """
        with self._scope() as session:
            row = session.get(models.ToolCall, tool_call_id)
            if row is None:
                raise _MissingToolCall(tool_call_id)

            if row.status == ToolCallStatus.EXECUTED.value:
                raise _FrozenToolCall(tool_call_id)

            row.status = status.value
            if result is not None:
                row.result = dict(result)
            if error is not None:
                row.error = error
            if rejection_reason is not None:
                row.rejection_reason = rejection_reason
            if idempotency_key is not None:
                row.idempotency_key = idempotency_key
            if status in {
                ToolCallStatus.EXECUTED,
                ToolCallStatus.FAILED,
                ToolCallStatus.REJECTED,
            }:
                row.completed_at = _now()
            session.flush()


class SqlApprovalStore(_SessionBound):
    """SQLAlchemy ``ApprovalStore``."""

    async def create(self, approval: ApprovalRequestDomain) -> ApprovalRequestDomain:
        """Persist a pending approval for one tool call."""
        with self._scope() as session:
            row = models.ApprovalRequest(
                id=approval.id,
                run_id=approval.run_id,
                tool_call_id=approval.tool_call_id,
                status=approval.status.value,
                reason=approval.reason,
                risk_explanation=approval.risk_explanation,
                arguments_snapshot=dict(approval.arguments_snapshot),
                created_at=approval.created_at,
                decided_at=approval.decided_at,
                decided_by=approval.decided_by,
            )
            session.add(row)
            session.flush()
            return _to_approval(row)

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequestDomain | None:
        """Fetch the (unique) approval bound to a tool call."""
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.ApprovalRequest).where(
                        models.ApprovalRequest.tool_call_id == tool_call_id
                    )
                )
                .scalars()
                .first()
            )
            return _to_approval(row) if row is not None else None

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        note: str | None = None,  # noqa: ARG002 -- part of the port signature; no column
        decided_at: datetime,
    ) -> ApprovalRequestDomain:
        """Record a human decision on a *pending* approval.

        The transition is a conditional ``UPDATE ... WHERE id = ? AND status =
        'pending'``: a second approve affects zero rows and does **not** grant
        again, which is what lets the API return 409 for an already-decided
        request rather than silently double-approving. The updated row is
        re-read and returned; ``note`` is not persisted (the spec's
        ``approval_requests`` table has no column for it) and is accepted only
        for port compatibility.
        """
        with self._scope() as session:
            new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            result = cast(
                CursorResult[tuple[object, ...]],
                session.execute(
                    update(models.ApprovalRequest)
                    .where(
                        models.ApprovalRequest.id == approval_id,
                        models.ApprovalRequest.status == ApprovalStatus.PENDING.value,
                    )
                    .values(
                        status=new_status.value,
                        decided_at=decided_at,
                        decided_by=decided_by,
                    )
                ),
            )
            if result.rowcount == 0:
                existing = session.get(models.ApprovalRequest, approval_id)
                if existing is None:
                    raise _MissingApproval(approval_id)
                # Already decided: return the current state unchanged. The caller
                # distinguishes this from a fresh grant by status/decided_at.
                return _to_approval(existing)

            session.flush()
            row = session.get(models.ApprovalRequest, approval_id)
            if row is None:  # pragma: no cover - defensive, cannot happen post-update
                raise _MissingApproval(approval_id)
            return _to_approval(row)

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether an approved request exists for *this* tool call.

        A database read on purpose: the approval may have been granted by the
        API process while the worker was down, and the check is bound to the
        tool call id so approving one refund cannot authorise another.
        """
        with self._scope() as session:
            found = session.execute(
                select(models.ApprovalRequest.id).where(
                    models.ApprovalRequest.tool_call_id == tool_call_id,
                    models.ApprovalRequest.status == ApprovalStatus.APPROVED.value,
                )
            ).first()
            return found is not None


def new_approval_id() -> UUID:
    """Generate a fresh approval id for callers constructing the domain object."""
    return uuid.uuid4()


__all__ = [
    "SqlApprovalStore",
    "SqlRunStore",
    "SqlTicketStore",
    "SqlToolCallStore",
    "new_approval_id",
]
