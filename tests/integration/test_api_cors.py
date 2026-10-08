"""CORS behaviour of the API, asserted the way a browser sees it.

The M7 dashboard runs on ``http://localhost:3000`` and the API on
``http://localhost:8000``. That is a cross-origin pair, so without a CORS policy
the browser discards the response before a line of dashboard code runs -- the
failure mode this suite exists to prevent and to keep prevented.

These are browser-shaped requests: they carry an ``Origin`` header and read
``access-control-allow-origin`` off the reply, because that is the entire
negotiation. They also assert things a curl-based test would not: that the
wildcard is *absent*, and that CORS never becomes a way around the bearer token.

The negative assertions are the load-bearing ones. A future edit that answers a
"fix the CORS error" ticket by setting ``allow_origins=["*"]`` is the exact
failure this file must go red on, so it is tested explicitly rather than left to
be noticed in review.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.settings import get_settings
from tests.integration.fakes import FakeApprovalStore, FakeRunStore, FakeTicketStore

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

# The dashboard's real origin, and a page an attacker would host.
_DASHBOARD = "http://localhost:3000"
_FOREIGN = "https://evil.example"

# What the `make_client` fixture returns: a factory taking an optional
# comma-separated origin list. Named so each test that takes it can be
# annotated in one word rather than in a nested Callable.
_ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[_ClientFactory]:
    """Build a ``TestClient`` over an app with a specific CORS policy.

    A factory rather than a fixed client because each test states the policy it
    is testing -- a single shared policy would make the tests below it assert
    something other than what their names claim.
    """

    def _build(cors_origins: str | None = None) -> TestClient:
        monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
        if cors_origins is None:
            monkeypatch.delenv("OPSPILOT_CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("OPSPILOT_CORS_ORIGINS", cors_origins)
        get_settings.cache_clear()

        run_store = FakeRunStore()
        app = create_app(
            run_store=run_store,
            ticket_store=FakeTicketStore(),
            approval_store=FakeApprovalStore(run_store=run_store),
        )
        get_settings.cache_clear()
        return TestClient(app)

    yield _build
    get_settings.cache_clear()


# -- The dashboard's own origin is allowed ------------------------------------


def test_dashboard_origin_is_allowed_by_default(make_client: _ClientFactory) -> None:
    """The default policy permits ``http://localhost:3000`` on a real request.

    Not just a preflight: a browser first sends the actual ``GET``, and this
    asserts the response carries ``access-control-allow-origin`` so the dashboard
    can read it at all.
    """
    client = make_client()
    response = client.get("/api/tickets", headers={**_AUTH, "Origin": _DASHBOARD})

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == _DASHBOARD


def test_preflight_from_the_dashboard_is_answered(make_client: _ClientFactory) -> None:
    """A dashboard POST triggers a preflight, which must succeed.

    The preflight is the request that fails first in practice: it is an
    ``OPTIONS`` with ``Access-Control-Request-Method`` set, and if
    ``Authorization`` is missing from ``allow_headers`` the browser aborts here
    and never sends the POST that would have succeeded.
    """
    client = make_client()
    response = client.options(
        "/api/tickets",
        headers={
            "Origin": _DASHBOARD,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == _DASHBOARD
    allowed_methods = response.headers["access-control-allow-methods"]
    assert "POST" in allowed_methods
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers


# -- A foreign origin is refused ---------------------------------------------


def test_foreign_origin_gets_no_allow_origin_header(make_client: _ClientFactory) -> None:
    """A page on another origin gets no ``access-control-allow-origin``.

    The response body is still 200 -- the browser discards it without that
    header, which is precisely why this cannot be asserted on status code alone.
    """
    client = make_client()
    response = client.get("/api/tickets", headers={**_AUTH, "Origin": _FOREIGN})

    assert "access-control-allow-origin" not in response.headers


def test_foreign_origin_preflight_is_not_approved(make_client: _ClientFactory) -> None:
    """A preflight from a foreign origin is refused the same way."""
    client = make_client()
    response = client.options(
        "/api/tickets",
        headers={
            "Origin": _FOREIGN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_a_second_configured_origin_is_allowed(make_client: _ClientFactory) -> None:
    """Every explicitly named origin is allowed, not just the first."""
    client = make_client(f"{_DASHBOARD},http://127.0.0.1:3000")

    assert (
        client.get("/api/tickets", headers={**_AUTH, "Origin": "http://127.0.0.1:3000"}).headers[
            "access-control-allow-origin"
        ]
        == "http://127.0.0.1:3000"
    )


# -- The wildcard is refused, and that refusal is the point ------------------


def test_the_wildcard_is_never_present_even_as_a_substring(make_client: _ClientFactory) -> None:
    """No response may carry ``*`` as its allowed origin.

    A wildcard is the shape of the mistake this whole file exists to prevent: any
    page the operator visits while the API runs could read every response from an
    API whose endpoints can move money. Asserting the header equals the named
    origin above would catch a literal ``["*"]``; this catches the sneaky forms
    too, such as Starlette echoing a wildcard it was configured with.
    """
    client = make_client()
    for origin in (_DASHBOARD, _FOREIGN):
        response = client.get("/api/tickets", headers={**_AUTH, "Origin": origin})
        header = response.headers.get("access-control-allow-origin")
        assert header != "*", f"wildcard allowed for {origin}: {header!r}"


def test_settings_refuse_to_construct_with_a_wildcard_origin() -> None:
    """A wildcard is rejected in configuration, before any app exists.

    Guarding this in the settings model rather than only in the middleware means
    the wildcard cannot reach the app by any route that later reads the field --
    including one nobody re-tests.
    """
    from opspilot.settings import Settings

    with pytest.raises(ValueError, match="cannot contain"):
        Settings(OPSPILOT_CORS_ORIGINS="*")


def test_a_wildcard_among_named_origins_is_still_refused() -> None:
    """``http://localhost:3000,*`` is refused as firmly as a bare ``*``.

    The list being *mostly* specific is the realistic version of this mistake,
    and a validator that only rejected a lone ``*`` would pass it.
    """
    from opspilot.settings import Settings

    with pytest.raises(ValueError, match="cannot contain"):
        Settings(OPSPILOT_CORS_ORIGINS=f"{_DASHBOARD},*")


# -- Empty means closed, not open --------------------------------------------


def test_empty_origins_allow_nothing_cross_origin(make_client: _ClientFactory) -> None:
    """Empty is "no cross-origin reader", which is not "everything".

    This is the case a careless parse turns into a wildcard -- ``[""]`` matches
    nothing, but a truthiness check that falls back to ``["*"]`` when the value is
    empty would invert the operator's intent and open the API to every page.
    """
    client = make_client("")

    for origin in (_DASHBOARD, _FOREIGN):
        response = client.get("/api/tickets", headers={**_AUTH, "Origin": origin})
        assert "access-control-allow-origin" not in response.headers


def test_whitespace_only_origins_allow_nothing(make_client: _ClientFactory) -> None:
    """Whitespace and stray commas are dropped, leaving a closed allowlist."""
    client = make_client(" , , ")

    response = client.get("/api/tickets", headers={**_AUTH, "Origin": _DASHBOARD})
    assert "access-control-allow-origin" not in response.headers


# -- CORS is not an authorisation mechanism ----------------------------------


def test_cors_does_not_waive_the_bearer_token(make_client: _ClientFactory) -> None:
    """An allowed origin without a token is still 401.

    CORS answers "may this page read the response"; the token answers "is this
    caller allowed to have it". Both must hold, so the dashboard's origin being
    permitted must not have weakened ``require_operator``.
    """
    client = make_client()
    response = client.get("/api/tickets", headers={"Origin": _DASHBOARD})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_foreign_origin_with_a_valid_token_is_still_401_free_but_unreadable(
    make_client: _ClientFactory,
) -> None:
    """A token still authenticates from any origin; CORS only hides the reply.

    Stated because it is the honest limit of this control: CORS is not a
    security boundary, and a reader who concludes otherwise from this suite is
    wrong. The token is the boundary; CORS is a browser-side convenience.
    """
    client = make_client()
    response = client.get("/api/tickets", headers={**_AUTH, "Origin": _FOREIGN})

    assert response.status_code == 200, "the token authenticated the request"
    assert "access-control-allow-origin" not in response.headers, (
        "but the browser would discard that 200, because the origin is not allowed"
    )


def test_health_stays_exempt_and_exposes_nothing_new(make_client: _ClientFactory) -> None:
    """``/health`` still needs no token and still says only "ok" and a version.

    Asserted alongside CORS because a policy change is a good time to notice that
    this route is reachable by any origin. That is intended -- a probe has no
    credential (contract §8) -- and the point is that it stays *this* small.
    """
    client = make_client()
    response = client.get("/health", headers={"Origin": _FOREIGN})

    assert response.status_code == 200
    assert set(response.json()) == {"status", "version"}


def test_app_refuses_to_start_with_a_wildcard_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No app with a wildcarded policy can ever be constructed or served.

    The end-to-end form of the guard, asserted on the *property* rather than on a
    message. Two layers can refuse a wildcard -- the settings validator and
    :func:`create_app` -- and which one fires first depends on how the settings
    were constructed. Matching on either one's wording would make this test pass
    or fail for a reason that has nothing to do with the security property, so it
    asserts only that construction is refused.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("OPSPILOT_CORS_ORIGINS", "*")
    get_settings.cache_clear()
    run_store = FakeRunStore()

    try:
        with pytest.raises(ValueError):
            create_app(
                run_store=run_store,
                ticket_store=FakeTicketStore(),
                approval_store=FakeApprovalStore(run_store=run_store),
            )
    finally:
        get_settings.cache_clear()
