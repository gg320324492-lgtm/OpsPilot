"""``LinearOrchestrator``: a deterministic, dependency-free step executor.

Responsibility: implement ``Orchestrator.step`` as a straight sequence -- no
graph, no library -- by delegating to the runtime functions. It is deterministic
and milliseconds fast, which is why the golden-path integration tests run on it
instead of on LangGraph.

Layer: ``adapters`` (orchestration). Implements
``opspilot.ports.orchestrator.Orchestrator``.

The point of having it (``docs/architecture.md`` §4): a test asserting the whole
workflow must not depend on a graph library. LangGraph is exercised in its own
contract test, which asserts the two orchestrators produce equivalent step
results, rather than by every test in the suite.
"""

from __future__ import annotations

from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepResult


class LinearOrchestrator:
    """Executes one step of a run in a fixed, deterministic order."""

    async def step(self, run: AgentRun) -> StepResult:
        """Advance the run by one step, dispatching on its current status.

        Raises ``RunParked`` at the approval gate (control flow, not failure).
        M0 stub.
        """
        raise NotImplementedError
