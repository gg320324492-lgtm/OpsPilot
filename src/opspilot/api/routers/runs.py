"""Run routes.

Responsibility: read a run and its trace -- status, steps, tool calls, and the
citations that support its answer -- plus the one *human follow-up* write:
re-creating a terminal run (Phase 3 M2-03). Runs are otherwise created through
the ticket endpoint and advanced by the worker, never driven by an HTTP request
(which is what keeps the agent loop out of the request path).

Layer: ``api`` (router).

A run parked in ``WAITING_APPROVAL`` is a normal, non-terminal state to return,
not an error.

Reads that the ``RunStore`` port does not name -- listing runs, the citation
join, the steps of a run, the audit ledger -- are reached through optional
methods when the concrete store offers them, so this router stays bound to the
port while still serving the dashboard's payload. Where a store does not offer
one, the field is an empty list rather than a 500: a missing citations panel is
a smaller failure than a run-detail screen that will not render.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opspilot.api.auth import require_operator
from opspilot.api.errors import ApiError
from opspilot.api.routers.deps import RunStoreDep, TicketStoreDep
from opspilot.api.schemas import (
    AuditEventDetail,
    AuditResponse,
    CitationDetail,
    ClaimLease,
    CustomerReply,
    OriginalWriteStatus,
    PendingApproval,
    RunCreateRequest,
    RunDetail,
    RunListResponse,
    RunRecreateResponse,
    RunRef,
    RunSummary,
    StepDetail,
    ToolCallDetail,
    TraceResponse,
    TraceStep,
)
from opspilot.domain.runs import TERMINAL, AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.ports.stores import CustomerReplyRow, PendingApprovalRow
from opspilot.settings import Settings, get_settings

# Re-exported for the tests that assert the write-status rule against the real
# store: the rule lives here (it is the router's classification, contract §3.3),
# and ``no_implicit_reexport`` makes the re-export explicit rather than silent.
__all__ = [
    "OriginalWriteStatus",
    "router",
]

router = APIRouter(prefix="/api", tags=["runs"], dependencies=[Depends(require_operator)])

#: The one tool whose execution the re-create response reports on. It is the
#: only tool in the registry that moves money, so "did the original run write?"
#: means "did a refund execute?" -- a refused read does not make the answer
#: ``unknown``.
REFUND_TOOL: Final[str] = "billing.issue_refund"

#: The audit event written when a run is re-created (contract §4.1). It is
#: written against the **new** run: the original run's ledger is a record of
#: what that run did, and this event records what *this* action did.
AUDIT_RUN_RECREATED: Final[str] = "run_recreated"

#: The actor the re-create audit event names. The route requires the operator
#: token, so a human is acting; ``operator`` is the API's name for that side of
#: the ledger (the approvals decision path records the *identity* instead, which
#: Phase 1 has exactly one of -- ``docs/api-contract.md`` §5, ``limitations.md``
#: §1).
ACTOR_OPERATOR: Final[str] = "operator"


class StepRow(Protocol):
    """The shape the router needs from a persisted ``agent_steps`` row.

    A local Protocol rather than a concrete model: it names only the fields the
    trace renders, so the persistence adapter (written in parallel) can satisfy it
    with any object carrying these attributes.
    """

    sequence: int
    step_type: str
    output: dict[str, object] | None
    latency_ms: int | None
    started_at: datetime


class ToolCallRow(Protocol):
    """The shape the router needs from a persisted ``tool_calls`` row."""

    id: UUID
    tool_name: str
    arguments: dict[str, object]
    permission: Permission
    status: ToolCallStatus
    result: dict[str, object] | None
    latency_ms: int | None
    idempotency_key: str | None
    error: str | None


class CitationRow(Protocol):
    """The shape the router needs from a persisted ``citations`` row."""

    document: str
    chunk: str
    score: float
    rank: int


class AuditEventRow(Protocol):
    """The shape the router needs from a persisted ``audit_events`` row.

    The ledger's columns, and nothing else: an ``AuditEvent`` is written once and
    never rewritten (``tracing/recorder.py``), so the projection is a copy of the
    row rather than a derived view of it.
    """

    id: UUID
    event_type: str
    created_at: datetime
    actor: str
    payload: dict[str, object]


def _settings() -> Settings:
    """The process settings, as a FastAPI dependency (patchable in tests)."""
    return get_settings()


def _optional_method(store: object, name: str) -> Any:  # noqa: ANN401 -- duck-typed port extension
    """Fetch an optional method off a store, or ``None`` if it is not offered.

    The store ports name the methods the *worker* needs. The dashboard needs a
    few more (a list, a join). Rather than widen the port -- which the persistence
    agent owns -- this router probes for them, so an in-memory fake in a test and
    the SQL adapter in production can each provide exactly what they have.
    """
    return getattr(store, name, None)


def _run_summary(run: AgentRun) -> RunSummary:
    """Project a domain ``AgentRun`` to its list/detail summary."""
    return RunSummary(
        id=run.id,
        ticket_id=run.ticket_id,
        status=run.status,
        model_provider=run.model_provider,
        model_name=run.model_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
    )


def _claim_lease(run: AgentRun) -> ClaimLease:
    """The run's claim lease, with the held/expired decision made here.

    ``claim_state`` reads the clock, so this is the one place in the request
    where "is a worker holding this right now" is decided. The projection is
    unconditional -- a run with no lease gets ``unclaimed`` rather than ``null``
    -- because the dashboard's branch is "which of the three states is this",
    and a nullable object makes "the field the API did not send" the fourth
    answer, which is the same empty-panel defect the optional reads below are
    written against.
    """
    return ClaimLease(
        state=run.claim_state(),
        claimed_by=run.claimed_by,
        claimed_until=run.claimed_until,
    )


@router.post("/runs", status_code=201, response_model=RunRef)
async def create_run(
    request: RunCreateRequest,
    runs: RunStoreDep,
    tickets: TicketStoreDep,
    settings: Settings = Depends(_settings),  # noqa: B008 -- FastAPI dependency marker
) -> RunRef:
    """Enqueue a new run for an existing ticket (contract §9).

    Re-running an investigation is a legitimate action, so this always creates a
    new run rather than deduplicating.

    Raises:
        ApiError: 404 ``ticket_not_found`` if the ticket does not exist.
    """
    getter = _optional_method(tickets, "get")
    ticket = await getter(request.ticket_id) if getter is not None else None
    if ticket is None:
        raise ApiError(
            404,
            "ticket_not_found",
            f"Ticket {request.ticket_id} does not exist.",
            {"ticket_id": str(request.ticket_id)},
        )
    model_name = settings.model_name or settings.model_provider
    run = await runs.create(
        ticket_id=request.ticket_id,
        model_provider=settings.model_provider,
        model_name=model_name,
    )
    return RunRef(id=run.id, status=run.status, created_at=run.created_at)


@router.post("/runs/{run_id}/recreate", status_code=201, response_model=RunRecreateResponse)
async def recreate_run(run_id: UUID, runs: RunStoreDep) -> RunRecreateResponse:
    """Create a new run for the original run's ticket -- the human follow-up path
    (Phase 3 M2-03, contract §3.2).

    An operator reads a finished run, decides its outcome needs another pass,
    and asks for a fresh run against the same ticket. The new run is a
    **sibling, not a continuation**: it starts in ``RECEIVED`` like any other
    run, and it inherits nothing from the original -- no approval, no executed
    refund, no trace. That is deliberate and it is the whole point: the
    follow-up exists because a human judged the original outcome insufficient,
    so the judgement has to be re-made, not copied.

    The response states what the original run actually wrote, as
    ``original_write_status``. The three values are ``executed`` (a refund
    row on the original run is recorded ``executed``), ``not_executed`` (no
    refund row at all -- every proposal was refused at a gate, or none was
    made) and ``unknown`` (refund rows exist but none is ``executed`` -- the
    crash window of ``docs/risks.md`` C2 -- or the rows could not be read). The
    value is computed here, from the store, so every client gets the same
    answer; it is **advice**, not an authorisation: a new run must pass every
    gate and win a fresh approval before anything moves, exactly like the
    original did.

    The idempotency key a new run derives is a function of *its* run id
    (``domain/policies.py``), so the server's replay guard does not reach
    across runs -- which is why the cross-run boundary is guarded here instead:
    only a terminal run can be re-created, a parked run's approval stays where
    it is, and the state machine's single ``RECEIVED`` entry point means a
    re-created run cannot skip the gates. The end-to-end cross-run property
    lives in ``tests/agent/test_recreate_run.py``, and this handler's own
    contract -- statuses, error codes, the write-status rule -- lives in
    ``tests/integration/test_api_run_recreate.py``.

    Writes one ``run_recreated`` audit event against the **new** run's ledger:
    the original's ledger records what that run did, and this event records
    what this action did -- who asked, and from which original.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run; 409
            ``run_not_recreatable`` if the run is not terminal -- a parked,
            in-flight or queued run still has a live path, and re-creating
            under it would risk two agents on one ticket.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)
    if run.status not in TERMINAL:
        raise ApiError(
            409,
            "run_not_recreatable",
            f"Run {run_id} is {run.status.value}, not a finished run. "
            "Only a completed or failed run can be re-created.",
            {"run_id": str(run_id), "status": run.status.value},
        )

    write_status = await _original_write_status(runs, run)
    # The settings come from the same source the sibling ``create_run`` uses, so
    # the new run's model matches whatever the deployment is configured for --
    # a re-created run is a *new* run, not a replay of the original's model.
    settings = _settings()
    model_name = settings.model_name or settings.model_provider
    new_run = await runs.create(
        ticket_id=run.ticket_id,
        model_provider=settings.model_provider,
        model_name=model_name,
    )
    await _record_recreated_event(runs, original=run, new_run=new_run)
    return RunRecreateResponse(
        original_run_id=run.id,
        original_write_status=write_status,
        run=RunRef(id=new_run.id, status=new_run.status, created_at=new_run.created_at),
    )


