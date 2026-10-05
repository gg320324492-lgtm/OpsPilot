"""``LangGraphOrchestrator``: the production executor, behind the port.

Responsibility: implement ``Orchestrator.step`` as a LangGraph graph whose nodes
correspond to the state machine's stages. This is the only module in the project
that imports ``langgraph``.

Layer: ``adapters`` (orchestration).

The dependency is inverted on purpose (``docs/architecture.md`` §4): the runtime
owns ``RunStatus``, ``ALLOWED_TRANSITIONS`` and the loop, and LangGraph is one
possible executor for it. Building the graph here -- rather than making the
project "be" the graph -- is why the allowed-transition table is unit-testable
without the library installed and why a graph edit cannot silently widen it.

Coverage note: this module is omitted from the coverage number
(``pyproject.toml``) and exercised by a contract test in CI only, because
covering it with a mock of LangGraph would measure the mock.
"""

from __future__ import annotations

from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepResult


class LangGraphOrchestrator:
    """Executes one run step as a LangGraph node."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._config = (args, kwargs)

    def build_graph(self) -> object:
        """Assemble the graph from the runtime's stages. M0 stub."""
        raise NotImplementedError

    async def step(self, run: AgentRun) -> StepResult:
        """Advance the run by one graph node. M0 stub."""
        raise NotImplementedError
