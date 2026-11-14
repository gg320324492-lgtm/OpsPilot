"""Single-run evidence export (Phase 3 M2-06).

Responsibility: serve ``GET /api/runs/{run_id}/export`` -- one run's redacted
summary as JSON, for reproducing an incident, briefing a colleague, or
attaching the run to a case study. Read-only; it introduces no new permission
model and rides the same ``require_operator`` dependency every ``/api`` route
declares.

Layer: ``api`` (router).

**Why a separate module rather than another method on ``runs.py``.** The export
is a *projection over four aggregates* -- run, tool calls, approvals, audit
events -- while every other runs route reads the run alone. Adding four loaders
and a second payload model to an already large router buys nothing but merge
surface, and this task shares the file with a parallel one. The router is
registered by ``app.py`` beside the others; nothing else changes.

**What is withheld, and how a reader knows.** The milestone names three
withholdings: credentials, default-hidden body text, and full provider error
bodies. The export enforces each at a different boundary:

- **Body text** is structurally absent: step payloads (which carry the
  verbatim model reply and, under ``STORE_FULL_PROMPTS=true``, prompts) are
  replaced by the step's shape and timing only, and the customer reply body is
  not read at all.
- **Full provider error bodies** were never written: the ledger stores
  ``sanitized_error_summary``'s truncated, credential-screened one-liner, so
  the export's ``failure_diagnostic`` can only project what exists.
- **Credentials** are screened twice: once at the write boundary (the string
  screen) and once here, where every free-form object passes through
  :func:`opspilot.api.schemas.redact_sensitive` before it is serialised -- so
  a payload key added later cannot smuggle a credential past the writer.

``redaction.withheld`` names all of this in the payload itself: an evidence
file that silently omits fields reads as "this never happened"; one that names
its withholdings reads as "this was screened".

Every list the export reads is reached through an optional-method probe, the
same convention ``runs.py`` uses, so a store that does not offer a read
degrades to an empty section rather than a 500: a missing section is a smaller
failure than a report the operator cannot open.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends

from opspilot.api.auth import require_operator
from opspilot.api.errors import ApiError
from opspilot.api.routers.deps import ApprovalStoreDep, RunStoreDep, TicketStoreDep
from opspilot.api.routers.runs import _claim_lease
from opspilot.api.schemas import (
    ExportApproval,
    ExportAuditEvent,
    ExportStep,
    ExportToolCall,
    FailureDiagnostic,
    RunExport,
    RunSummary,
    redact_sensitive,
)
from opspilot.ports.stores import StepRow, ToolCallRow

router = APIRouter(prefix="/api", tags=["runs"], dependencies=[Depends(require_operator)])


class ApprovalRow(Protocol):
    """The fields the export reads off an approval, whatever object carries them.

    Satisfied by the domain ``ApprovalRequest`` and by any read model with the
    same columns; ``status`` is duck-typed because the export only serialises
    it.
    """

    id: UUID
    tool_call_id: UUID
    status: Any
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object]
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    note: str | None


class AuditRow(Protocol):
    """The fields the export reads off an audit event."""

    id: UUID
    event_type: str
    created_at: datetime
    actor: str
    payload: dict[str, object]


def _optional_method(store: object, name: str) -> Any:  # noqa: ANN401 -- duck-typed port extension
    """Fetch an optional method off a store, or ``None`` if it is not offered."""
    return getattr(store, name, None)


def _failure_diagnostic(events: list[AuditRow]) -> FailureDiagnostic | None:
    """Project the run's ``model_failed`` ledger events into the M1-03 fields.

    The last event wins: a run that failed twice carries both, and the most
    recent diagnosis is the one that explains the terminal state. The payload
    is the writer's -- provider, model, stage, category, latency, attempt,
    HTTP status, request id, and the already-sanitised summary -- so the
    projection is a read, not a re-derivation. A payload missing a field
    exports the fields it has; an event that is not a diagnosis is skipped.
    """
    for event in reversed(events):
        if event.event_type != "model_failed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        provider = payload.get("provider")
        model = payload.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            continue
        stage = payload.get("stage")
        category = payload.get("error_category")
        summary = payload.get("error_summary")
        latency = payload.get("latency_ms")
        attempt = payload.get("attempt")
        http_status = payload.get("http_status")
        request_id = payload.get("request_id")
        return FailureDiagnostic(
            provider=provider,
            model=model,
            stage=stage if isinstance(stage, str) else "unknown",
            error_category=category if isinstance(category, str) else "unknown",
            error_summary=summary if isinstance(summary, str) else "",
            latency_ms=latency if isinstance(latency, int) else None,
            attempt=attempt if isinstance(attempt, int) else None,
            http_status=http_status if isinstance(http_status, int) else None,
            request_id=request_id if isinstance(request_id, str) else None,
        )
    return None


@router.get("/runs/{run_id}/export", response_model=RunExport)
async def export_run(
    run_id: UUID, runs: RunStoreDep, tickets: TicketStoreDep, approvals: ApprovalStoreDep
) -> RunExport:
    """One run's redacted evidence export (contract §4.2).

    Raises:
        ApiError: 404 ``run_not_found`` if there is no such run -- an empty
            export would say "this run produced nothing" about a run that
            never existed, which is the plausible-lie failure this project
            keeps having to name.
    """
    run = await runs.get(run_id)
    if run is None:
        raise ApiError(
            404,
            "run_not_found",
            f"Run {run_id} does not exist.",
            {"run_id": str(run_id)},
        )

    steps: list[StepRow] = []
    step_loader = _optional_method(runs, "list_steps")
    if step_loader is not None:
        steps = list(await step_loader(run_id))
    steps.sort(key=lambda row: row.sequence)

    tool_calls: list[ToolCallRow] = []
    call_loader = _optional_method(runs, "list_tool_calls")
    if call_loader is not None:
        tool_calls = list(await call_loader(run_id))
    tool_calls.sort(key=lambda row: str(row.id))

    # The run's approvals. The store's ``get_for_run`` answers "the most recent
    # one", which is the one an export names; ``list_approvals_for_run`` (when
    # offered) replaces it with *every* approval the run produced, because a
    # resumed run re-parks on a new call and an evidence file that hides the
    # second decision is half a record.
    approval_rows: list[ApprovalRow] = []
    list_for_run = _optional_method(approvals, "list_approvals_for_run")
    if list_for_run is not None:
        approval_rows = list(await list_for_run(run_id))
    else:
        get_for_run = _optional_method(approvals, "get_for_run")
        if get_for_run is not None:
            found: ApprovalRow | None = await get_for_run(run_id)
            if found is not None:
                approval_rows = [found]
    approval_rows.sort(key=lambda row: (row.created_at, str(row.id)))

    audit_rows: list[AuditRow] = []
    audit_loader = _optional_method(runs, "list_audit_events")
    if audit_loader is not None:
        audit_rows = list(await audit_loader(run_id))

    ticket_subject: str | None = None
    ticket = await tickets.get(run.ticket_id)
    if ticket is not None:
        ticket_subject = ticket.subject

    return RunExport(
        run=RunSummary(
            id=run.id,
            ticket_id=run.ticket_id,
            status=run.status,
            model_provider=run.model_provider,
            model_name=run.model_name,
            started_at=run.started_at,
            completed_at=run.completed_at,
            failure_reason=run.failure_reason,
            created_at=run.created_at,
        ),
        ticket_subject=ticket_subject,
        claim_lease=_claim_lease(run),
        steps=[
            ExportStep(
                sequence=row.sequence,
                step_type=row.step_type,
                latency_ms=row.latency_ms,
                started_at=row.started_at,
            )
            for row in steps
        ],
        tool_calls=[
            ExportToolCall(
                id=row.id,
                tool_name=row.tool_name,
                arguments=cast(dict[str, object], redact_sensitive(row.arguments)),
                permission=row.permission,
                status=row.status,
                latency_ms=row.latency_ms,
                idempotency_key=row.idempotency_key,
                error=row.error,
            )
            for row in tool_calls
        ],
        approvals=[
            ExportApproval(
                id=row.id,
                tool_call_id=row.tool_call_id,
                status=row.status,
                reason=row.reason,
                risk_explanation=row.risk_explanation,
                arguments_snapshot=cast(
                    dict[str, object], redact_sensitive(row.arguments_snapshot)
                ),
                created_at=row.created_at,
                decided_at=row.decided_at,
                decided_by=row.decided_by,
                note=row.note,
            )
            for row in approval_rows
        ],
        audit_events=[
            ExportAuditEvent(
                id=row.id,
                event_type=row.event_type,
                created_at=row.created_at,
                actor=row.actor,
                payload=cast(dict[str, object], redact_sensitive(row.payload)),
            )
            for row in audit_rows
        ],
        failure_diagnostic=_failure_diagnostic(audit_rows),
        # Aware UTC, always: the export is a record that outlives the process
        # and its timezone, and a naive stamp would be ambiguous on read.
        generated_at=datetime.now(UTC),
    )
