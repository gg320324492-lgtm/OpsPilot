"""Tracing layer: writes the execution timeline and the compliance ledger.

Responsibility: record what happened as *rows*, not logs -- ``AgentStep`` for the
ordered timeline the dashboard renders, and ``AuditEvent`` for the append-only
compliance record keyed by actor.

Layer: cross-cutting; depends on ``domain`` and ``ports``. The two stores are
deliberately distinct (``docs/data-model.md`` §3): collapsing them would mean
either the dashboard reads a compliance ledger or the ledger becomes a rendering
concern.
"""

from __future__ import annotations
