"""Orchestration adapters: the linear executor and the LangGraph executor.

Responsibility: two implementations of ``opspilot.ports.orchestrator.Orchestrator``.
Both are M0 stubs: ``step`` raises ``NotImplementedError`` in each, and
``LangGraphOrchestrator.build_graph`` does too. Nothing runs through this
package. ``LinearOrchestrator`` is what the golden-path call sites construct
and hand to ``run_loop``, which discards it; ``LangGraphOrchestrator`` nothing
constructs at all. There is no contract test comparing the two, and there
cannot be one while both raise.

What drives a run today is ``agents/runtime.py::_pump``, the
classify/retrieve/plan/execute/respond loop, which does not consult either
executor. Both are recorded as unimplemented in ``docs/milestones.md`` §M4.

Layer: ``adapters``. Only this package may import ``langgraph``.

The design point (``docs/architecture.md`` §4): the state machine and the
allowed-transition table live in ``domain/``, not in a graph definition, so
swapping the executor changes nothing above this directory.
"""

from __future__ import annotations
