"""``LangGraphOrchestrator``: the LangGraph executor, unwritten.

Responsibility: implement ``Orchestrator.step`` as a LangGraph graph whose nodes
correspond to the state machine's stages. None of that is written: ``step`` and
``build_graph`` both raise ``NotImplementedError``. Nothing constructs or calls
this class, and no ``langgraph`` import appears anywhere in the project yet --
this is the only module permitted to add one.

Layer: ``adapters`` (orchestration).

The dependency is inverted on purpose (``docs/architecture.md`` §4): the runtime
owns ``RunStatus``, ``ALLOWED_TRANSITIONS`` and the loop, and LangGraph is one
possible executor for it. Building the graph here -- rather than making the
project "be" the graph -- is why the allowed-transition table is unit-testable
without the library installed and why a graph edit cannot silently widen it.
That argument is sound and is why the module exists while the class is empty.

What drives a run today is ``agents/runtime.py::_pump``, the
classify/retrieve/plan/execute/respond loop, dispatched stage by stage through
``LinearOrchestrator``. This adapter implements no part of that: it is still a
stub, so nothing here is on a live path.

Coverage note: ``pyproject.toml`` omits this module from the coverage number,
and no test imports it. Those are two independent facts: the module is untested,
not tested elsewhere. There is no contract test asserting it agrees with
``LinearOrchestrator``, and there cannot be one while this adapter's ``step`` and
``build_graph`` raise ``NotImplementedError`` -- the linear executor is now real,
so it is this side of the comparison that has nothing to compare against.
"""

from __future__ import annotations

from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepResult


class LangGraphOrchestrator:
    """The LangGraph executor, unwritten. ``step`` and ``build_graph`` raise.

    "Unwritten" rather than "an M0 stub", which is what this used to say: M0 was
    the milestone that shipped the skeleton, and this class was never part of it
    -- M4 built ``LinearOrchestrator`` and left this one out. Calling it an M0
    stub implies a stage that has passed; the honest statement is that it is a
    declared capability nothing implements, recorded as such in
    ``docs/limitations.md`` §8 and left unticked in ``docs/milestones.md`` §M4.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._config = (args, kwargs)

    def build_graph(self) -> object:
        """Assemble the graph from the runtime's stages. Unwritten."""
        raise NotImplementedError

    async def step(self, run: AgentRun) -> StepResult:
        """Advance the run by one graph node. Unwritten."""
        raise NotImplementedError
