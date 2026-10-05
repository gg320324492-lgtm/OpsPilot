"""Agents layer: the runtime, its working state, prompt assembly and schemas.

Responsibility: drive a run through the state machine, assemble prompts,
validate model output into structured schemas, and enforce the five gates on
every tool call. This is where ``_gate_and_execute`` lives -- the
security-critical path.

Layer: ``agents``. Imports ``opspilot.domain`` and ``opspilot.ports`` only.
It must never import ``opspilot.adapters``: the runtime depends on Protocols, and
a concrete provider, gateway or store is injected. If an SDK leaked in here, the
test suite would fail on a machine where that SDK is not installed -- which is
exactly the guard this boundary provides (``docs/architecture.md`` §3).
"""

from __future__ import annotations
