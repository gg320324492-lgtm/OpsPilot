"""Approval status and approval decision value objects.

Responsibility: model the human-approval gate (gate 5). An
``ApprovalRequest`` is created in the *same transaction* as the
``EXECUTING -> WAITING_APPROVAL`` transition, so the parked run and the pending
approval can never disagree. One approval authorises *one specific* tool call
with the exact arguments a human was shown -- never a run, and never a
transaction id that a later proposal could reuse.

Layer: ``domain``. Imports only the standard library and Pydantic.

The split that matters: ``reason`` is what the model *says* it is doing and is
untrusted. ``risk_explanation`` is generated deterministically by
``domain/policies.py`` from the static permission and the arguments. The
approver's UI shows both, labelled differently. See ``docs/tool-permissions.md``
§3.2 and ``docs/data-model.md`` §2.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApprovalStatus(StrEnum):
    """The decision state of an ``ApprovalRequest``."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    """A persisted, human-decided authorisation for one tool call.

    Bound to ``tool_call_id`` (unique -- one approval per call), not to the run.
    ``arguments_snapshot`` is an immutable copy of the arguments as shown to the
    approver; ``ToolCall.arguments`` must not change after this is written.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID
    run_id: UUID
    tool_call_id: UUID
    status: ApprovalStatus = ApprovalStatus.PENDING
    reason: str
    risk_explanation: str
    arguments_snapshot: dict[str, object]
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None

    @property
    def is_decided(self) -> bool:
        """Whether a human has decided (approved or rejected) this request."""
        return self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}


class ApprovalDecision(BaseModel):
    """A value object describing a human's decision on an approval request."""

    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    status: ApprovalStatus
    decided_by: str
    decided_at: datetime
    note: str | None = None


def approve(
    approval: ApprovalRequest, *, decided_by: str, note: str | None = None
) -> ApprovalDecision:
    """Record an approval decision. M0 stub -- implementation lands in M1."""
    raise NotImplementedError


def reject(
    approval: ApprovalRequest, *, decided_by: str, note: str | None = None
) -> ApprovalDecision:
    """Record a rejection decision.

    A rejected refund does not fail the run: the correct outcome is an
    escalation reply via ``WAITING_APPROVAL -> RESPONDING``. M0 stub.
    """
    raise NotImplementedError
