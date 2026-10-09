"""API process entry point.

Responsibility: the ``opspilot-api`` console script and ``python -m
opspilot.api`` -- run the ASGI app under uvicorn.

Layer: ``api``.

Host and port are read from the environment with defaults that are correct for
local development (``127.0.0.1:8000``). The token check happens inside
:func:`create_app`, so an unconfigured process exits here, before uvicorn binds a
socket -- it never serves one unauthenticated request.

``LOG_LEVEL`` is applied here, before the app is built and before uvicorn starts,
because this is the process entry point and the only place a logging policy
belongs (``opspilot.observability``). It is deliberately *not* in
:func:`create_app`: the factory is imported by tests and by the type generator,
and a module that configures logging when it is imported dictates policy to
whatever imported it. Uvicorn's own ``dictConfig`` runs after this and leaves the
root logger alone, so the level set here is the level the process serves with.
"""

from __future__ import annotations

import os

import uvicorn

from opspilot.api.app import create_app
from opspilot.observability import apply_log_level
from opspilot.settings import get_settings

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000


def main() -> None:
    """Run the API with uvicorn on the configured host and port.

    Reads ``API_HOST`` / ``API_PORT`` (falling back to the ``HOST`` / ``PORT``
    that most container platforms inject, then to the local defaults). A bad
    ``API_PORT`` raises a clear ``ValueError`` rather than silently binding the
    default, because "it started on the wrong port" is a worse failure to debug
    than a refused start.
    """
    apply_log_level(get_settings().log_level)
    app = create_app()
    host = os.environ.get("API_HOST") or os.environ.get("HOST") or _DEFAULT_HOST
    port = _resolve_port()
    uvicorn.run(app, host=host, port=port)


class _BadPort(ValueError):
    """``API_PORT`` is set but is not an integer."""

    def __init__(self, raw: str) -> None:
        super().__init__(f"API_PORT must be an integer, got {raw!r}")


def _resolve_port() -> int:
    """The port to bind, from ``API_PORT``/``PORT`` or the default."""
    raw = os.environ.get("API_PORT") or os.environ.get("PORT")
    if not raw:
        return _DEFAULT_PORT
    try:
        return int(raw)
    except ValueError as exc:
        raise _BadPort(raw) from exc


if __name__ == "__main__":
    main()
