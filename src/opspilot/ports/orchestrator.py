"""The ``Orchestrator`` port.

Responsibility: the seam that inverts the dependency on LangGraph. The runtime
owns the state machine and the loop; an orchestrator is one possible *executor*
of a single step. LangGraph is a production implementation behind this Protocol,
and the linear implementation is what the golden-path tests run on.

Layer: ``ports``. Imports only ``typing`` and ``opspilot.domain`` -- no
``langgraph`` import appears here or anywhere above an adapter.

The design argument (``docs/architecture.md`` §4): the allowed-transition table
lives in ``domain/``, so it is unit-testable without LangGraph installed and a
graph edit cannot widen it. Phase 2's likely changes (retries, fallback models,
replay) are executor concerns and land behind this boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from opspilot.domain.runs import AgentRun, RunStatus


class StepKind(StrEnum):
    """What a single step did, for the step trace."""

    CLASSIFY = "classify"
    RETRIEVE = "retrieve"
    PLAN = "plan"
    EXECUTE = "execute"
    RESPOND = "respond"
    PARK = "park"


class StepResult(BaseModel):
    """The outcome of one orchestrated step."""

    kind: StepKind
    to_status: RunStatus
    detail: dict[str, object] | None = None


@runtime_checkable
class Orchestrator(Protocol):
    """Executes one step of a run and reports the resulting status."""

    async def step(self, run: AgentRun) -> StepResult:
        """Advance ``run`` by exactly one step.

        A step that reaches the approval gate raises ``RunParked`` rather than
        returning a normal result, because parking is control flow (the workflow
        working as designed), not a failure.
        """
        ...