async def _original_write_status(runs: object, run: AgentRun) -> OriginalWriteStatus:
    """Whether the original run's refund actually executed, as three values.

    The rows are read through the same optional ``list_tool_calls`` method the
    detail payload uses, so a store that cannot serve them yields ``unknown`` --
    the honest answer when the evidence is unavailable, and the one that tells
    the operator to check rather than the one that tells them to proceed.

    The rule, per row status:

    - any ``executed`` row -> ``executed``: the money moved and the row says so;
    - a dangling ``proposed`` row -> ``unknown``: the run died between the
      proposal and its gate outcome, or in the C2 window -- dispatched, then
      lost before the row committed (``docs/risks.md`` C2). The row cannot say
      which, so the answer cannot either;
    - everything else -- ``rejected`` (refused at a gate, never dispatched),
      ``awaiting_approval`` (parked, never decided, never dispatched),
      ``failed`` (dispatched and *refused by the server*, which refuses
      atomically: ``invalid_state``, ``amount_exceeds_transaction``) -- moved
      nothing, and with no ``executed`` row anywhere the answer is
      ``not_executed``.
    """
    try:
        rows: list[ToolCallDetail] = await _load_tool_calls(runs, run.id)
    except Exception:
        return OriginalWriteStatus.UNKNOWN
    refund_rows = [row for row in rows if row.tool_name == REFUND_TOOL]
    if not refund_rows:
        # No refund was ever proposed. Nothing moved, and there is no C2 window
        # to worry about: a dispatched-and-lost refund leaves its ``proposed``
        # row behind, so "no rows" means "no proposal".
        return OriginalWriteStatus.NOT_EXECUTED
    if any(row.status is ToolCallStatus.EXECUTED for row in refund_rows):
        return OriginalWriteStatus.EXECUTED
    if any(row.status is ToolCallStatus.PROPOSED for row in refund_rows):
        return OriginalWriteStatus.UNKNOWN
    return OriginalWriteStatus.NOT_EXECUTED


