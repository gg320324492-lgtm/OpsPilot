"""Approval routes.

Responsibility: list pending approvals and record a human decision. The approve
handler is an *edge-triggered* event: it flips the parked run back to a
claimable state, and the worker picks it up again -- approval is not a busy-wait.

Layer: ``api`` (router).

This is the human half of gate 5. The approver sees ``reason`` (the model's
untrusted justification) and ``risk_explanation`` (deterministic, from the
policy engine) side by side, labelled differently, plus the exact
``arguments_snapshot``. A decision binds to one ``tool_call_id``: approving a
refund for ``TX-88219`` never authorises a later proposal for another
transaction.

The two rules the tests pin, both from ``docs/api-contract.md`` §5:

- **Approving executes nothing.** It writes a decision and moves the run to
  ``EXECUTING``. Running the refund here would put a payment call inside an HTTP
  request with no trace if the response stream dropped.
- **A decision is a conditional update.** ``pending -> approved`` guarded by
  ``WHERE id = ? AND status = 'pending'``; a second click finds no row to update
  and gets 409 rather than double-granting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opspilot.api.auth import require_operator
from opspilot.api.errors import ApiError
from opspilot.api.routers.deps import ApprovalStoreDep, RunStoreDep
from opspilot.api.schemas import (
    ApprovalContext,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    ApprovalSummary,
    RunRef,
)
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.stores import ApprovalStore, RunStore

router = APIRouter(prefix="/api", tags=["approvals"], dependencies=[Depends(require_operator)])


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    approvals: ApprovalStoreDep,
    status: str = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApprovalListResponse:
    """List approvals, defaulting to the pending inbox (contract §5).

    Raises:
        ApiError: 400 ``validation_error`` if ``status`` is not an
            ``ApprovalStatus``.
    """
    try:
        parsed = ApprovalStatus(status)
    except ValueError as exc:
        raise ApiError(
            400,
            "validation_error",
            f"{status!r} is not a valid approval status.",
            {"status": status, "allowed": [s.value for s in ApprovalStatus]},
        ) from exc
    lister: Any = getattr(approvals, "list", None)
    rows: list[ApprovalRequest] = (
        await lister(status=parsed, limit=limit, offset=offset) if lister else []
    )
    items = [_summary(row) for row in rows]
    return ApprovalListResponse(items=items, total=len(items))


@router.get("/approvals/{approval_id}", response_model=ApprovalSummary)
async def get_approval(approval_id: UUID, approvals: ApprovalStoreDep) -> ApprovalSummary:
    """One approval, with the arguments as shown (contract §1).

    Raises:
        ApiError: 404 ``approval_not_found``.
    """
    approval = await _get_approval(approvals, approval_id)
    if approval is None:
        raise ApiError(
            404,
            "approval_not_found",
            f"Approval {approval_id} does not exist.",
            {"approval_id": str(approval_id)},
        )
    return _summary(approval)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve(
    approval_id: UUID,
    approvals: ApprovalStoreDep,
    runs: RunStoreDep,
    request: ApprovalDecisionRequest | None = None,
    operator: str = Depends(require_operator),
) -> ApprovalDecisionResponse:
    """Record approval and re-queue the run: ``WAITING_APPROVAL -> EXECUTING``.

    The run is flipped to a claimable state and the worker picks it up on its next
    poll. This handler does **not** execute the tool call.

    Args:
        approval_id: The approval to decide.
        approvals: The approval store port.
        runs: The run store port, used to read and move the parked run.
        request: Optional body carrying ``decided_by`` and a ``note``.
        operator: The authenticated operator id, the default ``decided_by``.

    Returns:
        The decided approval and the transitioned run.

    Raises:
        ApiError: 409 ``approval_already_decided`` if it was already decided;
            409 ``run_not_awaiting_approval`` if the run is not parked; 404 if
            the approval does not exist.
    """
    return await _decide(approval_id, approvals, runs, request, operator, approved=True)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject(
    approval_id: UUID,
    approvals: ApprovalStoreDep,
    runs: RunStoreDep,
    request: ApprovalDecisionRequest | None = None,
    operator: str = Depends(require_operator),
) -> ApprovalDecisionResponse:
    """Record rejection and re-queue the run for an escalation reply.

    The transition is ``WAITING_APPROVAL -> RESPONDING``, **not** ``FAILED``: a
    rejected refund is the workflow working, and the run goes on to produce an
    escalation reply (``docs/agent-state-machine.md`` §3).
    """
    return await _decide(approval_id, approvals, runs, request, operator, approved=False)


async def _decide(
    approval_id: UUID,
    approvals: ApprovalStore,
    runs: RunStore,
    request: ApprovalDecisionRequest | None,
    operator: str,
    *,
    approved: bool,
) -> ApprovalDecisionResponse:
    """Shared approve/reject path: decide, transition the run, return both.

    Args:
        approval_id: The approval to decide.
        approvals: The approval store port.
        runs: The run store port.
        request: Optional decision body.
        operator: The authenticated operator id.
        approved: ``True`` for approve, ``False`` for reject.

    Returns:
        The decided approval plus the run's new status.

    Raises:
        ApiError: 404, 409 ``approval_already_decided`` or
            409 ``run_not_awaiting_approval``.
    """
    body = request or ApprovalDecisionRequest()
    decided_by = body.decided_by or operator

    approval: ApprovalRequest | None = await _get_approval(approvals, approval_id)
    if approval is None:
        raise ApiError(
            404,
            "approval_not_found",
            f"Approval {approval_id} does not exist.",
            {"approval_id": str(approval_id)},
        )
    if approval.status is not ApprovalStatus.PENDING:
        raise _already_decided(approval)

    # The run must actually be parked before we touch the approval, so a stale
    # approval row cannot be used to move a run that has since gone elsewhere.
    run = await runs.get(approval.run_id)
    if run is None:
        raise ApiError(
            404,
            "run_not_found",
            f"Run {approval.run_id} does not exist.",
            {"run_id": str(approval.run_id)},
        )
    expected = RunStatus.WAITING_APPROVAL
    if run.status is not expected:
        raise ApiError(
            409,
            "run_not_awaiting_approval",
            (
                f"Run {run.id} is {run.status.value!r}, not 'waiting_approval'; "
                "it is not awaiting a decision."
            ),
            {"run_id": str(run.id), "status": run.status.value},
        )

    # The conditional update: the store performs `WHERE id = ? AND status =
    # 'pending'`. If another click won the race first, our update matches no row
    # and the store raises/returns "already decided" -- which we turn into a 409
    # rather than a double grant.
    try:
        decided = await approvals.decide(
            approval_id,
            approved=approved,
            decided_by=decided_by,
            note=body.note,
            decided_at=datetime.now(UTC),
        )
    except _AlreadyDecidedError as exc:
        raise _already_decided(exc.approval) from exc
    except LookupError as exc:
        # A store may signal the same condition with ``LookupError``; the port's
        # docstring leaves the "lost the race" signalling unspecified, so both are
        # accepted here and both mean 409.
        refreshed = await _get_approval(approvals, approval_id)
        raise _already_decided(refreshed or approval) from exc

    # The run transition follows the decision. ``decide`` also flips the run in
    # the store; this is the read-back that the response reports, and the status
    # it asserts is the one the state machine permits.
    target = RunStatus.EXECUTING if approved else RunStatus.RESPONDING
    transitioned = await _read_transitioned_run(runs, approval.run_id, target)

    return ApprovalDecisionResponse(
        id=decided.id,
        status=decided.status,
        decided_at=decided.decided_at or datetime.now(UTC),
        decided_by=decided.decided_by or decided_by,
        run=RunRef(
            id=transitioned.id,
            status=transitioned.status,
            created_at=transitioned.created_at,
        ),
    )


async def _get_approval(approvals: ApprovalStore, approval_id: UUID) -> ApprovalRequest | None:
    """Read one approval, preferring the store's richer ``get`` when it has one.

    The ``ApprovalStore`` port requires only ``get_for_tool_call``; the API needs a
    lookup by approval id. A store that offers ``get`` is used directly; otherwise
    this returns ``None`` and the caller reports 404, which is honest -- an id
    lookup genuinely is unavailable.
    """
    getter: Any = getattr(approvals, "get", None)
    if getter is None:
        return None
    result: ApprovalRequest | None = await getter(approval_id)
    return result


async def _read_transitioned_run(runs: RunStore, run_id: UUID, expected: RunStatus) -> AgentRun:
    """Read the run back after a decision, asserting the expected transition.

    The store owns the write; this read-back is what the response reports and what
    makes a store that forgot to move the run a loud failure (a mismatched status
    raises rather than being serialized as if it were fine).
    """
    run = await runs.get(run_id)
    if run is None:  # pragma: no cover - the run existed a moment ago
        raise ApiError(
            404,
            "run_not_found",
            f"Run {run_id} does not exist.",
            {"run_id": str(run_id)},
        )
    if run.status is not expected:
        raise ApiError(
            409,
            "run_already_completed",
            (
                f"Run {run_id} is {run.status.value!r}; expected "
                f"{expected.value!r} after the decision."
            ),
            {"run_id": str(run_id), "status": run.status.value, "expected": expected.value},
        )
    return run


class _AlreadyDecidedError(Exception):
    """Internal marker for a store that lost the conditional-update race."""

    def __init__(self, approval: ApprovalRequest) -> None:
        self.approval = approval
        super().__init__(f"approval {approval.id} is already {approval.status.value}")


def _already_decided(approval: ApprovalRequest) -> ApiError:
    """Build the 409 the contract specifies for a second click (contract §6).

    The message names the actor and the moment, which is what the example in the
    contract shows and what makes the message worth having.
    """
    when = approval.decided_at.isoformat() if approval.decided_at else "an earlier time"
    by = approval.decided_by or "an operator"
    return ApiError(
        409,
        "approval_already_decided",
        f"Approval {approval.id} was already {approval.status.value} by {by} at {when}.",
        {"approval_id": str(approval.id), "status": approval.status.value},
    )


def _summary(approval: ApprovalRequest) -> ApprovalSummary:
    """Project an ``ApprovalRequest`` to the inbox shape, with approver context."""
    return ApprovalSummary(
        id=approval.id,
        run_id=approval.run_id,
        tool_call_id=approval.tool_call_id,
        status=approval.status,
        reason=approval.reason,
        risk_explanation=approval.risk_explanation,
        arguments_snapshot=approval.arguments_snapshot,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        context=_context_for(approval),
    )


def _context_for(approval: ApprovalRequest) -> ApprovalContext | None:
    """Assemble the approver's context from the approval's own snapshot.

    Whoever persisted the approval may have tucked the display context into the
    arguments snapshot under ``_context``; reading it from there keeps this
    endpoint to already-persisted data and triggers no tool calls (contract §5).
    """
    raw = approval.arguments_snapshot.get("_context")
    if not isinstance(raw, dict):
        return None
    company = raw.get("company")
    subject = raw.get("ticket_subject")
    return ApprovalContext(
        company=str(company) if company is not None else None,
        ticket_subject=str(subject) if subject is not None else None,
    )
