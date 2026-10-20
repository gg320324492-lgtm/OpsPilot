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
from opspilot.domain.errors import ApprovalAlreadyDecided, ApprovalExpired, NotFoundError
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
    # ``total`` counts the rows that match the filter, not the rows on this
    # page. It used to be ``len(items)``, which made a paginated inbox report
    # a total that did not count the approvals it did not return -- and the
    # dashboard read that number as "how many are waiting for a human".
    #
    # The count is a *required* port method, so there is no fallback to the
    # page length: a store that cannot count is a store that does not satisfy
    # the contract this endpoint publishes (contract §10).
    total = await approvals.count(status=parsed)
    return ApprovalListResponse(items=items, total=total)


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
            409 ``approval_expired`` if it timed out before anybody decided it;
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
        ApiError: 404, 409 ``approval_already_decided``, 409
            ``approval_expired`` or 409 ``run_not_awaiting_approval``.
    """
    body = request or ApprovalDecisionRequest()
    # The identity is the server's to assign. It used to be
    # ``body.decided_by or operator``, which let the request body name the
    # operator -- so a caller could put somebody else's name on a refund they
    # approved themselves, in the one column that records who did it.
    decided_by = operator

    approval: ApprovalRequest | None = await _get_approval(approvals, approval_id)
    if approval is None:
        raise ApiError(
            404,
            "approval_not_found",
            f"Approval {approval_id} does not exist.",
            {"approval_id": str(approval_id)},
        )
    if approval.status is ApprovalStatus.EXPIRED:
        raise _expired(approval)
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
    # and the store says so -- which we turn into a 409 rather than a double
    # grant.
    try:
        decided = await approvals.decide(
            approval_id,
            approved=approved,
            decided_by=decided_by,
            note=body.note,
            decided_at=datetime.now(UTC),
        )
    except ApprovalExpired as exc:
        # The TTL swept this approval between the pre-check above and the
        # conditional update. The store's update matched no row because the
        # status is no longer ``pending`` -- and the store tells the two apart
        # for us, so the answer here is "it expired", not "somebody else
        # decided it first".
        refreshed = await _get_approval(approvals, approval_id)
        raise _expired(refreshed or approval) from exc
    except ApprovalAlreadyDecided as exc:
        # The store's conditional update matched no row: another writer decided
        # this approval first. The refusal *is* the answer. Before, the store
        # returned the winner's row instead, and a caller that could not tell
        # the two apart read the winner's decision as its own -- which is why a
        # genuinely concurrent second click answered 200 where the contract
        # promises 409.
        refreshed = await _get_approval(approvals, approval_id)
        raise _already_decided(refreshed or approval) from exc
    except NotFoundError as exc:
        # The approval row disappeared between the pre-check and the update.
        # Narrow window, but it is a missing entity, not a lost race.
        raise ApiError(
            404,
            "approval_not_found",
            f"Approval {approval_id} does not exist.",
            {"approval_id": str(approval_id)},
        ) from exc
    except LookupError as exc:
        # A store that signals the same condition with a bare ``LookupError``;
        # the port's docstring names ``ApprovalAlreadyDecided``, but a
        # third-party implementation of the port may not.
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
        note=decided.note,
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


def _expired(approval: ApprovalRequest) -> ApiError:
    """Build the 409 for a decision on an approval that timed out (contract §6).

    **A different answer from :func:`_already_decided`, on purpose.** That one
    says "somebody else decided this first" and names the actor and the
    moment. This one says the request timed out -- and names nobody, because
    nobody decided it. Reporting an expired approval as "already decided by an
    operator at ..." would put a person in a sentence that is false, on the one
    screen an operator is reading to find out what happened to their refund.

    The message also does not promise a re-proposal, because no code path
    makes one: the run stays parked and the decision simply cannot be
    recorded. What the operator is owed is the truth about the state, and the
    gap is recorded in ``docs/limitations.md``.
    """
    return ApiError(
        409,
        "approval_expired",
        (
            f"Approval {approval.id} expired before it was decided. The run is "
            "still parked and no tool call was made; this decision cannot be "
            "recorded."
        ),
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
    """The approver's context, as persisted when the run parked.

    Assembled by the runtime from data the run had already fetched, and kept in
    a column of its own. It used to be read out of ``arguments_snapshot`` under
    a ``_context`` key that **nothing ever wrote** -- the snapshot is filled
    from the model's tool arguments, which gate 1 validates with
    ``extra="forbid"``, so it could not carry that key -- and every approval
    the system created therefore reported ``context: null`` while the
    contract's worked example showed a company and a subject.

    It is deliberately not in the snapshot either: the snapshot is the one
    thing that means "this is what the human approved and this is what will be
    executed", and it is immutable (``docs/tool-permissions.md`` §3.2). A
    display-only string has no business inside it.
    """
    context = approval.context
    if context is None:
        return None
    return ApprovalContext(
        company=context.company,
        ticket_subject=context.ticket_subject,
    )
