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
"""

from __future__ import annotations

from uuid import UUID

from opspilot.api.schemas import ApprovalDecisionRequest, ApprovalSummary


async def list_pending_approvals() -> list[ApprovalSummary]:
    """Return the pending approval inbox. M0 stub."""
    raise NotImplementedError


async def approve(approval_id: UUID, request: ApprovalDecisionRequest) -> ApprovalSummary:
    """Record approval and re-queue the run. M0 stub."""
    raise NotImplementedError


async def reject(approval_id: UUID, request: ApprovalDecisionRequest) -> ApprovalSummary:
    """Record rejection and re-queue the run for an escalation reply. M0 stub."""
    raise NotImplementedError
