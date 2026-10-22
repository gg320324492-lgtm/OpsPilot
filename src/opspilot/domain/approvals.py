"""Approval status and approval decision value objects.

Responsibility: model the human-approval gate (gate 5). The parked run and the
pending approval are **two writes in a fixed order**, not one transaction: the
``ApprovalRequest`` is written first and the run's transition to
``WAITING_APPROVAL`` second, so a run is never parked on an approval that does
not exist. The approval store and the run store each own their own
transaction, so the two writes are **not** atomic: a crash between them leaves
a pending approval behind a run still in ``EXECUTING``. That is the one
direction in which the two can disagree, and it is the safe one -- the run is
still claimable, so the worker re-drives it and the approval is found again.
One approval authorises *one specific* tool call with the exact arguments a
human was shown -- never a run, and never a transaction id that a later
proposal could reuse.

Layer: ``domain``. Imports only the standard library and Pydantic.

The split that matters: ``reason`` is what the model *says* it is doing and is
untrusted. ``risk_explanation`` is generated deterministically by
``domain/policies.py`` from the static permission and the arguments. The
approver's UI shows both, labelled differently. See ``docs/tool-permissions.md``
§3.2 and ``docs/data-model.md`` §2.

Everything the *human* adds on top of that -- the operator's ``note``, the
approver's ``context`` -- lives in its own column, never inside
``arguments_snapshot``. The snapshot is one thing: what was shown and what will
be executed. It is immutable, and a second meaning inside it would break the
invariant the approval gate depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ApprovalContext:
    """Extra context for the approver, assembled from already-persisted data.

    ``company`` comes from a ``crm.get_customer`` result the run has already
    executed -- no tool call is triggered to assemble it. ``ticket_subject``
    is the ticket's own subject line, which is **customer-supplied text**, not
    a fact the policy engine verified; the dashboard renders it in the quieter
    of its two styles for that reason.

    Frozen, like the other read models in ``ports/stores.py``: the context is
    assembled once, when the run parks, and read many times. A mutable copy
    would let one approver's view of the company drift from the row.

    Every field is optional because the context is **supplementary**: a run
    that never fetched a customer, or a ticket with no subject, still produces
    a valid approval. Absent is not the same as empty, and neither is an
    error.
    """

    company: str | None = None
    ticket_subject: str | None = None


class ApprovalRequest(BaseModel):
    """A persisted, human-decided authorisation for one tool call.

    Bound to ``tool_call_id`` (unique -- one approval per call), not to the run.
    ``arguments_snapshot`` is an immutable copy of the arguments as shown to the
    approver; ``ToolCall.arguments`` must not change after this is written.

    The four optional trailing fields are the *decision* side of the row, and
    they are separate columns rather than entries in ``arguments_snapshot``:
    the snapshot is what the human approved and what will be executed, so it is
    immutable (``docs/tool-permissions.md`` §3.2) and a display-only string has
    no business inside it.

    - ``decided_at`` -- when the decision was taken.
    - ``decided_by`` -- who decided. Server-assigned: it is the authenticated
      operator's identity, never a value the request body chose.
    - ``note`` -- the operator's own remark on the decision.
    - ``context`` -- supplementary display context for the approver.
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
    note: str | None = None
    context: ApprovalContext | None = None

    @property
    def is_decided(self) -> bool:
        """Whether a human has decided (approved or rejected) this request.

        **Nothing in the system asks this.** It is here because a status enum
        that can be ``pending`` invites the question, and the answer is one
        line -- but the *decision* itself is made by the store's ``decide``,
        which is a conditional update, not a function that returns a value
        object. A decision made in the database is not a decision that can be
        modelled as a return value, which is why there is no ``approve()``
        here to mirror it.
        """
        return self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
