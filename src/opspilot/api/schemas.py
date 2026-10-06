"""Request and response Pydantic models for the HTTP surface.

Responsibility: the wire contract -- what a client sends and receives -- kept
separate from the domain models so an internal refactor does not silently change
the public API, and so the API can expose exactly the fields the dashboard needs
(never, for example, a raw model prompt).

Layer: ``api``.

A response model may embed domain enums (``RunStatus``, ``Permission``) because
those values *are* the contract; it must not leak ORM rows. See
``docs/api-contract.md``.

Every model here is transcribed from ``docs/api-contract.md``: the field names
are the contract, so they are matched literally rather than paraphrased. Where a
row's exact value is not known at serialization time (a step's ``label``, an
approval's ``context``), the field is filled by the router from persisted data
rather than left to the client.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus

# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class TicketDetail(BaseModel):
    """A ticket as returned by ``POST /api/tickets`` and ``GET /api/tickets/{id}``."""

    id: UUID
    external_id: str | None = None
    subject: str
    body: str
    customer_email: str
    created_at: datetime


class RunRef(BaseModel):
    """A run reference embedded in the ticket-create response (``{id, status}``)."""

    id: UUID
    status: RunStatus
    created_at: datetime


class StepDetail(BaseModel):
    """One execution-timeline step in the run-detail payload (contract §3)."""

    sequence: int
    step_type: str
    output: dict[str, object] | None = None
    latency_ms: int | None = None
    started_at: datetime


class TraceStep(BaseModel):
    """One step in the cheap trace payload (contract §4).

    ``label`` is already human-readable and ``detail`` is a short summary string,
    both assembled server-side, so rendering the timeline requires no client-side
    logic beyond ordering.
    """

    sequence: int
    step_type: str
    label: str
    detail: str
    latency_ms: int | None = None
    at: datetime


class ToolCallDetail(BaseModel):
    """One tool call in a run's detail payload (contract §3)."""

    id: UUID
    tool_name: str
    arguments: dict[str, object]
    permission: Permission
    status: ToolCallStatus
    result: dict[str, object] | None = None
    latency_ms: int | None = None
    idempotency_key: str | None = None
    error: str | None = None


class CitationDetail(BaseModel):
    """One cited knowledge chunk (contract §3).

    ``chunk`` is ``"{document_slug}#{anchor}"`` -- a stable string a reader can
    grep for in ``knowledge/``.
    """

    document: str
    chunk: str
    score: float
    rank: int


class ApprovalContext(BaseModel):
    """The extra context shown beside a pending approval (contract §5).

    Assembled from already-persisted data; it triggers no tool calls.
    """

    company: str | None = None
    ticket_subject: str | None = None


class PendingApproval(BaseModel):
    """The approver-visible payload nested inside a parked run (contract §3)."""

    id: UUID
    tool_call_id: UUID
    status: ApprovalStatus
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object]
    created_at: datetime


class CustomerReply(BaseModel):
    """The customer-visible reply, set only at ``COMPLETED`` (contract §3).

    ``escalated`` is declared rather than smuggled through ``extra="allow"``:
    it is the field that distinguishes "we could not complete this and a human
    is on it" from "here is your refund", and a declared field is one the
    OpenAPI document publishes and the dashboard's generated types carry. A
    field that only exists at runtime is a field a client cannot render.
    """

    body: str
    escalated: bool = False
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


class TicketCreateRequest(BaseModel):
    """Body of ``POST /api/tickets``."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str
    customer_email: str
    external_id: str | None = None


class TicketCreateResponse(BaseModel):
    """The created ticket and the run enqueued for it (contract §2).

    The nested ``ticket`` + ``run`` shape is deliberate: the client gets both ids
    and the run's starting status in one response, so it can begin polling
    ``GET /api/runs/{id}`` without a second request.
    """

    ticket: TicketDetail
    run: RunRef


class TicketListResponse(BaseModel):
    """Body of ``GET /api/tickets`` (contract §10)."""

    items: list[TicketDetail]
    total: int


class TicketDetailResponse(BaseModel):
    """Body of ``GET /api/tickets/{ticket_id}``: the ticket and its runs."""

    ticket: TicketDetail
    runs: list[RunRef]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    """Body of ``POST /api/runs`` -- enqueue a run for an existing ticket."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID


