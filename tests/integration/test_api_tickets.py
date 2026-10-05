"""Integration tests for the tickets API.

These are the M1 tests for ``POST /api/tickets`` and its neighbours. They run
against a FastAPI ``TestClient`` with an in-memory fake store injected through
``create_app``, so they exercise the routers -- which is what this milestone
delivers -- without depending on the persistence layer being finished.

The assertions that matter:

- the create response is 201 with both a ``ticket`` and a ``run``, and the run is
  exactly ``received``;
- the handler started nothing -- no worker call, no tool call, no model call;
- a failure mid-transaction leaves no ticket;
- missing/wrong bearer token is 401, and ``/health`` needs none;
- a validation failure speaks the single error envelope, not FastAPI's default
  ``{"detail": ...}``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.api.auth import MissingOperatorToken
from opspilot.domain.runs import RunStatus
from opspilot.settings import get_settings
from tests.integration.fakes import FakeApprovalStore, FakeRunStore, FakeTicketStore

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def stores() -> tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]:
    """The three fake stores, wired to share a run store for the approval flow."""
    run_store = FakeRunStore()
    return FakeTicketStore(), run_store, FakeApprovalStore(run_store=run_store)


@pytest.fixture
def client(
    stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A ``TestClient`` over an app whose stores are the in-memory fakes.

    Settings are overridden before the app is built so the startup token check
    sees a configured token; ``get_settings`` is cache-cleared on teardown so one
    test's token cannot leak into another's.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("MODEL_NAME", "fake-model")
    get_settings.cache_clear()

    ticket_store, run_store, approval_store = stores
    # The fakes satisfy the ``RunStore``/``TicketStore``/``ApprovalStore``
    # Protocols structurally, so no cast is needed here.
    app = create_app(
        run_store=run_store,
        ticket_store=ticket_store,
        approval_store=approval_store,
    )
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


# -- POST /api/tickets -------------------------------------------------------


def test_create_ticket_returns_201_with_ticket_and_received_run(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """The happy path: 201, a ticket, and a run whose status is exactly ``received``."""
    response = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={
            "subject": "Charged twice for invoice INV-2026-384",
            "body": "We were charged twice for invoice INV-2026-384.",
            "customer_email": "billing@acme.example",
            "external_id": "ZD-55102",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"ticket", "run"}
    assert body["ticket"]["subject"] == "Charged twice for invoice INV-2026-384"
    assert body["ticket"]["external_id"] == "ZD-55102"
    assert body["ticket"]["customer_email"] == "billing@acme.example"
    assert body["run"]["status"] == "received"

    _, run_store, _ = stores
    stored = run_store.rows[next(iter(run_store.rows))]
    assert stored.status is RunStatus.RECEIVED


def test_create_ticket_does_not_start_the_run(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """The handler enqueues; it must not invoke the worker or execute a tool.

    This is the contract's central claim about ``POST /api/tickets`` (§2): the run
    is picked up by the worker's next poll, not by the request.
    """
    _, run_store, _ = stores
    response = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={
            "subject": "s",
            "body": "b",
            "customer_email": "c@example.com",
        },
    )
    assert response.status_code == 201, response.text

    run_id = response.json()["run"]["id"]
    stored = run_store.rows[next(iter(run_store.rows))]
    assert stored.id.__str__() == run_id
    # Nothing advanced the run past RECEIVED...
    assert stored.status is RunStatus.RECEIVED
    # ...and nothing called into the worker or a tool.
    assert run_store.worker_spy.call_count == 0, run_store.worker_spy.calls
    assert run_store.tool_spy.call_count == 0, run_store.tool_spy.calls


def test_create_ticket_rolls_back_when_the_run_insert_fails(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """Both rows land or neither does: a failed run insert leaves no ticket."""
    ticket_store, run_store, _ = stores
    run_store.fail_create = True

    with pytest.raises(RuntimeError, match="run insert failed"):
        client.post(
            "/api/tickets",
            headers=_AUTH,
            json={"subject": "s", "body": "b", "customer_email": "c@example.com"},
        )

    assert ticket_store.rows == {}, "a ticket survived a failed run insert"
    assert run_store.rows == {}


def test_missing_token_is_401(client: TestClient) -> None:
    """No ``Authorization`` header -> 401 with the error envelope."""
    response = client.post(
        "/api/tickets",
        json={"subject": "s", "body": "b", "customer_email": "c@example.com"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_401(client: TestClient) -> None:
    """A wrong token -> 401, not 200."""
    response = client.get("/api/tickets", headers={"Authorization": "Bearer not-the-token"})
    assert response.status_code == 401
    assert "error" in response.json()


def test_health_needs_no_token(client: TestClient) -> None:
    """``/health`` is exempt from auth (contract §8)."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_app_refuses_to_start_without_a_token(
    stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset token refuses startup -- a default-open auth is worse than none.

    This is the assertion that the check is a startup check, not a per-request
    one: ``create_app`` itself raises rather than building an app that answers
    401 to every request.
    """
    monkeypatch.delenv("OPSPILOT_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "")
    get_settings.cache_clear()
    ticket_store, run_store, approval_store = stores

    with pytest.raises(MissingOperatorToken):
        create_app(
            run_store=run_store,
            ticket_store=ticket_store,
            approval_store=approval_store,
        )
    get_settings.cache_clear()


def test_validation_failure_uses_the_error_envelope(client: TestClient) -> None:
    """A bad body returns the single envelope with ``code``, not ``{"detail":...}``."""
    response = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={"subject": "missing the rest"},
    )
    assert response.status_code == 422
    body = response.json()
    assert "detail" not in body, "FastAPI's default validation shape leaked through"
    assert body["error"]["code"] == "schema_invalid"
    assert isinstance(body["error"]["details"]["errors"], list)


def test_get_ticket_returns_its_runs(client: TestClient) -> None:
    """``GET /api/tickets/{id}`` returns the ticket and the run created for it."""
    created = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={"subject": "s", "body": "b", "customer_email": "c@example.com"},
    ).json()
    ticket_id = created["ticket"]["id"]

    response = client.get(f"/api/tickets/{ticket_id}", headers=_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["ticket"]["id"] == ticket_id
    assert [run["id"] for run in body["runs"]] == [created["run"]["id"]]


def test_get_unknown_ticket_is_404_with_code(client: TestClient) -> None:
    """An unknown ticket is 404 ``ticket_not_found`` -- a code, not a bare 404."""
    response = client.get("/api/tickets/00000000-0000-0000-0000-000000000000", headers=_AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ticket_not_found"


def test_create_run_for_existing_ticket_is_201_received(client: TestClient) -> None:
    """``POST /api/runs`` enqueues another run for an existing ticket."""
    created = client.post(
        "/api/tickets",
        headers=_AUTH,
        json={"subject": "s", "body": "b", "customer_email": "c@example.com"},
    ).json()

    response = client.post("/api/runs", headers=_AUTH, json={"ticket_id": created["ticket"]["id"]})
    assert response.status_code == 201
    assert response.json()["status"] == "received"


def test_list_runs_filters_by_status(client: TestClient, stores: tuple[object, ...]) -> None:
    """``GET /api/runs?status=received`` returns the enqueued runs."""
    client.post(
        "/api/tickets",
        headers=_AUTH,
        json={"subject": "s", "body": "b", "customer_email": "c@example.com"},
    )
    response = client.get("/api/runs?status=received", headers=_AUTH)
    assert response.status_code == 200
    assert response.json()["total"] == 1

    empty = client.get("/api/runs?status=completed", headers=_AUTH)
    assert empty.json()["total"] == 0


def test_list_runs_rejects_a_bad_status(client: TestClient) -> None:
    """An unknown status filter is 400 ``validation_error``."""
    response = client.get("/api/runs?status=banana", headers=_AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_ready_reports_ok_without_a_checker(client: TestClient) -> None:
    """``/ready`` is 200 with both checks named when no checker is bound."""
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "migrations": "ok"}
