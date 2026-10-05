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

from opspilot.agents.schemas import ProposedAction, TicketClassification
from opspilot.domain.runs import AgentRun
from opspilot.ports.vector_store import SearchHit


@dataclass
class ToolCallRecord:
    """One tool call the runtime has driven through the gates, as the loop sees it.

    A *view* of what was persisted: the ``tool_call_id`` is the one the runtime
    recorded, ``status`` is the terminal status the gates reached, and
    ``result`` is the gateway's returned payload when it executed. It is not an
    authority of any kind -- gate 5 re-reads the database for an approval rather
    than trusting anything here (``docs/tool-permissions.md`` §3.1).
    """

    tool_call_id: UUID
    tool_name: str
    status: str
    result: dict[str, object] | None = None


@dataclass
class RunContext:
    """Everything one run's loop needs to carry between steps.

    Plain data, no behaviour: the gates read the static registry, never this
    context, so nothing stored here can widen a permission or bypass a gate.
    The ``proposed_actions`` list is the model's output verbatim and is therefore
    untrusted -- it is carried so the prompt can show the model its own prior
    reasoning, not because the runtime consults it when deciding.
    """

    run: AgentRun
    ticket_subject: str
    ticket_body: str
    customer_email: str
    ticket_id: UUID | None = None
    classification: TicketClassification | None = None
    retrieval_hits: list[SearchHit] = field(default_factory=list)
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    executed_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    steps_taken: int = 0
    max_steps: int = 24
    response_body: str = ""
    escalated: bool = False


@dataclass
class StepState:
    """The scratch state for a single step in the loop."""

    sequence: int
    started_at: float