class RunSummary(BaseModel):
    """A run as listed by ``GET /api/runs``."""

    id: UUID
    ticket_id: UUID
    status: RunStatus
    model_provider: str
    model_name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime


class RunListResponse(BaseModel):
    """Body of ``GET /api/runs`` (contract §10)."""

    items: list[RunSummary]
    total: int


class RunDetail(BaseModel):
    """Body of ``GET /api/runs/{run_id}`` -- the run-detail screen (contract §3).

    ``pending_approval`` is present only when the run is parked; ``customer_reply``
    is set only at ``COMPLETED``.
    """

    id: UUID
    ticket_id: UUID
    status: RunStatus
    model_provider: str
    model_name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime
    steps: list[StepDetail] = Field(default_factory=list)
    tool_calls: list[ToolCallDetail] = Field(default_factory=list)
    citations: list[CitationDetail] = Field(default_factory=list)
    pending_approval: PendingApproval | None = None
    customer_reply: CustomerReply | None = None


class TraceResponse(BaseModel):
    """Body of ``GET /api/runs/{run_id}/trace`` (contract §4)."""

    run_id: UUID
    steps: list[TraceStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalSummary(BaseModel):
    """An approval as shown in the approvals inbox (contract §5)."""

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
    context: ApprovalContext | None = None


class ApprovalListResponse(BaseModel):
    """Body of ``GET /api/approvals`` (contract §5, §10)."""

    items: list[ApprovalSummary]
    total: int


class ApprovalDecisionRequest(BaseModel):
    """Body of ``POST /api/approvals/{id}/approve`` and ``/reject``.

    The body may be empty; ``decided_by`` defaults to the operator identity
    resolved from the bearer token, and ``note`` is optional.
    """

    model_config = ConfigDict(extra="forbid")

    decided_by: str | None = None
    note: str | None = None


class ApprovalDecisionResponse(BaseModel):
    """The 200 body of an approve/reject (contract §5).

    ``run`` is the *transitioned* run -- ``executing`` after an approve,
    ``responding`` after a reject -- so the dashboard can update without a
    follow-up fetch.
    """

    id: UUID
    status: ApprovalStatus
    decided_at: datetime
    decided_by: str
    run: RunRef


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class KnowledgeDocumentSummary(BaseModel):
    """One indexed document in ``GET /api/knowledge``."""

    source: str
    title: str
    chunk_count: int
    indexed_at: datetime
    content_hash: str


class KnowledgeListResponse(BaseModel):
    """Body of ``GET /api/knowledge``."""

    items: list[KnowledgeDocumentSummary]
    total: int


class KnowledgeReindexResponse(BaseModel):
    """Result of ``POST /api/knowledge/reindex`` (contract §9).

    ``documents_seen`` counts every file found; ``documents_indexed`` counts only
    those whose ``content_hash`` changed and were re-embedded, so a repeat call
    returns ``documents_indexed == 0`` on an unchanged tree.
    """

    documents_seen: int
    documents_indexed: int
    chunks_written: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Body of ``GET /health`` (contract §7). Liveness only."""

    status: str = "ok"
    version: str


class ReadyResponse(BaseModel):
    """Body of ``GET /ready`` (contract §7).

    ``checks`` names each probe so a 503 body states which one failed rather than
    a bare "not ready".
    """

    status: str
    checks: dict[str, str]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    """The inner ``error`` object of the single error envelope (contract §6)."""

    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    """The one error shape returned by every failure, everywhere (contract §6)."""

    error: ErrorBody
