"""FastAPI application factory.

Responsibility: build the app -- mount routers, install the auth dependency,
register the exception handlers, and wire concrete adapters to the ports. This
is the composition root for the API process.

Layer: ``api``. The only place in the API that chooses adapters -- the routers
receive ports.

Startup behaviour: the app refuses to start when ``OPSPILOT_OPERATOR_TOKEN`` is
unset, rather than defaulting to open. A default-open auth is worse than no auth
because it looks like auth.
"""

from __future__ import annotations


def create_app() -> object:
    """Construct and return the configured FastAPI application. M0 stub."""
    raise NotImplementedError