async def _record_recreated_event(runs: object, *, original: AgentRun, new_run: AgentRun) -> None:
    """Write the one ``run_recreated`` audit event against the new run.

    The event is best-effort by construction: the re-creation itself has
    already committed when this runs, and a store that offers no audit writer
    (the in-memory test fakes) loses the event, not the run. What it must never
    be is *silent* in the payload -- ``original_run_id`` is on every event, so
    a reader can always answer "this new run came from where?".
    """
    recorder_factory = _optional_method(runs, "audit_recorder_for")
    if recorder_factory is None:
        return
    recorder = recorder_factory(new_run.id)
    await recorder.record_audit(
        event_type=AUDIT_RUN_RECREATED,
        actor=ACTOR_OPERATOR,
        payload={"original_run_id": str(original.id)},
    )


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    runs: RunStoreDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    """List runs, optionally filtered by status (contract §1, §10).

    Raises:
        ApiError: 400 ``validation_error`` if ``status`` is not a ``RunStatus``.
    """
    parsed: RunStatus | None = None
    if status is not None:
        try:
            parsed = RunStatus(status)
        except ValueError as exc:
            raise ApiError(
                400,
                "validation_error",
                f"{status!r} is not a valid run status.",
                {"status": status, "allowed": [s.value for s in RunStatus]},
            ) from exc
    lister = _optional_method(runs, "list")
    if lister is None:
        return RunListResponse(items=[], total=0)
    rows = await lister(status=parsed, limit=limit, offset=offset)
    # ``total`` counts every run matching the filter, not the ones on this
    # page. It used to be ``len(rows)``, which made a paginated list report a
    # total that did not count the rows it did not return -- and the dashboard
    # read it as "how many runs there are".
    #
    # The count is a *required* port method, so there is no fallback to the
    # page length: a store that cannot count is a store that does not satisfy
    # the contract this endpoint publishes (contract §10).
    total = await runs.count(status=parsed)
    return RunListResponse(items=[_run_summary(run) for run in rows], total=total)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: UUID, runs: RunStoreDep) -> RunDetail:
    """The run-detail payload the dashboard renders (contract §3).

    ``pending_approval`` is populated only when the run is parked in
    ``WAITING_APPROVAL``; ``customer_reply`` only from a completed run's response
    step. Both are fetched through optional store methods so a store that does
    not offer them still serves the run.

    ``failed_tool_calls`` is the summary an operator needs and the ``tool_calls``
    list does not give them: a run can be ``completed``, carry a customer reply
    and five citations, and still have had a refund refused somewhere in the
    middle of the trace. Filtering here rather than in the client means the
    dashboard cannot forget to.

    ``claim_lease`` is the one field that separates a run nothing has claimed
    from a run a worker holds and has not started writing steps for. The claim
    takes a row without changing its status, so both render as ``received`` with
    no steps -- measured for the full 300 s of ``WORKER_CLAIM_LEASE_SECONDS``
    against a healthy worker, and recorded in ``docs/limitations.md`` §2. The run
    *status* is deliberately not extended to carry it: the state machine is a
    specification (``docs/agent-state-machine.md`` §2) and this is not one of its
    states.

    ``original_write_status`` (Phase 3 M2-03) is populated only for a *terminal*
    run -- failed or completed -- by the same rule ``POST
    /api/runs/{id}/recreate`` reports (§3.3). A finished run is exactly the
    state the re-create button shows for, so the screen that offers the button
    is the one that states what the original wrote; a run still in flight
    carries ``null``, because "did the write execute" is not yet a settled fact
    about it.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)

    steps = await _load_steps(runs, run_id)
    tool_calls = await _load_tool_calls(runs, run_id)
    citations = await _load_citations(runs, run_id)
    pending = await _load_pending_approval(runs, run)
    reply = await _load_customer_reply(runs, run)

    return RunDetail(
        id=run.id,
        ticket_id=run.ticket_id,
        status=run.status,
        model_provider=run.model_provider,
        model_name=run.model_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        claim_lease=_claim_lease(run),
        steps=steps,
        tool_calls=tool_calls,
        citations=citations,
        pending_approval=pending,
        customer_reply=reply,
        failed_tool_calls=[tc for tc in tool_calls if tc.status is ToolCallStatus.FAILED],
        original_write_status=(
            await _original_write_status(runs, run) if run.status in TERMINAL else None
        ),
    )


@router.get("/runs/{run_id}/trace", response_model=TraceResponse)
async def get_run_trace(run_id: UUID, runs: RunStoreDep) -> TraceResponse:
    """The ordered timeline only, without tool arguments or model payloads.

    Each step is projected to a human-readable ``label`` and a short ``detail``
    server-side, so the client renders without deciding what a step means.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)
    loader = _optional_method(runs, "list_steps")
    rows = await loader(run_id) if loader is not None else []
    timeline = [_to_trace_step(row) for row in rows]
    timeline.sort(key=lambda step: step.sequence)
    return TraceResponse(run_id=run_id, steps=timeline)


