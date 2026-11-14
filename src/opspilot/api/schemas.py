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
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.runs import ClaimLeaseState, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus


def redact_sensitive(value: object, _depth: int = 0) -> object:
    """Screen a free-form JSON value for credential-shaped content.

    The export re-uses :func:`~opspilot.agents.runtime.sanitized_error_summary`'s
    fragment screen at the *key* level: a key whose name matches a credential
    pattern has its value replaced, in place, recursively. This is the
    structural half of the writer's string-level screen -- the writer screens
    what an exception *says*, this screens what a payload *names*.

    Returns a new structure; the input is never mutated (the caller may hold
    the original row).
    """
    if _depth > 12:  # a run's payloads are shallow; the cap is a cycle seatbelt
        return "[redacted: structure too deep]"
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
                out[str(key)] = "[redacted: key matched a credential pattern]"
            else:
                out[str(key)] = redact_sensitive(item, _depth + 1)
        return out
    if isinstance(value, list):
        return [redact_sensitive(item, _depth + 1) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_VALUE_FRAGMENTS):
            return "[redacted: value matched a credential pattern]"
        return value
    return value


#: Key-name fragments that mark a payload entry as carrying (or naming) a
#: credential. Same vocabulary and same intent as the runtime's
#: ``_CREDENTIAL_FRAGMENTS``; kept as a separate tuple because this screen
#: matches against keys, which are identifiers, not prose.
_SENSITIVE_KEY_FRAGMENTS: Final = (
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "x-api-key",
)

#: Value fragments that mark a *string* as a credential. ``sk-`` is the only
#: provider-key shape carried here: a bare value that embeds it is a key
#: whether or not its key name said so.
_SENSITIVE_VALUE_FRAGMENTS: Final = ("sk-",)

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
    """A run reference embedded in another payload.

    Three fields -- ``id``, ``status`` and ``created_at`` -- and nothing else.
    It is a *reference*, and it is embedded in three places: the ticket-create
    response's ``run`` (contract §2), the ``runs`` list of a ticket (§2.2), and
    the ``run`` an approval decision returns (§5).

    It is not the summary of §3.1 (which carries the model provider and the
    rest) and not the detail of §3 (which carries the steps, the tool calls and
    the citations). A client that needs those follows the ``id``.
    """

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


class ClaimLease(BaseModel):
    """Who is holding this run, and until when (contract §3).

    ``state`` is computed **server-side** rather than left to the client to
    derive from ``claimed_until`` against its own clock. That is the same rule
    the timeline and ``failed_tool_calls`` follow (``docs/api-contract.md`` §4):
    a decision made once, on the server, is the same decision in the README
    screenshot, in the dashboard and in anybody else's client. A client that
    compared the timestamps itself would have to re-implement the comparison
    before it could say the one sentence this field exists to make true.

    ``claimed_by`` and ``claimed_until`` ride along because the operator needs
    the bound, not just the verdict: "held until 14:32" is what turns a stuck run
    from an open-ended worry into a deadline.
    """

    state: ClaimLeaseState
    claimed_by: str | None = None
    claimed_until: datetime | None = None


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
    is set only at ``COMPLETED``. ``claim_lease`` is present on every run, because
    "no worker has claimed this" and "a worker holds this and has not written a
    step" are two different operational facts and the page has to be able to say
    which one it is looking at.
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
    claim_lease: ClaimLease = Field(
        default_factory=lambda: ClaimLease(state=ClaimLeaseState.UNCLAIMED)
    )
    steps: list[StepDetail] = Field(default_factory=list)
    tool_calls: list[ToolCallDetail] = Field(default_factory=list)
    citations: list[CitationDetail] = Field(default_factory=list)
    pending_approval: PendingApproval | None = None
    customer_reply: CustomerReply | None = None
    # The subset of ``tool_calls`` that did not succeed, repeated as a summary.
    #
    # The rows were already in ``tool_calls``; this changes nothing about what is
    # recorded and everything about what an operator can see. Observed live, a
    # run reported ``completed`` with five citations and a customer reply
    # claiming a refund, with the failed refund one status string deep in a list
    # a human has to know to scan. A summary field is what makes "this run lost
    # money somewhere" impossible to read past -- which is the only property that
    # matters when the same page also says "and here is your reply".
    failed_tool_calls: list[ToolCallDetail] = Field(default_factory=list)


class TraceResponse(BaseModel):
    """Body of ``GET /api/runs/{run_id}/trace`` (contract §4)."""

    run_id: UUID
    steps: list[TraceStep] = Field(default_factory=list)


class AuditEventDetail(BaseModel):
    """One ``audit_events`` row in the audit payload (contract §4.1).

    ``actor`` is who or what wrote the event. Every event the agent loop records
    today names ``runtime`` -- the constant is there so a writer that is *not* the
    runtime (an API-side action, a human decision) is distinguishable from it by
    reading one column rather than by knowing which log line to trust.
    ``payload`` is the event's own contents and its shape depends on
    ``event_type``: ``tool_failed`` carries ``{tool_call_id, tool_name,
    permission, ok, latency_ms, error}``, which is the one an operator greps for.

    Neither is interpreted here. A step's label is a *presentation* concern, so
    the trace assembles it server-side; an audit event's contents are a *record*
    concern, so this model transcribes them and explains nothing.
    """

    id: UUID
    event_type: str
    created_at: datetime
    actor: str
    payload: dict[str, object]


class AuditResponse(BaseModel):
    """Body of ``GET /api/runs/{run_id}/audit`` (contract §4.1).

    ``events`` is empty when the run has written none, which is a real answer
    ("nothing is on this ledger yet") rather than an error -- the run has to
    exist, which is the only 404 this endpoint returns.
    """

    run_id: UUID
    events: list[AuditEventDetail] = Field(default_factory=list)


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
    note: str | None = None
    context: ApprovalContext | None = None


