"""The persistence ports: ``RunStore``, ``ToolCallStore``, ``ApprovalStore``,
``TicketStore``.

Responsibility: the storage boundaries the runtime and API depend on. Splitting
the stores by aggregate (rather than one god-repository) keeps each Protocol
small enough that an adapter is obviously correct, and keeps the audit surface
honest -- ``AuditStore`` deliberately exposes no mutating method, because the
ledger is append-only by convention.

Layer: ``ports``. Imports only ``typing``, ``datetime``/``uuid`` from the stdlib
and ``opspilot.domain``. The SQLAlchemy implementation lives in
``opspilot.adapters.persistence`` and is the *only* place that knows the ORM
exists. See ``docs/data-model.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from opspilot.domain.approvals import ApprovalRequest
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus


@runtime_checkable
class TicketStore(Protocol):
    """Reads and writes tickets."""

    async def create(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None = None
    ) -> UUID:
        """Insert a ticket and return its id."""
        ...


@runtime_checkable
class RunStore(Protocol):
    """Reads and writes agent runs.

    ``claim_next`` is the worker's queue primitive: ``SELECT ... FOR UPDATE SKIP
    LOCKED LIMIT 1`` over the claimable statuses, ordered by ``created_at``.
    SQLite cannot express ``SKIP LOCKED`` and degrades to a single-threaded
    claim (see ``docs/data-model.md`` §6).
    """

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED`` and return it."""
        ...

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id."""
        ...

    async def claim_next(self, *, worker_id: str) -> AgentRun | None:
        """Atomically claim the oldest claimable run, or return ``None``."""
        ...

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update status and, in the same transaction, write the state-change step."""
        ...


@runtime_checkable
class ToolCallStore(Protocol):
    """Reads and writes tool calls."""

    async def record_proposed(
        self, *, run_id: UUID, tool_name: str, arguments: dict[str, object], permission: str
    ) -> UUID:
        """Insert a ``proposed`` tool call and return its id."""
        ...

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
        """Move a tool call to a new status and record its outcome."""
        ...


@runtime_checkable
class ApprovalStore(Protocol):
    """Reads and writes approval requests."""

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a pending approval for one tool call."""
        ...

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequest | None:
        """Fetch the (unique) approval bound to a tool call."""
        ...

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        note: str | None = None,
        decided_at: datetime,
    ) -> ApprovalRequest:
        """Record a human decision and flip the run back to a claimable state."""
        ...

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether an approved request exists for *this* tool call.

        A database read on purpose: the approval may have been granted by the
        API process while the worker was down, and the check is bound to the
        tool call id so approving one refund cannot authorise another.
        """
        ...
