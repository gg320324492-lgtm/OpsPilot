"""``RunContext`` and per-step working state.

Responsibility: the mutable working state the runtime carries through a run --
the run, the ticket, what has been retrieved, the conversation-with-the-model so
far, and the step budget. It is *runtime* state, distinct from the persisted
``AgentRun`` (domain) and from the ``AgentStep`` trace rows.

Layer: ``agents``. Imports ``opspilot.domain`` and ``opspilot.ports`` only.

Note that no field here can widen a permission or bypass a gate: the context
holds data, not authority. The gates read the static registry, not the context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from opspilot.agents.schemas import TicketClassification
from opspilot.domain.runs import AgentRun
from opspilot.ports.vector_store import SearchHit


@dataclass
class RunContext:
    """Everything one run's loop needs to carry between steps."""

    run: AgentRun
    ticket_subject: str
    ticket_body: str
    customer_email: str
    classification: TicketClassification | None = None
    retrieval_hits: list[SearchHit] = field(default_factory=list)
    proposed_actions: list[dict[str, object]] = field(default_factory=list)
    executed_tool_calls: list[UUID] = field(default_factory=list)
    steps_taken: int = 0
    max_steps: int = 24


@dataclass
class StepState:
    """The scratch state for a single step in the loop."""

    sequence: int
    started_at: float
