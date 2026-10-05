"""Agent run status, the allowed-transition table, and ``AgentRun``.

Responsibility: the run state machine in one place. The status is a single enum
column, never a set of booleans, and the legal edges are *data* so a graph edit
in an orchestration adapter cannot silently widen them
(``docs/architecture.md`` §4, ``docs/agent-state-machine.md``).

Layer: ``domain``. Imports only ``__future__``, the standard library
(``datetime``, ``enum``, ``uuid``) and Pydantic.

M0 note: ``RunStatus`` and ``ALLOWED_TRANSITIONS`` are transcribed literally
from the specification (``docs/agent-state-machine.md`` §2) because that table
*is* the spec and is safe to freeze now. ``AgentRun.transition_to`` is a stub;
the transition logic lands in M1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """The lifecycle of an agent run.

    ``WAITING_APPROVAL`` is the one non-terminal state in which the worker is
    *not* holding the row -- every other non-terminal state is claim-and-work.
    """

    RECEIVED = "received"
    CLASSIFYING = "classifying"
    RETRIEVING = "retrieving"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    RESPONDING = "responding"
    COMPLETED = "completed"
    FAILED = "failed"


# From docs/agent-state-machine.md §2. This table is the specification: every
# edge below is justified in §2.1, and COMPLETED/FAILED deliberately have empty
# successor sets (COMPLETED -> EXECUTING is illegal and has a test).
ALLOWED_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.RECEIVED: frozenset({RunStatus.CLASSIFYING, RunStatus.FAILED}),
    RunStatus.CLASSIFYING: frozenset({RunStatus.RETRIEVING, RunStatus.FAILED}),
    RunStatus.RETRIEVING: frozenset({RunStatus.PLANNING, RunStatus.FAILED}),
    RunStatus.PLANNING: frozenset({RunStatus.EXECUTING, RunStatus.RESPONDING, RunStatus.FAILED}),
    RunStatus.EXECUTING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.WAITING_APPROVAL,
            RunStatus.RESPONDING,
            RunStatus.FAILED,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {RunStatus.EXECUTING, RunStatus.RESPONDING, RunStatus.FAILED}
    ),
    RunStatus.RESPONDING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
}

# Derived from the enum, not typed as literals: a new state defaults to
# *claimable*, which is the safe direction (forgetting to exclude a parked state
# causes duplicate work, and a test catches it). See §5.
CLAIMABLE: Final[frozenset[RunStatus]] = frozenset(RunStatus) - {
    RunStatus.WAITING_APPROVAL,
    RunStatus.COMPLETED,
    RunStatus.FAILED,
}

TERMINAL: Final[frozenset[RunStatus]] = frozenset({RunStatus.COMPLETED, RunStatus.FAILED})


class AgentRun(BaseModel):
    """A single execution of the workflow for one ticket.

    Mirrors the ``agent_runs`` table (``docs/data-model.md`` §2). The domain
    object carries no ORM behaviour -- persistence mapping lives in
    ``opspilot.adapters.persistence``.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID
    ticket_id: UUID
    status: RunStatus = RunStatus.RECEIVED
    model_provider: str
    model_name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_terminal(self) -> bool:
        """Whether the run has reached a terminal state."""
        return self.status in TERMINAL

    def can_transition_to(self, target: RunStatus) -> bool:
        """Whether ``target`` is an allowed successor of the current status."""
        return target in ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, target: RunStatus) -> AgentRun:
        """Move the run to ``target``, or raise ``IllegalTransition``.

        The only way to change status; there is no setter. Raises
        ``IllegalTransition`` when the edge is not in ``ALLOWED_TRANSITIONS``.

        M0 stub -- implementation lands in M1.
        """
        raise NotImplementedError
