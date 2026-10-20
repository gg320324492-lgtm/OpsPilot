"""Domain exception hierarchy.

Responsibility: the typed failures the domain and the agent runtime raise. Each
exception names a *business* condition, not a transport or library error, so
callers can branch on meaning rather than on a message string.

Layer: ``domain``. Imports only ``__future__`` and the standard library.

Two of these exceptions are control flow, not errors, and are called out because
conflating them with failures is the mistake this hierarchy exists to prevent:

- ``RunParked`` -- raised at the approval gate (gate 5). It means "the run is
  now legitimately waiting for a human", which is the workflow working. It must
  never be caught and mapped to ``FAILED``.
- ``IllegalTransition`` -- a programming error. The transition table is data, so
  reaching this means code asked for an edge that does not exist.

Two of these exceptions carry a second base class, ``LookupError``, and the
reason is the same for both: the API's approval route already catches
``LookupError`` around the store's ``decide`` and answers the contract's 409
for a decision that lost its race. A condition that is *named* but cannot be
caught by the branch written for it is a condition the branch was never really
handling -- see :class:`NotFoundError` and :class:`ApprovalAlreadyDecided`.

**One exception deliberately does not.** :class:`ApprovalExpired` is the
mirror image of those two: it is *not* a ``LookupError``, because the branch
that catches ``LookupError`` answers "somebody else decided this first", and
an approval that timed out was decided by nobody. Adding the base class would
have made that answer reachable and wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from opspilot.domain.runs import RunStatus


class OpsPilotError(Exception):
    """Base class for every exception raised by OpsPilot's own code.

    Catching this (rather than ``Exception``) distinguishes "OpsPilot refused or
    failed this operation" from "something unrelated broke".
    """


class DomainError(OpsPilotError):
    """Base class for violations of a domain rule."""


class NotFoundError(DomainError, LookupError):
    """A referenced entity does not exist.

    Also a ``LookupError``, and that second base class is load-bearing rather
    than cosmetic. A missing entity *is* the builtin "key not found"
    condition, and the approvals route already has a branch for it: it catches
    ``LookupError`` around the store's ``decide`` and answers the contract's
    409 for a decision that lost its race (``api/routers/approvals.py``). While
    ``NotFoundError`` was not a ``LookupError``, that branch was **dead code**
    -- the exception it was written for could never be caught by it -- so the
    route's careful 409 was unreachable and the store's signal fell through to
    the generic handler instead. Adding the base makes the existing branch live
    without touching the route.

    The alternative -- a second exception type for "missing" -- would have split
    one business condition across two names and left the route still unable to
    catch it.
    """


class ApprovalAlreadyDecided(DomainError, LookupError):
    """An approval was already decided, so this decision did not happen.

    The decision is a **conditional** update (``WHERE id = ? AND status =
    'pending'``), so under concurrency exactly one writer moves the row and the
    others affect zero rows. The losers must be told they lost: returning the
    winner's row as if it were their own decision is a lie the caller cannot
    detect, and it is why the second click on "approve" used to answer 200
    where the contract promises 409 (``docs/api-contract.md`` §6).

    ``LookupError`` is a second base for the same reason as
    :class:`NotFoundError`: the approvals route catches it around ``decide``
    and turns it into the contract's 409 ``approval_already_decided``. A
    ``DomainError`` alone would have escaped that branch as a 500 -- the very
    defect this exception exists to fix.

    It lives here, beside :class:`ApprovalArgumentsChanged`, rather than in
    ``domain/approvals.py``: this module is the one that names the typed
    failures the runtime and the API branch on, and an approval-specific
    failure already lives here.
    """

    def __init__(self, approval_id: UUID, status: str) -> None:
        self.approval_id = approval_id
        self.status = status
        super().__init__(f"approval {approval_id} was already decided ({status})")


class ApprovalExpired(DomainError):
    """An approval timed out, so this decision did not happen.

    **Not** a ``LookupError``, and that is the whole point of this class.
    The approvals route catches ``LookupError`` around the store's ``decide``
    and answers the contract's 409 ``approval_already_decided`` -- "somebody
    else got there first". An expired approval is a *different* answer: nobody
    decided it, the clock ran out, and the run is still parked waiting for a
    decision that can no longer be given. Had this been a ``LookupError``, it
    would have been swallowed by that branch and reported as "already decided
    by an operator" -- a statement about a person that is false, and about the
    only thing an operator reading it would want to know.

    It is also *not* ``ApprovalAlreadyDecided``: that one names a lost race
    between two writers, and its message ("was already approved by ... at
    ...") is exactly right there and exactly wrong here.
    """

    def __init__(self, approval_id: UUID) -> None:
        self.approval_id = approval_id
        super().__init__(f"approval {approval_id} expired before it was decided")


class IllegalTransition(DomainError):
    """A run status change was requested that the transition table forbids.

    Example: ``COMPLETED -> EXECUTING``. This is a programming error; the
    allowed edges are data in ``domain/runs.py`` so a rejection here means
    caller code, not configuration, is wrong.
    """

    def __init__(self, current: RunStatus, requested: RunStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"illegal run transition: {current.value} -> {requested.value}")


class RunParked(DomainError):
    """The run has been parked awaiting human approval (gate 5).

    Control flow, not a failure. The runtime raises this to unwind the current
    step when the approval gate fires; the worker catches it, releases the row
    and moves on. ``RunStatus.WAITING_APPROVAL`` is the only non-terminal state
    in which the worker does not hold the row (``docs/agent-state-machine.md``
    §1).
    """

    def __init__(self, run_id: str, tool_call_id: str) -> None:
        self.run_id = run_id
        self.tool_call_id = tool_call_id
        super().__init__(f"run {run_id} parked awaiting approval for tool call {tool_call_id}")


class MCPUnavailable(DomainError):
    """A tool server could not be reached at all.

    Distinct from a tool that ran and refused. ``invalid_state`` and
    ``not_found`` are answers, and a run can reason about them; this is the
    absence of one. Continuing past it would let the agent plan its next step
    from results it never received -- including proposing a refund it has no
    evidence for -- so the runtime marks the run ``FAILED('mcp_unavailable')``
    and raises this to unwind, exactly as ``docs/agent-state-machine.md`` §3
    describes: "The system could not complete the work it was asked to do."
    """

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"tool server for {tool_name} is unreachable")


class ApprovalArgumentsChanged(DomainError):
    """The arguments of an approval-bound tool call changed after approval.

    ``ToolCall.arguments`` is immutable once an ``ApprovalRequest`` references
    it: the human approved the arguments they were shown, and executing
    different ones would make the approval meaningless
    (``docs/tool-permissions.md`` §3.2).
    """


class PolicyDenied(DomainError):
    """The policy engine (gate 4) refused the tool call.

    Carries the deterministic reason so the tool call can be recorded as
    ``rejected`` with the gate that rejected it.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"policy denied tool call: {reason}")


