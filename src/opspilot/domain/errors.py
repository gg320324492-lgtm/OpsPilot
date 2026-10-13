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


class NotFoundError(DomainError):
    """A referenced entity does not exist."""


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
