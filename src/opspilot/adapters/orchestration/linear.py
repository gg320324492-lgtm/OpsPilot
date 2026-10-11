"""``LinearOrchestrator``: the graph-free step executor, and an M0 stub.

Responsibility: implement ``Orchestrator.step`` as a straight sequence -- no
graph, no library -- by delegating to the runtime functions. None of that is
written: ``step`` raises ``NotImplementedError``, so this class runs nothing.

Layer: ``adapters`` (orchestration). Implements
``opspilot.ports.orchestrator.Orchestrator``.

The reason the port exists (``docs/architecture.md`` §4): a test asserting the
whole workflow must not depend on a graph library. The runtime owns
``RunStatus``, ``ALLOWED_TRANSITIONS`` and the loop, and an orchestrator is one
possible executor for it -- which is why the boundary is drawn here even though
neither executor is implemented yet.

What drives a run today is ``agents/runtime.py::_pump``, the
classify/retrieve/plan/execute/respond loop. ``worker/__main__.py``,
``evals/runner.py``, ``tests/agent/_golden_harness.py`` and
``scripts/demo_golden_path.py`` each construct a ``LinearOrchestrator`` and pass
it as ``orchestrator=``; ``run_loop`` discards it (``_ = orchestrator``) and
never calls ``step``. The class is carried by those call sites, not run by them.

No test calls ``step``, and no contract test compares this executor against
``LangGraphOrchestrator``: one cannot assert equivalence between two methods
that both raise ``NotImplementedError``.
"""

from __future__ import annotations

from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepResult


class LinearOrchestrator:
    """M0 stub for the graph-free step executor. ``step`` raises."""

    async def step(self, run: AgentRun) -> StepResult:
        """Advance the run by one step, dispatching on its current status.

        M0 stub: raises ``NotImplementedError`` and reaches no gate. The
        approval gate that raises ``RunParked`` lives in
        ``agents/runtime.py::_park_run``, not here.
        """
        raise NotImplementedError
