"""Orchestration adapters: the linear executor and the LangGraph executor.

Responsibility: two implementations of ``opspilot.ports.orchestrator.Orchestrator``.
``LinearOrchestrator`` is deterministic and milliseconds-fast and is what the
golden-path tests run on; ``LangGraphOrchestrator`` is the production executor
and is exercised by its own contract test rather than by every test.

Layer: ``adapters``. Only this package may import ``langgraph``.

The design point (``docs/architecture.md`` §4): the state machine and the
allowed-transition table live in ``domain/``, not in a graph definition, so
swapping the executor changes nothing above this directory.
"""

from __future__ import annotations
