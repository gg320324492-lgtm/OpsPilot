"""Repository classes implementing the store ports.

Responsibility: concrete ``RunStore``, ``ToolCallStore``, ``ApprovalStore``,
``TicketStore`` and ``AuditStore`` over SQLAlchemy, mapping between ORM rows and
domain objects. The worker's claim query (``FOR UPDATE SKIP LOCKED``) and the
partial unique idempotency index live behind this layer.

Layer: ``adapters``. Implements ``opspilot.ports.stores``; imports SQLAlchemy
and ``opspilot.domain``.

Two rules this module must keep, both asserted by tests:

- ``AuditStore`` exposes **no mutating method** -- the ledger is append-only by
  convention (``test_audit_is_append_only``).
- A run's status update and its ``AgentStep(step_type='state_change')`` insert
  happen in **one transaction**, so a status change without its step is never
  observable (``docs/agent-state-machine.md`` §4).

M0: class signatures only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from opspilot.domain.approvals import ApprovalRequest
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus


class SqlRunStore:
    """SQLAlchemy ``RunStore``, including the worker's claim query."""

    async def create(self, *, ticket_id: UUID, model_provider: str, model_name: str) -> AgentRun:
        """Insert a run in ``RECEIVED``. M0 stub."""
        raise NotImplementedError

    async def get(self, run_id: UUID) -> AgentRun | None:
        """Fetch a run by id. M0 stub."""
        raise NotImplementedError

    async def claim_next(self, *, worker_id: str) -> AgentRun | None:
        """Claim the oldest claimable run with ``FOR UPDATE SKIP LOCKED``. M0 stub."""
        raise NotImplementedError

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update status and write the state-change step atomically. M0 stub."""
        raise NotImplementedError


class SqlToolCallStore:
    """SQLAlchemy ``ToolCallStore``."""

    async def record_proposed(
        self, *, run_id: UUID, tool_name: str, arguments: dict[str, object], permission: str
    ) -> UUID:
        """Insert a ``proposed`` tool call. M0 stub."""
        raise NotImplementedError

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
        """Transition a tool call and record its outcome. M0 stub."""
        raise NotImplementedError


class SqlApprovalStore:
    """SQLAlchemy ``ApprovalStore``."""

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a pending approval. M0 stub."""
        raise NotImplementedError

    async def get_for_tool_call(self, tool_call_id: UUID) -> ApprovalRequest | None:
        """Fetch the approval bound to a tool call. M0 stub."""
        raise NotImplementedError

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        note: str | None = None,
        decided_at: datetime,
    ) -> ApprovalRequest:
        """Record a decision and re-queue the run. M0 stub."""
        raise NotImplementedError

    async def has_approved(self, tool_call_id: UUID) -> bool:
        """Whether this tool call has an approved request. M0 stub."""
        raise NotImplementedError


class SqlTicketStore:
    """SQLAlchemy ``TicketStore``."""

    async def create(
        self, *, subject: str, body: str, customer_email: str, external_id: str | None = None
    ) -> UUID:
        """Insert a ticket. M0 stub."""
        raise NotImplementedError