@router.get("/runs/{run_id}/audit", response_model=AuditResponse)
async def get_run_audit(run_id: UUID, runs: RunStoreDep) -> AuditResponse:
    """The run's audit ledger, oldest event first (contract §4.1).

    The ledger, not the trace, and the two answer different questions.
    ``/trace`` is the *execution timeline* -- what step ran, in what order, how
    long it took. ``/audit`` is the *compliance record* -- which events were
    appended, by whom, with what payload. A step says the tool was called; the
    ledger says the run was authorised to call it and what came back. Only the
    ledger is append-only, and only the ledger is what an auditor reads.

    Events are returned in the order they were written, unfiltered and
    un-summarised. An operator looking for "did this run lose money somewhere"
    reads ``failed_tool_calls`` on ``GET /api/runs/{id}``; an operator looking
    for *everything that was recorded about this run*, including the events no
    other surface shows, reads this.

    A run with no events is an empty list, not an error. A run id that does not
    exist is the 404 ``run_not_found`` its sibling endpoints raise -- an empty
    list there would say "this run's ledger is empty" about a run that was never
    created, which is a different and more dangerous statement.

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run.
    """
    run = await runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)
    events = await _load_audit_events(runs, run_id)
    return AuditResponse(run_id=run_id, events=events)


# -- loaders -----------------------------------------------------------------


