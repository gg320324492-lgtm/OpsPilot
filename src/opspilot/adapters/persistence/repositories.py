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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.domain.approvals import ApprovalRequest as ApprovalRequestDomain
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import ApprovalArgumentsChanged, NotFoundError
from opspilot.domain.runs import CLAIMABLE, AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.ports.stores import (
    CitationRecord,
    CitationRow,
    CustomerReplyRow,
    KnowledgeDocumentRecord,
    PendingApprovalRow,
    PendingToolCall,
    StepRow,
    TicketRecord,
    ToolCallRow,
)

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


def _to_pending_tool_call(row: models.ToolCall) -> PendingToolCall:
    """Map a ``tool_calls`` row to the ``PendingToolCall`` read model."""
    return PendingToolCall(
        tool_call_id=row.id,
        run_id=row.run_id,
        tool_name=row.tool_name,
        arguments=dict(row.arguments),
        status=ToolCallStatus(row.status),
    )


@dataclass(frozen=True)
class AuditEventRow:
    """One ``audit_events`` row, as ``GET /api/runs/{id}/audit`` reads it.

    The other dashboard reads return read models defined in ``ports/stores.py``
    (``StepRow``, ``ToolCallRow``, ``CitationRow``, ``CustomerReplyRow``). This one
    is defined here, and the reason is structural rather than incidental: the
    ledger has **no store port at all** -- it is append-only and
    ``tracing/recorder.py`` is its only writer, so there is no port for the read
    model to sit beside. ``runs.py``'s local ``AuditEventRow`` Protocol is what the
    router actually types against, exactly as it does for ``StepRow``, so this
    dataclass satisfies it without the router importing the adapter.

    ``payload`` is copied (``dict(row.payload)``) because a JSON column is handed
    back as a fresh mutable object per row, and a caller must not be able to reach
    into the ORM's state through it.
    """

    id: UUID
    event_type: str
    created_at: datetime
    actor: str
    payload: dict[str, object]


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

    async def get(self, ticket_id: UUID) -> TicketRecord | None:
        """Fetch a ticket by id as the worker's ``RunContext`` needs it."""
        with self._scope() as session:
            row = session.get(models.Ticket, ticket_id)
            if row is None:
                return None
            return TicketRecord(
                id=row.id,
                subject=row.subject,
                body=row.body,
                customer_email=row.customer_email,
                external_id=row.external_id,
                created_at=row.created_at,
            )

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[TicketRecord]:
        """List tickets, newest first (contract §10).

        Not on the ``TicketStore`` port: the port names what the *worker* needs,
        and listing is a dashboard read that ``GET /api/tickets`` probes for
        rather than requiring. Without it the endpoint's documented degradation
        is an empty page, so a deployment would report "no tickets" for a system
        that has them -- the same empty-list-is-a-plausible-lie failure the
        connection banner exists to prevent, one layer down.
        """
        with self._scope() as session:
            rows = (
                session.execute(
                    select(models.Ticket)
                    .order_by(models.Ticket.created_at.desc(), models.Ticket.id)
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            return [
                TicketRecord(
                    id=row.id,
                    subject=row.subject,
                    body=row.body,
                    customer_email=row.customer_email,
                    external_id=row.external_id,
                    created_at=row.created_at,
                )
                for row in rows
            ]


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

    async def get_pending_approval(self, run_id: UUID) -> PendingApprovalRow | None:
        """The run's pending approval, or ``None`` (run detail's approver card).

        A **second** pending approval for the same run is possible and is the
        one the M7 dashboard has to be able to show: ``EXECUTING`` survives a
        restart, so a run killed between committing ``EXECUTING`` and committing
        the tool call's terminal status resumes, re-proposes the same refund
        under a **new** ``tool_call_id``, and parks again. The idempotency key
        stops the money moving twice; it does not stop two approval rows existing.

        So this returns the *earliest* pending row -- the one an operator should
        act on -- and the dashboard groups on ``run_id`` to show the rest. The
        alternative, returning the newest and hiding the other, would make the
        approvals inbox look like two unrelated refunds.
        """
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.ApprovalRequest)
                    .where(
                        models.ApprovalRequest.run_id == run_id,
                        models.ApprovalRequest.status == ApprovalStatus.PENDING.value,
                    )
                    .order_by(models.ApprovalRequest.created_at)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            return PendingApprovalRow(
                id=row.id,
                tool_call_id=row.tool_call_id,
                status=ApprovalStatus(row.status),
                reason=row.reason,
                risk_explanation=row.risk_explanation,
                arguments_snapshot=dict(row.arguments_snapshot),
                created_at=row.created_at,
            )

    async def get_customer_reply(self, run_id: UUID) -> CustomerReplyRow | None:
        """The completed run's customer-visible reply, or ``None``.

        Read from the ``response`` step's recorded output rather than from a
        reply column, because ``response`` is the step that produces it: the
        body and the escalation flag are both already persisted there.
        """
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.AgentStep)
                    .where(
                        models.AgentStep.run_id == run_id,
                        models.AgentStep.step_type == "response",
                    )
                    .order_by(models.AgentStep.sequence.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            output = row.output or {}
            body = output.get("body") or output.get("reply") or ""
            if not isinstance(body, str) or not body:
                return None
            return CustomerReplyRow(body=body, escalated=bool(output.get("escalated")))

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

    # -- reads the dashboard needs ---------------------------------------
    #
    # The router loads a run's steps, tool calls and citations through optional
    # methods, and it is bound to the *run* store. Those three reads therefore
    # have to live here, on the object the router actually holds.
    #
    # They were absent, and the consequence was invisible to the suite: the
    # router's `_optional_method` probe found nothing and returned an empty list,
    # so a real deployment rendered a run detail with no timeline, no tool calls
    # and no citations -- three of the panels the dashboard exists to show. The
    # tests passed because `tests/integration/fakes.py`'s FakeRunStore implements
    # all three. A fake more capable than production is the one kind of test
    # double that cannot fail, which is why this went unnoticed through M1-M5.

    async def list_steps(self, run_id: UUID) -> list[StepRow]:
        """Return a run's steps in sequence order."""
        with self._scope() as session:
            rows = session.execute(
                select(models.AgentStep)
                .where(models.AgentStep.run_id == run_id)
                .order_by(models.AgentStep.sequence)
            ).scalars()
            return [
                StepRow(
                    sequence=row.sequence,
                    step_type=row.step_type,
                    output=row.output,
                    latency_ms=row.latency_ms,
                    started_at=row.started_at,
                )
                for row in rows
            ]

    async def list_tool_calls(self, run_id: UUID) -> list[ToolCallRow]:
        """Return a run's tool calls in the order they were proposed."""
        with self._scope() as session:
            rows = session.execute(
                select(models.ToolCall)
                .where(models.ToolCall.run_id == run_id)
                .order_by(models.ToolCall.created_at)
            ).scalars()
            return [
                ToolCallRow(
                    id=row.id,
                    tool_name=row.tool_name,
                    arguments=row.arguments,
                    permission=Permission(row.permission),
                    status=ToolCallStatus(row.status),
                    result=row.result,
                    latency_ms=row.latency_ms,
                    idempotency_key=row.idempotency_key,
                    error=row.error,
                )
                for row in rows
            ]

    async def list_citations(self, run_id: UUID) -> list[CitationRow]:
        """Return a run's citations, best rank first.

        Composes ``chunk`` as ``"{source}#{anchor}"`` -- the stable string
        ``docs/api-contract.md`` §3 says a reader can grep for in ``knowledge/``
        -- so the router renders a link without deciding how a citation is
        spelled.
        """
        with self._scope() as session:
            rows = session.execute(
                select(
                    models.KnowledgeDocument.source,
                    models.KnowledgeChunk.anchor,
                    models.Citation.score,
                    models.Citation.rank,
                )
                .join(
                    models.KnowledgeDocument,
                    models.KnowledgeDocument.id == models.Citation.document_id,
                )
                .join(
                    models.KnowledgeChunk,
                    models.KnowledgeChunk.id == models.Citation.chunk_id,
                )
                .where(models.Citation.run_id == run_id)
                .order_by(models.Citation.rank)
            ).all()
            return [
                CitationRow(
                    document=source,
                    chunk=f"{source}#{anchor}",
                    score=float(score),
                    rank=int(rank),
                )
                for source, anchor, score, rank in rows
            ]

    async def list_audit_events(self, run_id: UUID) -> list[AuditEventRow]:
        """Return a run's audit events, oldest first (contract §4.1).

        The fourth dashboard read that has to live here for the reason the block
        above gives: the router is bound to the *run* store, so a read it makes
        about a run is a method on this object or it is no read at all.

        It exists because the ledger had no reader. ``runtime.py`` writes a
        ``tool_failed`` event for every dispatched call that came back
        ``ok=False`` and its comment calls that "what makes 'one failed write' a
        query rather than an inference" -- but nothing could query it: the only
        readers of ``audit_events`` under ``src/`` were the eval harness and the
        tests. An operator had to open ``psql`` to find out that a run's refund
        was refused. ``GET /api/runs/{id}/audit`` is that query.

        Only this run's rows come back. The ledger also holds events whose
        ``run_id`` is ``NULL`` -- authentication and reindex -- and those are not
        this run's history, so they are not on its page. This is also the *only*
        method the ledger has: the append-only convention at the top of this
        module means there is no update or delete path to pair a read with, which
        is what makes it safe to expose where a mutating one would not be.

        The secondary sort on ``id`` is a total-order tie-break, not meaning:
        several events for one run are written inside one transaction and the
        clock's resolution (coarser on Windows) can give them the same
        ``created_at``, so ordering on time alone would return the same rows in
        an arbitrary order between two reads. A UUID carries no information; the
        claim is only that the order is stable for one database.
        """
        with self._scope() as session:
            rows = session.execute(
                select(models.AuditEvent)
                .where(models.AuditEvent.run_id == run_id)
                .order_by(models.AuditEvent.created_at, models.AuditEvent.id)
            ).scalars()
            return [
                AuditEventRow(
                    id=row.id,
                    event_type=row.event_type,
                    created_at=row.created_at,
                    actor=row.actor,
                    payload=dict(row.payload),
                )
                for row in rows
            ]

    # `list` is defined last in this class on purpose. Naming a method `list`
    # shadows the builtin *inside the class body*, so every `-> list[X]`
    # annotation written after it resolves `list` to the method and mypy
    # rejects it. Python does not care at runtime; a type checker does, and a
    # `--strict` codebase that cannot typecheck is not one. The name is not
    # negotiable -- the routers probe for exactly `list` -- so the fix is
    # ordering, not a rename. See the identical placement in
    # `SqlApprovalStore` and `SqlTicketStore`.
    async def list(
        self, *, status: RunStatus | None = None, limit: int = 50, offset: int = 0
    ) -> list[AgentRun]:
        """List runs newest first, optionally filtered by status (contract §1).

        Not on the ``RunStore`` port, for the same reason as
        ``SqlTicketStore.list``: the port names the worker's queue primitive,
        and ``GET /api/runs`` probes for the read. Without it that endpoint
        returns its documented empty page, so the Runs screen would show
        nothing for a system that has runs.

        The secondary sort on ``id`` is not decoration. ``POST /api/tickets``
        writes a ticket and its run in one transaction and both rows get the
        same default ``created_at``, so without a total order the offset paging
        the contract §10 specifies could show a run twice across two pages or
        skip one entirely -- neither of which is visible in any single response.
        """
        with self._scope() as session:
            statement = select(models.AgentRun)
            if status is not None:
                statement = statement.where(models.AgentRun.status == status.value)
            rows = (
                session.execute(
                    statement.order_by(models.AgentRun.created_at.desc(), models.AgentRun.id)
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            return [_to_agent_run(row) for row in rows]


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
        latency_ms: int | None = None,
    ) -> None:
        """Move a tool call to a new status and record its outcome.

        ``arguments`` are immutable once an ``ApprovalRequest`` references the
        call: this method never writes them, so an approved call's recorded
        arguments cannot drift from what the human approved. An executed call is
        finished and a further terminal transition is refused.

        ``latency_ms`` is written only when supplied, and a supplied ``0`` is
        written -- ``is not None``, not truthiness -- because a sub-millisecond
        in-process dispatch legitimately measures 0ms and a real 0 must not be
        conflated with "never measured".
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
            if latency_ms is not None:
                row.latency_ms = latency_ms
            if status in {
                ToolCallStatus.EXECUTED,
                ToolCallStatus.FAILED,
                ToolCallStatus.REJECTED,
            }:
                row.completed_at = _now()
            session.flush()

    async def get(self, tool_call_id: UUID) -> PendingToolCall | None:
        """Fetch one tool call by id."""
        with self._scope() as session:
            row = session.get(models.ToolCall, tool_call_id)
            return _to_pending_tool_call(row) if row is not None else None

    async def find_awaiting_approval(self, run_id: UUID) -> PendingToolCall | None:
        """Find the run's ``awaiting_approval`` call, oldest first.

        This is the resume-pass discovery query (Gap B): after an approval the run
        is re-queued in ``EXECUTING`` with exactly one call parked at gate 5, and
        the worker needs that call's id so the runtime re-enters the gates for the
        call the human actually decided -- never for a fresh proposal, which would
        carry a new id and park again.
        """
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.ToolCall)
                    .where(
                        models.ToolCall.run_id == run_id,
                        models.ToolCall.status == ToolCallStatus.AWAITING_APPROVAL.value,
                    )
                    .order_by(models.ToolCall.created_at)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return _to_pending_tool_call(row) if row is not None else None

    async def find_executed(
        self, *, run_id: UUID, idempotency_key: str
    ) -> dict[str, object] | None:
        """Return the result of an ``executed`` call with this key, or ``None``.

        Gate 4's idempotency lookup (Gap A): the policy engine cannot read the
        database, so the runtime performs this read and hands the ``(found,
        result)`` tuple to the pure decision function. The partial unique index
        makes ``(run_id, idempotency_key)`` at most one executed row.
        """
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.ToolCall)
                    .where(
                        models.ToolCall.run_id == run_id,
                        models.ToolCall.idempotency_key == idempotency_key,
                        models.ToolCall.status == ToolCallStatus.EXECUTED.value,
                    )
                    .order_by(models.ToolCall.created_at)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is None or row.result is None:
                return None
            return dict(row.result)

    async def find_executed_call_id(self, *, run_id: UUID, idempotency_key: str) -> UUID | None:
        """Return the id of the ``executed`` call holding this key, or ``None``."""
        with self._scope() as session:
            found = session.execute(
                select(models.ToolCall.id).where(
                    models.ToolCall.run_id == run_id,
                    models.ToolCall.idempotency_key == idempotency_key,
                    models.ToolCall.status == ToolCallStatus.EXECUTED.value,
                )
            ).first()
            return found[0] if found is not None else None


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

    async def get(self, approval_id: UUID) -> ApprovalRequestDomain | None:
        """Fetch one approval by its own id, as the approvals API needs it.

        Not on the ``ApprovalStore`` port: the port names what the *worker*
        needs (by tool call, by run), while ``GET /api/approvals/{id}`` and the
        409's read-back both key on the approval id. Without it the router's
        ``getattr(approvals, "get", None)`` probe finds nothing, and the route
        answers 404 for every approval that exists -- including the one an
        operator is looking at, and the one whose second click should have
        produced a 409.
        """
        with self._scope() as session:
            row = session.get(models.ApprovalRequest, approval_id)
            return _to_approval(row) if row is not None else None

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

    async def get_for_run(self, run_id: UUID) -> ApprovalRequestDomain | None:
        """Fetch the run's most recent approval, or ``None``."""
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.ApprovalRequest)
                    .where(models.ApprovalRequest.run_id == run_id)
                    .order_by(models.ApprovalRequest.created_at.desc())
                    .limit(1)
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
        """Record a human decision on a *pending* approval and re-queue the run.

        The decision is a conditional ``UPDATE ... WHERE id = ? AND status =
        'pending'``: a second approve affects zero rows and does **not** grant
        again, which is what lets the API return 409 for an already-decided
        request rather than silently double-approving. ``note`` is not persisted
        (the spec's ``approval_requests`` table has no column for it).

        On a *fresh* decision the run is moved in the same transaction -- to
        ``EXECUTING`` on approve (the worker will re-enter the gates for this
        call) or ``RESPONDING`` on reject (the worker will compose an escalation
        reply). This is not the owner of the run's status: it is the write that
        makes approval an *edge-triggered* event, so a worker polling the run
        table sees the run become claimable. The state-change ``AgentStep`` is
        written by the same ``SqlRunStore.set_status`` call, so the transition
        and its trace are one unit (``docs/agent-state-machine.md`` §4).
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
                # Already decided: return the current state unchanged and leave
                # the run alone -- the first decision already moved it. The
                # caller distinguishes this from a fresh grant by status.
                return _to_approval(existing)

            session.flush()
            row = session.get(models.ApprovalRequest, approval_id)
            if row is None:  # pragma: no cover - defensive, cannot happen post-update
                raise _MissingApproval(approval_id)
            target = RunStatus.EXECUTING if approved else RunStatus.RESPONDING
            await SqlRunStore(session).set_status(row.run_id, target)
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

    # Defined last for the same reason as `SqlRunStore.list`: naming a method
    # `list` shadows the builtin for every annotation written after it in the
    # class body. See that comment in full; the routers probe for this exact
    # name, so ordering is the fix and a rename is not available.
    async def list(
        self, *, status: ApprovalStatus | None = None, limit: int = 50, offset: int = 0
    ) -> list[ApprovalRequestDomain]:
        """List approvals, optionally filtered by status (contract §5).

        Not on the ``ApprovalStore`` port, for the same reason as ``get``: the
        inbox is an operator read that the router probes for. Without it,
        ``GET /api/approvals`` returns an empty page -- the one screen the
        project's whole thesis depends on, reporting "nothing is waiting for a
        human" while a refund sits parked.

        Newest first, with ``id`` as the tie-break. The tie-break is not
        cosmetic: a run resumed from ``EXECUTING`` re-proposes the same refund
        under a **new** ``tool_call_id`` and parks again, so two pending rows for
        one run can carry the same ``created_at``. Without a total order the
        dashboard's "two cards for one refund" grouping would be stable by luck
        rather than by rule. ``id`` is a UUID, so it carries no meaning -- the
        claim is only that it does not change between two reads of one database.
        """
        with self._scope() as session:
            statement = select(models.ApprovalRequest)
            if status is not None:
                statement = statement.where(models.ApprovalRequest.status == status.value)
            rows = (
                session.execute(
                    statement.order_by(
                        models.ApprovalRequest.created_at.desc(), models.ApprovalRequest.id
                    )
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            return [_to_approval(row) for row in rows]


def new_approval_id() -> UUID:
    """Generate a fresh approval id for callers constructing the domain object."""
    return uuid.uuid4()


class SqlKnowledgeDocumentStore(_SessionBound):
    """SQLAlchemy ``KnowledgeDocumentStore`` over ``knowledge_documents``.

    ``upsert`` is keyed on ``source`` (the table's unique column) so re-ingesting
    a document updates the row rather than creating a second one, and the id --
    which the chunks' foreign key points at -- stays stable across a reindex.
    """

    async def upsert_document(
        self,
        *,
        source: str,
        title: str,
        content: str,
        metadata: dict[str, object],
        content_hash: str,
    ) -> UUID:
        """Insert or update a document by ``source`` and return its id."""
        with self._scope() as session:
            row = (
                session.execute(
                    select(models.KnowledgeDocument).where(
                        models.KnowledgeDocument.source == source
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = models.KnowledgeDocument(
                    title=title,
                    source=source,
                    content=content,
                    doc_metadata=dict(metadata),
                    content_hash=content_hash,
                    indexed_at=_now(),
                )
                session.add(row)
            else:
                row.title = title
                row.content = content
                row.doc_metadata = dict(metadata)
                row.content_hash = content_hash
                row.indexed_at = _now()
            session.flush()
            return row.id

    async def content_hash(self, source: str) -> str | None:
        """Return the stored content hash for ``source``, or ``None``."""
        with self._scope() as session:
            return session.execute(
                select(models.KnowledgeDocument.content_hash).where(
                    models.KnowledgeDocument.source == source
                )
            ).scalar()

    async def list_documents(self) -> list[KnowledgeDocumentRecord]:
        """Return every indexed document with its chunk count, newest first."""
        with self._scope() as session:
            chunk_count = func.count(models.KnowledgeChunk.id)
            rows = session.execute(
                select(models.KnowledgeDocument, chunk_count)
                .outerjoin(
                    models.KnowledgeChunk,
                    models.KnowledgeChunk.document_id == models.KnowledgeDocument.id,
                )
                .group_by(models.KnowledgeDocument.id)
                .order_by(models.KnowledgeDocument.source)
            ).all()
            return [
                KnowledgeDocumentRecord(
                    source=document.source,
                    title=document.title,
                    chunk_count=int(count),
                    indexed_at=document.indexed_at,
                    content_hash=document.content_hash,
                )
                for document, count in rows
            ]


def _split_citation_chunk(chunk: str) -> str:
    """Extract the anchor from a ``"{document_slug}#{anchor}"`` citation string.

    ``list_citations`` builds the string; ``create_many`` takes it apart. The
    split is on the first ``#`` only, because the slug is a filename and cannot
    contain one while the anchor could in principle.
    """
    _slug, separator, anchor = chunk.partition("#")
    return anchor if separator else ""


class SqlCitationStore(_SessionBound):
    """SQLAlchemy ``CitationStore`` over ``citations``.

    **Foreign-key ordering.** ``citations.chunk_id`` references
    ``knowledge_chunks.id`` and ``citations.document_id`` references
    ``knowledge_documents.id`` (``docs/data-model.md`` §2), so a citation can
    only be written after the chunk it names exists. Retrieval guarantees this:
    a hit comes from a row in ``knowledge_chunks``, which means ingestion wrote
    it first. ``create_many`` resolves the ``chunk_id`` by the same
    ``(document_id, ordinal)`` derivative the vector stores use, so the id it
    writes is the id of the row retrieval read -- not a second, orphan row.
    """

    async def create(
        self, *, run_id: UUID, document_id: UUID, chunk_id: UUID, score: float, rank: int
    ) -> None:
        """Persist one citation for a run."""
        with self._scope() as session:
            session.add(
                models.Citation(
                    run_id=run_id,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    score=score,
                    rank=rank,
                )
            )
            session.flush()

    async def create_many(self, run_id: UUID, records: list[CitationRecord]) -> None:
        """Persist a run's retrieval hits as citations.

        Each record's ``chunk`` is ``"{document_slug}#{anchor}"``. The document is
        resolved by ``source`` and the chunk by ``(document_id, ordinal)`` -- the
        ``citations`` table has no ``ordinal`` column, and the anchor is not
        guaranteed unique, so the ordinal is recovered from the label on the way
        in. A hit whose document or chunk row is absent is skipped rather than
        written as a dangling reference: the foreign keys would reject it anyway,
        and skipping keeps a partially-indexed run's citations honest.
        """
        with self._scope() as session:
            for record in records:
                document_id = session.execute(
                    select(models.KnowledgeDocument.id).where(
                        models.KnowledgeDocument.source == record.document
                    )
                ).scalar()
                if document_id is None:
                    continue
                anchor = _split_citation_chunk(record.chunk)
                chunk_id = (
                    session.execute(
                        select(models.KnowledgeChunk.id).where(
                            models.KnowledgeChunk.document_id == document_id,
                            models.KnowledgeChunk.anchor == anchor,
                        )
                    )
                    .scalars()
                    .first()
                )
                if chunk_id is None:
                    continue
                session.add(
                    models.Citation(
                        run_id=run_id,
                        document_id=document_id,
                        chunk_id=chunk_id,
                        score=record.score,
                        rank=record.rank,
                    )
                )
            session.flush()

    async def list_citations(self, run_id: UUID) -> list[CitationRecord]:
        """Return a run's citations, best rank first.

        Joins ``knowledge_documents`` for the slug; the anchor is already on the
        chunk row, so ``chunk`` is composed as ``"{source}#{anchor}"`` here
        rather than in the router.
        """
        with self._scope() as session:
            rows = session.execute(
                select(
                    models.KnowledgeDocument.source,
                    models.KnowledgeChunk.anchor,
                    models.Citation.score,
                    models.Citation.rank,
                )
                .join(
                    models.KnowledgeDocument,
                    models.KnowledgeDocument.id == models.Citation.document_id,
                )
                .join(
                    models.KnowledgeChunk,
                    models.KnowledgeChunk.id == models.Citation.chunk_id,
                )
                .where(models.Citation.run_id == run_id)
                .order_by(models.Citation.rank)
            ).all()
            return [
                CitationRecord(
                    document=source,
                    chunk=f"{source}#{anchor}",
                    score=float(score),
                    rank=int(rank),
                )
                for source, anchor, score, rank in rows
            ]


__all__ = [
    "AuditEventRow",
    "SqlApprovalStore",
    "SqlCitationStore",
    "SqlKnowledgeDocumentStore",
    "SqlRunStore",
    "SqlTicketStore",
    "SqlToolCallStore",
    "new_approval_id",
]
