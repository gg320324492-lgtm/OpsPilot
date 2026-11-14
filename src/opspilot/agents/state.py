"""``RunContext`` and per-step working state.

Responsibility: the mutable working state the runtime carries through a run --
the run, the ticket, what has been retrieved, the conversation-with-the-model so
far, and the step budget. It is *runtime* state, distinct from the persisted
``AgentRun`` (domain) and from the ``AgentStep`` trace rows.

Layer: ``agents``. Imports ``opspilot.domain``, ``opspilot.ports`` and
``opspilot.settings`` -- the last one only to keep a *default* from becoming a
second definition (see :data:`DEFAULT_MAX_STEPS`).

Note that no field here can widen a permission or bypass a gate: the context
holds data, not authority. The gates read the static registry, not the context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

from opspilot.agents.schemas import ProposedAction, TicketClassification
from opspilot.domain.runs import AgentRun
from opspilot.ports.vector_store import SearchHit


def default_max_steps() -> int:
    """``Settings.max_steps``, so the step budget has one definition.

    The budget used to be a literal ``24`` here *and* another literal ``24`` in
    ``agents/runtime.py``, both duplicating ``settings.py``. That is the defect
    M6a fixed for ``RETRIEVAL_MIN_SCORE``: a deployment that wrote ``MAX_STEPS=40``
    got a 24-step budget, and the run that demonstrated it exhausted its steps
    and ended ``max_steps_exceeded`` with no way to say why. Three copies of a
    number an operator can move in ``.env`` is two too many, so the definition
    lives in ``settings.py`` and every other module *references* it.

    A function rather than a module constant, on purpose. A ``Final[int] =
    _settings_max_steps()`` evaluated at import reads the environment once, for
    the life of the process -- so a deployment (or a test) that set ``MAX_STEPS``
    after this module was first imported would still get the earlier value, which
    is the same stale-copy defect wearing a different hat. The dataclass field
    below therefore takes this as a ``default_factory``: each ``RunContext``
    resolves the budget when it is constructed.
    """
    from opspilot.settings import get_settings

    return get_settings().max_steps


def default_rejected_proposal_stop_limit() -> int:
    """``Settings.rejected_proposal_stop_limit``, so the stop threshold has one
    definition.

    Phase 3 M2-02. Same shape and same reason as :func:`default_max_steps`
    above: the threshold lives in ``settings.py`` and is resolved when a
    ``RunContext`` is constructed, never frozen at import -- a deployment (or a
    test) that sets ``REJECTED_PROPOSAL_STOP_LIMIT`` after this module was first
    imported still gets the current value.
    """
    from opspilot.settings import get_settings

    return get_settings().rejected_proposal_stop_limit


def proposal_fingerprint(proposal: ProposedAction) -> tuple[str, str]:
    """The ``(tool_name, canonical_arguments_json)`` identity of a proposal.

    Two proposals are "the same invalid proposal" when both the tool name and
    the arguments match exactly. The arguments are serialised with
    ``sort_keys=True`` so ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` -- the
    same call in a different key order -- count as the same proposal: the
    model's JSON key order carries no information, and counting a re-ordered
    duplicate as "different" would let the loop keep spinning. The JSON is a
    string so the fingerprint is hashable and comparable across the run.
    """
    return (
        proposal.tool_name or "",
        json.dumps(proposal.arguments, sort_keys=True, ensure_ascii=False),
    )


@dataclass
class ToolCallRecord:
    """One tool call the runtime has driven through the gates, as the loop sees it.

    A *view* of what was persisted: the ``tool_call_id`` is the one the runtime
    recorded, ``status`` is the terminal status the gates reached, and
    ``result`` is the gateway's returned payload when it executed. It is not an
    authority of any kind -- gate 5 re-reads the database for an approval rather
    than trusting anything here (``docs/tool-permissions.md`` §3.1).

    ``rejection_reason``/``rejection_detail`` mirror the persisted row's
    ``rejection_reason`` (the gate name) and ``error`` (the rule-level detail)
    for a ``rejected`` call. They exist so the planning prompt can tell the
    model *why* its last proposal was refused -- the one fact that stops it
    re-proposing the same malformed call (``docs/progress.md``, the B2 loop) --
    and are ``None`` for every non-rejected status.
    """

    tool_call_id: UUID
    tool_name: str
    status: str
    result: dict[str, object] | None = None
    rejection_reason: str | None = None
    rejection_detail: str | None = None


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
    # Resolved per construction rather than once at import -- see
    # ``default_max_steps``. The worker passes this explicitly on every context
    # it builds, so this default is the fallback for tests and any future
    # builder, not the production path.
    max_steps: int = field(default_factory=default_max_steps)
    # Phase 3 M2-02: the consecutive-identical-invalid-proposal stop threshold.
    # Resolved per construction for the same reason as ``max_steps``; the worker
    # passes it explicitly on every context it builds.
    rejected_proposal_stop_limit: int = field(default_factory=default_rejected_proposal_stop_limit)
    # The run's consecutive identical *invalid* proposals, as a stack of
    # fingerprints (``(tool_name, canonical_arguments_json)``). A list rather
    # than a counter because the reset is structural: a different proposal or an
    # executed call empties the stack, and the *same* proposal pushes onto it --
    # so "the streak broke" is ``not ctx.rejected_streak``, not an int that has
    # to remember how to be re-zeroed at every call site. The runtime owns the
    # pushes and clears; nothing here can widen a permission or bypass a gate.
    rejected_streak: list[tuple[str, str]] = field(default_factory=list)
    response_body: str = ""
    escalated: bool = False


@dataclass
class StepState:
    """The scratch state for a single step in the loop."""

    sequence: int
    started_at: float
