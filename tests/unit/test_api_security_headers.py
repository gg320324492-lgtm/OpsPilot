"""Unit tests for the API's default security headers.

``SecurityHeadersMiddleware`` is small, so the tests are small too -- but each
one pins a header a regression would silently drop, because a middleware that
stops setting a header fails no route and breaks no schema. The only signal
that it regressed is these tests.

The cache-control assertion is scoped to ``/api/`` on purpose: the two health
probes are on the hot path of every compose healthcheck and CI wait loop, and
forcing ``no-store`` on them would be a deployment-behaviour change dressed as
a security one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opspilot.api.headers import SecurityHeadersMiddleware


@pytest.fixture
def app() -> FastAPI:
    """A minimal app with the middleware installed and one route per surface."""
    application = FastAPI()
    application.add_middleware(SecurityHeadersMiddleware)

    @application.get("/api/things")
    def things() -> dict[str, str]:
        return {"ok": "true"}

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def test_api_responses_carry_the_static_headers(app: FastAPI) -> None:
    client = TestClient(app)
    response = client.get("/api/things")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_api_responses_are_not_stored(app: FastAPI) -> None:
    client = TestClient(app)
    response = client.get("/api/things")
    assert response.headers["cache-control"] == "private, no-store"


def test_health_carries_the_static_headers_but_no_cache_directive(app: FastAPI) -> None:
    """The probes keep the framing/sniffing defaults and no cache header.

    ``/health`` carries no credential, so it has nothing to keep out of a
    cache; adding ``no-store`` there would be a policy the deployment did not
    ask for.
    """
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "cache-control" not in response.headers


def test_a_handler_set_header_wins_over_the_default(app: FastAPI) -> None:
    """A route that sets its own value is not overridden.

    The middleware is a default, not an override: a future endpoint that has a
    reason to cache must be able to say so without unwinding the middleware.
    """

    @app.get("/api/cached")
    def cached() -> dict[str, str]:
        return {"ok": "true"}

    @app.middleware("http")
    async def set_cache_header(request: object, call_next: object) -> object:
        response = await call_next(request)  # type: ignore[operator]
        response.headers["cache-control"] = "public, max-age=60"
        return response

    client = TestClient(app)
    response = client.get("/api/cached")
    assert response.headers["cache-control"] == "public, max-age=60"
    # ...and the static headers are still there.
    assert response.headers["x-frame-options"] == "DENY"


def test_the_installed_app_sends_the_headers_on_a_guarded_route() -> None:
    """The real ``create_app`` wiring sends them too, not just the toy app.

    The fixture above proves the middleware's own behaviour; this proves the
    app factory actually installs it, which is the thing a future edit to
    ``create_app`` could silently drop.
    """
    import os

    from opspilot.api.app import create_app
    from opspilot.settings import get_settings
    from tests.integration.fakes import (
        FakeApprovalStore,
        FakeRunStore,
        FakeTicketIntake,
        FakeTicketStore,
    )

    os.environ["OPSPILOT_OPERATOR_TOKEN"] = "headers-installation-check-token"  # noqa: S105 -- a fixture value, not a credential
    get_settings.cache_clear()
    try:
        run_store = FakeRunStore()
        ticket_store = FakeTicketStore()
        application = create_app(
            run_store=run_store,
            ticket_store=ticket_store,
            ticket_intake=FakeTicketIntake(tickets=ticket_store, runs=run_store),
            approval_store=FakeApprovalStore(run_store=run_store),
        )
    finally:
        get_settings.cache_clear()
    client = TestClient(application)
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
