"""API routers: tickets, runs, approvals, knowledge.

Responsibility: the route table, grouped by resource. Each router depends on the
ports (stores, gateway) through FastAPI dependencies, never on a concrete
adapter, so the app factory is the only place that picks an implementation.

Layer: ``api``. Every route but the two health probes sits behind the
operator-token dependency -- ``/health`` and ``/ready`` are deliberately
token-free, because a liveness probe carries no credential and a probe that
needed one would be reporting on the credential rather than on the service.
"""

from __future__ import annotations
