"""API routers: tickets, runs, approvals, knowledge.

Responsibility: the route table, grouped by resource. Each router depends on the
ports (stores, gateway) through FastAPI dependencies, never on a concrete
adapter, so the app factory is the only place that picks an implementation.

Layer: ``api``. All routes sit behind the operator-token dependency.
"""

from __future__ import annotations
