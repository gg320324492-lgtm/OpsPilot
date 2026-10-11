"""Orchestration adapters: the linear executor and the LangGraph executor.

Responsibility: two implementations of ``opspilot.ports.orchestrator.Orchestrator``.
``LinearOrchestrator`` is written and is what drives a provider-mode run: it is a
thin dispatcher over the runtime's stage functions (``agents/runtime.py``'s
``*_stage`` functions, sequenced by ``StageDriver``), bound to the driver by
``_pump`` and stepped one stage at a time. ``LangGraphOrchestrator`` remains an M0
stub -- ``step`` and ``build_graph`` both raise ``NotImplementedError`` -- and
nothing constructs it. There is no contract test comparing the two, and there
cannot be one while one of them raises on every method it would compare.

Layer: ``adapters``. Only this package may import ``langgraph``.

The design point (``docs/architecture.md`` §4): the state machine and the
allowed-transition table live in ``domain/``, not in a graph definition, so
swapping the executor changes nothing above this directory.
"""

from __future__ import annotations