class UnknownTool(DomainError):
    """The model proposed a tool name that is not in the static registry (gate 2)."""


class TicketExists(DomainError):
    """A ticket with this ``external_id`` already exists.

    The ticket store's unique index on ``external_id`` fired. This is named as a
    business condition rather than left as the driver's ``IntegrityError`` because
    the condition is the contract's (``docs/api-contract.md`` §9): ``POST
    /api/tickets`` always creates, and a client that sends an ``external_id``
    which is already taken gets ``409 ticket_exists`` **carrying the existing
    ticket**, not a 500 that reports a database failure to an operator who did
    nothing wrong.

    It carries the id of the ticket that already holds the ``external_id`` so the
    caller can read it back through the store rather than re-deriving it. Which
    constraint fired is decided by the adapter, not here -- a domain layer that
    parsed ``UNIQUE constraint failed: tickets.external_id`` out of a SQLite
    message and ``duplicate key value violates unique constraint`` out of a
    Postgres one would know two dialects, which is the adapter's job (ADR-0004).
    """

    def __init__(self, external_id: str, ticket_id: UUID) -> None:
        self.external_id = external_id
        self.ticket_id = ticket_id
        super().__init__(f"a ticket with external_id {external_id!r} already exists ({ticket_id})")


class MaxStepsExceeded(DomainError):
    """The run exceeded its plan/execute budget (``MAX_STEPS``).

    Maps to ``FAILED`` with ``failure_reason='max_steps_exceeded'``.
    """

    def __init__(self, run_id: str, max_steps: int) -> None:
        self.run_id = run_id
        self.max_steps = max_steps
        super().__init__(f"run {run_id} exceeded the {max_steps}-step budget")