async def _load_steps(store: object, run_id: UUID) -> list[StepDetail]:
    """Load a run's steps as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_steps")
    if loader is None:
        return []
    rows: list[StepRow] = await loader(run_id)
    return [
        StepDetail(
            sequence=row.sequence,
            step_type=row.step_type,
            output=row.output,
            latency_ms=row.latency_ms,
            started_at=row.started_at,
        )
        for row in rows
    ]


async def _load_tool_calls(store: object, run_id: UUID) -> list[ToolCallDetail]:
    """Load a run's tool calls as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_tool_calls")
    if loader is None:
        return []
    rows: list[ToolCallRow] = await loader(run_id)
    return [
        ToolCallDetail(
            id=row.id,
            tool_name=row.tool_name,
            arguments=row.arguments,
            permission=row.permission,
            status=row.status,
            result=row.result,
            latency_ms=row.latency_ms,
            idempotency_key=row.idempotency_key,
            error=row.error,
        )
        for row in rows
    ]


async def _load_citations(store: object, run_id: UUID) -> list[CitationDetail]:
    """Load a run's citations as detail rows, or an empty list if unsupported."""
    loader = _optional_method(store, "list_citations")
    if loader is None:
        return []
    rows: list[CitationRow] = await loader(run_id)
    return [
        CitationDetail(
            document=row.document,
            chunk=row.chunk,
            score=float(row.score),
            rank=int(row.rank),
        )
        for row in rows
    ]


async def _load_audit_events(store: object, run_id: UUID) -> list[AuditEventDetail]:
    """Load a run's audit events, oldest first, or an empty list if unsupported.

    Ordered by the store rather than re-sorted here: the store is the object that
    knows the ledger's total order (``created_at`` then ``id``, so two events
    sharing a timestamp do not swap between two reads). Re-sorting in the router
    on ``created_at`` alone would throw that away and make the page's order
    depend on the clock's resolution.

    No projection and no filtering. The trace assembles labels server-side
    because a step's meaning is a *presentation* concern; an audit event's
    meaning is a *record* concern, and a router that interpreted one would be
    editing the ledger's contents on the way out.
    """
    loader = _optional_method(store, "list_audit_events")
    if loader is None:
        return []
    rows: list[AuditEventRow] = await loader(run_id)
    return [
        AuditEventDetail(
            id=row.id,
            event_type=row.event_type,
            created_at=row.created_at,
            actor=row.actor,
            payload=dict(row.payload),
        )
        for row in rows
    ]


async def _load_pending_approval(store: object, run: AgentRun) -> PendingApproval | None:
    """Load the nested pending-approval payload when, and only when, parked.

    The store returns its own read model, not this module's schema, so the
    projection happens here. The two are separate types on purpose: the store
    row is a storage-boundary read model and the schema is the wire contract,
    and a run detail page whose approver card is `null` because the two happened
    to have different class names is exactly the kind of empty panel this
    milestone exists to remove.

    Only *one* approval is nested, even when a resumed run has re-proposed and
    re-parked. That is a deliberate limit of contract §3, not an oversight: the
    Approvals screen is where the duplicate is shown, with both cards, because
    hiding the second here would leave an operator deciding on one while the
    other stays pending.
    """
    if run.status is not RunStatus.WAITING_APPROVAL:
        return None
    loader = _optional_method(store, "get_pending_approval")
    if loader is None:
        return None
    row: PendingApprovalRow | None = await loader(run.id)
    if row is None:
        return None
    return PendingApproval(
        id=row.id,
        tool_call_id=row.tool_call_id,
        status=row.status,
        reason=row.reason,
        risk_explanation=row.risk_explanation,
        arguments_snapshot=row.arguments_snapshot,
        created_at=row.created_at,
    )


