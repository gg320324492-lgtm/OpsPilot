"""Agent run status, the allowed-transition table, and ``AgentRun``.

Responsibility: the run state machine in one place. The status is a single enum
column, never a set of booleans, and the legal edges are *data* so a graph edit
in an orchestration adapter cannot silently widen them
(``docs/architecture.md`` §4, ``docs/agent-state-machine.md``).

Layer: ``domain``. Imports only ``__future__``, the standard library
(``datetime``, ``enum``, ``uuid``) and Pydantic.

``RunStatus`` and ``ALLOWED_TRANSITIONS`` are transcribed literally from the
specification (``docs/agent-state-machine.md`` §2) because that table *is* the
spec. ``AgentRun.transition_to`` is the single implementation of the rule: it
looks the current status up in the table, raises ``IllegalTransition`` when the
edge is absent, and otherwise sets the status and returns the run. There is no
setter, and nothing else in the package re-implements the check -- two
implementations of a security-relevant rule is one implementation too many.

``ClaimLeaseState`` is *not* part of that specification and is deliberately kept
out of it. The claim lease is not a state of the run -- ``claim_next`` takes a
row without moving it (``migrations/versions/0002_claim_lease.py``), and
``docs/agent-state-machine.md`` §2 has no edge for it -- it is a second,
independent fact about who is holding the row. So it is a separate enum beside
the status rather than a member of it: adding it to ``RunStatus`` would have
meant editing a specification table to describe something the table was never
about, and the run's status would then have two independent ways of saying
"somebody is working on this", which is the ambiguity the lease was introduced
to remove.
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


class ClaimLeaseState(StrEnum):
    """Whether a worker is holding a run's claim lease *right now*.

    The lease is ``agent_runs.claimed_until``/``claimed_by``
    (``migrations/versions/0002_claim_lease.py``): ``claim_next`` writes them in
    the same transaction as the claim read, ``renew_lease`` pushes the deadline
    forward while the holder is alive, and ``set_status`` clears them on the
    transitions that hand the row back.

    **Three states, not a boolean.** ``UNCLAIMED`` is a run nothing has ever
    claimed. ``HELD`` is a deadline in the future, so a second worker cannot take
    the row. ``EXPIRED`` is a lease whose holder died or stalled: nobody is
    driving the run, and it is claimable again. Collapsing the last two into one
    boolean loses the only distinction that matters to the operator reading a run
    stuck at ``RECEIVED`` -- "a worker holds this and is working" versus "the
    holder is gone and the run is waiting to be re-claimed"
    (``docs/limitations.md`` §2) -- and pushes the choice of which sentence to
    say back out to every call site.
    """

    UNCLAIMED = "unclaimed"
    HELD = "held"
    EXPIRED = "expired"


class AgentRun(BaseModel):
    """A single execution of the workflow for one ticket.

    Mirrors the ``agent_runs`` table (``docs/data-model.md`` §2), including the
    claim lease: the claim writes ``claimed_until``/``claimed_by`` but does not
    change ``status``, so until those two columns were carried here a claimed run
    and a run nothing claimed were the same object. The domain object carries no
    ORM behaviour -- persistence mapping lives in
    ``opspilot.adapters.persistence``.

    **The lease is here rather than behind a second read, deliberately.** It is
    operational state (who holds it, until when) rather than the run's business
    identity, and that argues for keeping it out; the mirror property argues the
    other way, and the second read is what decides it. A repository method beside
    ``get`` would be a second round trip per render *and* a second
    ``_optional_method`` probe that a deployment could be missing --
    ``adapters/persistence/repositories.py`` records what happens then (a silent
    empty panel), and it is the failure this package documents most often.
    ``started_at``, ``completed_at`` and ``failure_reason`` are operational
    columns too and have always been here. What is *not* here is a way to write
    the lease: only ``claim_next``, ``renew_lease`` and ``set_status`` move it,
    all in the adapter, and ``claim_state`` reads it without deciding anything an
    operator should decide.
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
    claimed_until: datetime | None = None
    claimed_by: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the run has reached a terminal state."""
        return self.status in TERMINAL

    def claim_state(self, now: datetime | None = None) -> ClaimLeaseState:
        """Whether a worker holds this run's claim lease at ``now``.

        The clock is a parameter so a test can place itself exactly on the
        boundary instead of sleeping up to it, and defaults to ``now`` because
        every real caller asks about the present.

        The deadline is normalised to UTC first because SQLite has no
        ``timestamptz``: a value read back there is naive even though every value
        written is aware UTC (ADR-0004; the same quirk and the same workaround on
        the other side of the wire, in ``web/src/lib/format.ts::parseTimestamp``).
        Comparing a naive and an aware datetime raises ``TypeError``, so a
        deployment on the tests-only SQLite path would answer 500 for every run
        carrying a lease.
        """
        if self.claimed_until is None:
            return ClaimLeaseState.UNCLAIMED
        deadline = self.claimed_until
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        moment = now if now is not None else datetime.now(UTC)
        return ClaimLeaseState.HELD if deadline > moment else ClaimLeaseState.EXPIRED

    def can_transition_to(self, target: RunStatus) -> bool:
        """Whether ``target`` is an allowed successor of the current status."""
        return target in ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, target: RunStatus) -> AgentRun:
        """Move the run to ``target``, or raise ``IllegalTransition``.

        The only way to change status; there is no setter. ``ALLOWED_TRANSITIONS``
        is the specification (``docs/agent-state-machine.md`` §2), so this method
        is a table lookup, not a rule of its own: it raises
        ``IllegalTransition(current, requested)`` when the edge is absent and
        otherwise sets ``status`` and returns ``self`` so a caller can chain.

        ``runtime.transition_or_raise`` delegates here rather than duplicating
        the check -- the rule lives in exactly one place.
        """
        if target not in ALLOWED_TRANSITIONS[self.status]:
            # Imported lazily because ``errors`` imports ``RunStatus`` from this
            # module under ``TYPE_CHECKING``; that keeps the import graph acyclic
            # at runtime while this stays the one implementation of the check.
            from opspilot.domain.errors import IllegalTransition

            raise IllegalTransition(self.status, target)
        self.status = target
        return self