class ApprovalListResponse(BaseModel):
    """Body of ``GET /api/approvals`` (contract §5, §10)."""

    items: list[ApprovalSummary]
    total: int


class ApprovalDecisionRequest(BaseModel):
    """Body of ``POST /api/approvals/{id}/approve`` and ``/reject``.

    The body may be empty. ``note`` is the operator's own remark on the
    decision, and it is persisted with it -- a remark that was accepted and
    then dropped was a remark the operator believed they had left.

    There is deliberately **no** ``decided_by`` field. The operator's identity
    is resolved from the bearer token by the server, and a request body that
    could name the operator would let a caller sign somebody else's name to a
    refund they approved themselves -- which is the one thing an audit column
    must not be. ``extra="forbid"`` means a client that sends one is told so,
    rather than having it quietly ignored.
    """

    model_config = ConfigDict(extra="forbid")

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
    note: str | None = None
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


class ExportStep(BaseModel):
    """One execution step in the export, without the step's recorded payload.

    ``agent_steps.output`` is deliberately **absent**: the step payloads carry
    the verbatim model reply (the ``response`` step's ``body`` -- the one
    string ``tracing/recorder.py`` stores verbatim) and the prompt text a
    deployment running ``STORE_FULL_PROMPTS=true`` keeps, both of which the
    milestone names as "默认隐藏的正文" the export must not carry. The step's
    shape, order and timing are what an evidence file needs.
    """

    sequence: int
    step_type: str
    latency_ms: int | None = None
    started_at: datetime


class ExportToolCall(BaseModel):
    """One tool call in the export, with its arguments credential-screened.

    ``arguments`` and ``result`` are passed through :func:`redact_sensitive`,
    because a tool's arguments are whatever the model proposed -- free-form
    JSON the policy gate never saw a credential pattern in. ``error`` is the
    short policy/gateway code (``invalid_state``, ``not_found``), not a
    provider error body, so it is carried as stored.
    """

    id: UUID
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    permission: Permission
    status: ToolCallStatus
    latency_ms: int | None = None
    idempotency_key: str | None = None
    error: str | None = None


class ExportApproval(BaseModel):
    """One approval row in the export, with its snapshot credential-screened.

    ``arguments_snapshot`` is what the human was shown and what executed, so
    it is evidence; it is screened for the same reason the tool call's live
    arguments are. The decision columns are carried in full -- who decided and
    when is exactly what an auditor reads the export for.
    """

    id: UUID
    tool_call_id: UUID
    status: ApprovalStatus
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    note: str | None = None


class ExportAuditEvent(BaseModel):
    """One audit-ledger row in the export.

    The payload is transcribed as stored -- the ledger is already the
    redacted surface (``sanitized_error_summary`` at the write boundary) --
    and then passed through :func:`redact_sensitive` once more so a payload
    key added later cannot smuggle a credential past the writer's own screen.
    """

    id: UUID
    event_type: str
    created_at: datetime
    actor: str
    payload: dict[str, object] = Field(default_factory=dict)


class FailureDiagnostic(BaseModel):
    """The M1-03 structured failure diagnosis, projected from ``model_failed``.

    These are the fields the milestone names: provider, model, stage, error
    category, latency, attempt, and the HTTP status / request id when the
    provider exposed them. ``error_summary`` is already the truncated,
    credential-screened one-liner the writer produced; the *full* provider
    error body was never written to the ledger, so it cannot reach the export
    either. ``latency_ms``, ``attempt``, ``http_status`` and ``request_id`` are
    optional because an event missing one (an older writer, a provider that
    exposes nothing) still exports the fields it has.
    """

    provider: str
    model: str
    stage: str
    error_category: str
    error_summary: str
    latency_ms: int | None = None
    attempt: int | None = None
    http_status: int | None = None
    request_id: str | None = None


class RunExport(BaseModel):
    """Body of ``GET /api/runs/{run_id}/export`` (contract §4.2).

    The single-run evidence export: the run's summary, its steps, tool calls,
    approvals, audit-ledger events and -- when the run failed a model call --
    the structured failure diagnosis. Every free-form object it carries has
    been through :func:`redact_sensitive`; the verbatim model reply and the
    stored prompts are not in it at all.

    ``redaction`` names the withholdings so a reader knows what is absent by
    design rather than by omission.
    """

    run: RunSummary
    ticket_subject: str | None = None
    claim_lease: ClaimLease = Field(
        default_factory=lambda: ClaimLease(state=ClaimLeaseState.UNCLAIMED)
    )
    steps: list[ExportStep] = Field(default_factory=list)
    tool_calls: list[ExportToolCall] = Field(default_factory=list)
    approvals: list[ExportApproval] = Field(default_factory=list)
    audit_events: list[ExportAuditEvent] = Field(default_factory=list)
    failure_diagnostic: FailureDiagnostic | None = None
    generated_at: datetime
    redaction: RedactionNotice = Field(default_factory=lambda: RedactionNotice())


class RedactionNotice(BaseModel):
    """What the export withholds, stated rather than implied.

    An evidence file that silently omits fields reads as "this never
    happened"; one that names its withholdings reads as "this was screened".
    The keys are the contract: a client can render them verbatim.
    """

    withheld: list[str] = Field(
        default_factory=lambda: [
            "step payloads (verbatim model reply and prompts)",
            "customer reply body",
            "full provider error bodies",
            "credentials matching a credential pattern",
        ]
    )


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
