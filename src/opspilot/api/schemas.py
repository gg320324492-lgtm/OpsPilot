"""Request and response Pydantic models for the HTTP surface.

Responsibility: the wire contract -- what a client sends and receives -- kept
separate from the domain models so an internal refactor does not silently change
the public API, and so the API can expose exactly the fields the dashboard needs
(never, for example, a raw model prompt).

Layer: ``api``.

A response model may embed domain enums (``RunStatus``, ``Permission``) because
those values *are* the contract; it must not leak ORM rows. See
``docs/api-contract.md``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus


class TicketCreateRequest(BaseModel):
    """Body of ``POST /api/tickets``."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str
    customer_email: str
    external_id: str | None = None


class TicketCreateResponse(BaseModel):
    """The created ticket and the run enqueued for it."""

    ticket_id: UUID
    run_id: UUID
    status: RunStatus


class RunSummary(BaseModel):
    """A run as listed or fetched."""

    id: UUID
    ticket_id: UUID
    status: RunStatus
    model_provider: str
    model_name: str
    created_at: datetime
    completed_at: datetime | None = None
    failure_reason: str | None = None


class ToolCallSummary(BaseModel):
    """A tool call in a run's trace."""

    id: UUID
    tool_name: str
    permission: Permission
    status: ToolCallStatus
    created_at: datetime


class ApprovalSummary(BaseModel):
    """An approval as shown in the approvals inbox."""

    id: UUID
    run_id: UUID
    tool_call_id: UUID
    status: ApprovalStatus
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object]
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class ApprovalDecisionRequest(BaseModel):
    """Body of ``POST /api/approvals/{id}/approve`` and ``/reject``."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class KnowledgeReindexResponse(BaseModel):
    """Result of ``POST /api/knowledge/reindex``."""

    documents_indexed: int
    chunks_written: int