async def _load_customer_reply(store: object, run: AgentRun) -> CustomerReply | None:
    """Load the customer reply, which is set only at ``COMPLETED``.

    The store's row carries ``escalated`` alongside the body, and ``escalated``
    is a **declared** field on ``CustomerReply``, so the flag reaches the
    client rather than being dropped at this boundary. (It is the declaration
    that carries it, not the model's ``extra="allow"``: a declared field is
    serialised whatever the extra-field policy is. ``extra="allow"`` is what
    puts an index signature in the OpenAPI type, and it is what
    ``api/schemas.py`` says it is for -- a field that exists only at runtime.
    ``escalated`` is not one of those.) It matters to a reader: "we could not
    complete your request, a human is on it" and "here is your refund" are the
    same sentence shape with completely different meanings, and the flag is the
    only thing on screen that tells them apart.
    """
    if run.status is not RunStatus.COMPLETED:
        return None
    loader = _optional_method(store, "get_customer_reply")
    if loader is None:
        return None
    row: CustomerReplyRow | None = await loader(run.id)
    if row is None:
        return None
    return CustomerReply(body=row.body, escalated=row.escalated)


def _run_not_found(run_id: UUID) -> ApiError:
    """Build the contract's 404 ``run_not_found`` for a missing run."""
    return ApiError(
        404,
        "run_not_found",
        f"Run {run_id} does not exist.",
        {"run_id": str(run_id)},
    )


def _to_trace_step(row: StepRow) -> TraceStep:
    """Project a step row into the trace shape, assembling the label server-side."""
    step_type = row.step_type
    output = row.output
    return TraceStep(
        sequence=row.sequence,
        step_type=step_type,
        label=_label_for(step_type),
        detail=_detail_for(step_type, output),
        latency_ms=row.latency_ms,
        at=row.started_at,
    )


_LABELS: dict[str, str] = {
    "state_change": "State changed",
    "classification": "Ticket classified",
    "retrieval": "Policy retrieved",
    "planning": "Next action proposed",
    "response": "Reply composed",
}


def _label_for(step_type: str) -> str:
    """The human-readable label for a step type."""
    return _LABELS.get(step_type, step_type.replace("_", " ").capitalize() or "Step")


def _detail_for(step_type: str, output: object) -> str:
    """A short summary string for a step, derived from its output.

    Every key read here is asserted against a real recorded step by
    ``tests/agent/test_trace_labels_are_read_from_real_steps.py``, which drives
    the golden path, reads the ``AgentStep`` rows back and requires the detail
    for each real step type to be non-empty. Both of the keys this used to read
    -- ``"to"`` for a state change, and a whole ``tool_call`` branch -- were
    wrong: every writer emits ``to_status``, and no code path in ``src/``
    records a ``tool_call`` step at all. Dead branches are worse than missing
    ones, because a missing branch fails loudly when the event finally arrives
    and a dead one silently renders ``to None``.
    """
    if not isinstance(output, dict):
        return output if isinstance(output, str) else ""
    if step_type == "state_change":
        to = output.get("to_status")
        return f"to {to}" if to else ""
    if step_type == "classification":
        category = output.get("category", "")
        confidence = output.get("confidence")
        return f"{category} ({confidence})" if confidence is not None else str(category)
    if step_type == "planning":
        # The proposal the model made, which is the step a reader most wants to
        # see: it names the action before the trace records whether it worked.
        tool = str(output.get("tool_name", "") or "")
        if output.get("done"):
            return "no further action needed" if not tool else f"{tool} (done)"
        return tool
    if step_type == "retrieval":
        return f"{output.get('count', '')} chunks".strip()
    if step_type == "response":
        chars = output.get("chars")
        escalated = bool(output.get("escalated"))
        length = f"{chars} chars" if isinstance(chars, int) else ""
        if escalated:
            return "escalated reply, " + length if length else "escalated reply"
        return length
    return ""
