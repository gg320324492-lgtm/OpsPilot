"""Health and readiness routes.

Responsibility: ``GET /health`` -- process liveness, touching nothing -- and
``GET /ready`` -- whether the database is reachable and migrated to head.

Layer: ``api`` (router).

The two are deliberately different endpoints, not two fields of one
(``docs/api-contract.md`` §7). A process that answers ``/health`` is alive; a
process that answers ``/ready`` can serve. In Compose the worker's
``depends_on: api: condition: service_healthy`` keys on ``/ready``, never
``/health``, because a process pointed at an unmigrated database is exactly the
state that produces a confusing "table does not exist" three layers down.

Both routes are exempt from the bearer token (contract §8): a health probe has
no credential, and requiring one would make the probe report the *auth* status,
not the process's.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from opspilot.api.schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])

# Reported by ``/health`` -- the package version, so a probe can tell which build
# answered. Kept as a literal here rather than read from metadata at import time,
# which would make ``/health`` depend on the installed distribution.
_VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: the process is up. Touches nothing."""
    return HealthResponse(status="ok", version=_VERSION)


@router.get("/ready", response_model=ReadyResponse)
async def ready(request: Request) -> JSONResponse:
    """Readiness: check the database and that migrations are at head.

    Each check is named in the body so a 503 states *which* one failed rather than
    a bare "not ready". The checker is bound on ``app.state`` by the app factory
    (``readiness_check``); the factory builds a real one from the stores' session
    factory on every deployment path.

    An unbound checker is not health. Before this was fixed, a missing checker
    reported both checks as ``ok`` -- a probe that never touched the database
    saying the database was fine, which is exactly what an orchestrator must not
    be told. A checker absent or one that returns a non-``ok`` state now yields
    ``503``, so the absence of a probe reads as "not ready" rather than "fine".

    Returns:
        ``200`` with ``{"status":"ready","checks":{...}}`` when every check
        passes, or ``503`` with the failing checks named.
    """
    checker = getattr(request.app.state, "readiness_check", None)
    if checker is None:
        # No checker at all: refuse to certify. The values are the same
        # vocabulary the built checker uses, so a probe reading the body sees a
        # state it can act on, not a bare "not ready".
        checks = {"database": "unconfigured", "migrations": "unconfigured"}
    else:
        checks = await checker()
    failed = {name: state for name, state in checks.items() if state != "ok"}
    if failed:
        body = ReadyResponse(status="not_ready", checks=checks)
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
    body = ReadyResponse(status="ready", checks=checks)
    return JSONResponse(status_code=200, content=body.model_dump(mode="json"))
