"""The audit ledger must be readable by the operator who has to answer for it.

## Why this file exists

M12 gave a failed tool call its own audit event type, ``tool_failed``, instead of
writing ``tool_executed`` with an ``ok: false`` payload -- an event name that
asserted the opposite of its own contents. ``agents/runtime.py`` says the split
"makes 'one failed write' a query rather than an inference", and the rows are
there: ``audit_events`` holds them, verified against the Postgres stack.

**Nothing could query them.** The only readers of ``audit_events`` under ``src/``
were the eval harness and the tests; every API route read steps, tool calls,
citations and approvals, and none read the ledger. So the operator-visible surface
was: a run-detail payload with a ``failed_tool_calls`` summary, and nothing that
answers "everything of record that happened to this run". To read the ledger an
operator had to open ``psql`` and write the query by hand -- which makes the claim
in the runtime's comment true of the database and false of the deployment.

``GET /api/runs/{run_id}/audit`` is that query, and this file is what keeps it
one. What is asserted:

- a run whose refund was refused serves that refusal, with its ``error`` code,
  through the API (the happy path);
- the ledger is in the order it was written, and carries the events no other
  endpoint shows (``model_called``, ``approval_requested``);
- the route is guarded like every other ``/api`` route (401 without a token);
- a run with nothing on its ledger gets an empty list, and a run id that does not
  exist gets the 404 its sibling endpoints give -- an empty list there would say
  "this run's ledger is empty" about a run that never existed, which is the
  plausible-lie failure this project keeps having to name.

The store is the real ``SqlRunStore`` over a real schema, never a fake:
``tests/integration/fakes.py`` implements every read the runs router probes for,
so a fake-backed test would pass against a store that could not answer -- which is
the exact shape of the defect this endpoint fixes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
)
from opspilot.api.app import create_app
from opspilot.domain.runs import AgentRun
from opspilot.settings import Settings, get_settings

# The reproduction is imported, not copied. ``test_failed_tool_call_visibility``
# already builds the exact run this endpoint exists for -- park at gate 5, approve
# through the real store, drain, refund refused -- through the real stores and the
# real runtime. A second copy here would be a second thing to keep true about how
# a failed write is produced, and the two would drift.
from tests.integration.test_failed_tool_call_visibility import _failed_refund_run, _new_run

_TOKEN = "audit-endpoint-test-token"  # noqa: S105 -- a fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


def _client(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app, bound to the real SQL stores over the test database.

    All three stores are passed explicitly so nothing reaches for the process's
    own ``DATABASE_URL``: a factory built from settings here would query a
    different database from the one the run was written to, and the endpoint would
    answer from an empty ledger.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    get_settings.cache_clear()
    try:
        app = create_app(
            run_store=SqlRunStore(factory),
            ticket_store=SqlTicketStore(factory),
            approval_store=SqlApprovalStore(factory),
        )
        return TestClient(app)
    finally:
        get_settings.cache_clear()


def _build_failed_run(factory: sessionmaker[Session]) -> AgentRun:
    """The live reproduction in miniature, driven on the production path.

    ``asyncio.run`` rather than an async test because the assertions below are
    HTTP ones and ``TestClient`` owns its own event loop: a test that tried to do
    both in one loop would be testing the client's plumbing, not the endpoint.
    """
    run, _gateway = asyncio.run(_failed_refund_run(factory))
    return run


def _build_clean_run(factory: sessionmaker[Session]) -> AgentRun:
    """A run that has been created and not driven: no step, no tool call, no event."""
    return asyncio.run(_new_run(factory))


# ---------------------------------------------------------------------------
# The happy path: a refused write is on the ledger, and the API serves it
# ---------------------------------------------------------------------------


def test_a_failed_write_is_served_from_the_ledger(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``tool_failed`` row is returned, payload intact, through the API.

    This is the assertion the whole endpoint turns on. The rows existed before;
    what did not exist was a way for the operator to read them without a database
    client. The payload's ``error`` is the load-bearing field -- ``invalid_state``
    is what the billing server answered, and it is the difference between "the
    refund was refused because it had already happened" and "the refund was lost".
    """
    run = _build_failed_run(factory)
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{run.id}/audit", headers=_AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(run.id)

    failed = [event for event in body["events"] if event["event_type"] == "tool_failed"]
    assert len(failed) == 1, f"expected exactly one tool_failed event, got {failed}"
    event = failed[0]
    assert set(event) == {"id", "event_type", "created_at", "actor", "payload"}
    assert event["actor"] == "runtime"
    assert event["payload"]["tool_name"] == "billing.issue_refund"
    assert event["payload"]["ok"] is False
    assert event["payload"]["error"] == "invalid_state"
    assert event["payload"]["permission"] == "high_risk_write"


def test_the_ledger_is_in_the_order_it_was_written(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Events come back oldest first, which is the order the ledger is read in.

    A ledger read newest-first is a log tail; the question an operator asks of it
    ("what happened, and in what order") is answered by the sequence. Asserted on
    the timestamps rather than on the event types so the claim is about ordering
    and not about which events this particular run happens to write.
    """
    run = _build_failed_run(factory)
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{run.id}/audit", headers=_AUTH)

    stamps = [event["created_at"] for event in response.json()["events"]]
    assert len(stamps) > 1, "the run must have written more than one event to be ordered"
    assert stamps == sorted(stamps)


def test_the_ledger_carries_events_no_other_endpoint_shows(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``model_called`` is on the ledger and nowhere else -- the division of labour.

    ``/trace`` is the execution timeline and ``/runs/{id}`` is the run detail;
    neither lists the audit events. This is what makes the two endpoints
    different reads rather than two renderings of one, and it is asserted so a
    future "simplification" that returns steps from this route cannot pass.
    """
    run = _build_failed_run(factory)
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{run.id}/audit", headers=_AUTH)

    types = {event["event_type"] for event in response.json()["events"]}
    assert "model_called" in types, (
        f"model_called is written by every real model call and belongs to no other "
        f"endpoint; the ledger returned {sorted(types)}"
    )


def test_the_ledger_records_the_approval_the_operator_granted(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parked refund is an ``approval_requested`` event -- who authorised what.

    The approval request is the record of a human decision being *asked for*, and
    it is the one event a trace step does not carry: the parking is visible as a
    status change, the request is not. Without this, the ledger would show a
    refund attempt with nothing about the authorisation that preceded it.
    """
    run = _build_failed_run(factory)
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{run.id}/audit", headers=_AUTH)

    requested = [e for e in response.json()["events"] if e["event_type"] == "approval_requested"]
    assert requested, "parking for approval must be an audit event"
    assert requested[0]["payload"]["tool_name"] == "billing.issue_refund"


# ---------------------------------------------------------------------------
# Auth, emptiness and the 404
# ---------------------------------------------------------------------------


def test_the_ledger_needs_a_token(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``Authorization`` header -> 401 with the error envelope (contract §8).

    The ledger records every action a run took, including the ones that moved
    money; it is guarded by the same router-level ``require_operator`` as every
    other ``/api`` route, and asserted here so a route added without that
    inheritance fails rather than shipping an open read of the ledger.
    """
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{uuid4()}/audit")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_run_with_no_events_returns_an_empty_list(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that has written nothing gets ``{"events": []}``, not an error.

    The run exists and its ledger is genuinely empty: it was created and never
    driven. An empty list is the correct answer -- the alternative, a 404, would
    report a run the dashboard just created as missing.
    """
    run = _build_clean_run(factory)
    client = _client(factory, monkeypatch)

    response = client.get(f"/api/runs/{run.id}/audit", headers=_AUTH)

    assert response.status_code == 200, response.text
    assert response.json() == {"run_id": str(run.id), "events": []}


def test_an_unknown_run_is_404_not_an_empty_ledger(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run id that does not exist is ``run_not_found``, not an empty list.

    This is the deliberate difference from the case above. "This run was created
    and has recorded nothing" and "there is no such run" are different statements,
    and only one of them is a mistake by the caller. Returning an empty list for
    both would make a mistyped id indistinguishable from a run that had not
    started -- the plausible-lie shape, one layer down from the empty page.
    """
    client = _client(factory, monkeypatch)
    missing = uuid4()

    response = client.get(f"/api/runs/{missing}/audit", headers=_AUTH)

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "run_not_found"
    assert body["error"]["details"]["run_id"] == str(missing)


def test_the_real_store_answers_the_probe_the_route_makes(
    factory: sessionmaker[Session],
) -> None:
    """``SqlRunStore`` really has ``list_audit_events``.

    The router reaches the ledger through the same optional-method probe the other
    run reads use, and a store that does not implement it degrades to an empty
    list -- the documented behaviour, and the reason the fakes in
    ``tests/integration/fakes.py`` cannot test this file's happy path. This is the
    guard that the production object is not the one being degraded: without it,
    deleting the method from ``SqlRunStore`` would leave every test above passing
    on a route that answers ``[]`` forever.
    """
    run = _build_failed_run(factory)

    rows = asyncio.run(SqlRunStore(factory).list_audit_events(run.id))

    assert rows, "the store returned no audit rows for a run that wrote several"
    assert {row.event_type for row in rows} >= {"tool_failed", "model_called"}
    failed = next(row for row in rows if row.event_type == "tool_failed")
    assert failed.payload["error"] == "invalid_state"


def test_the_ledger_read_is_scoped_to_one_run(factory: sessionmaker[Session]) -> None:
    """A second run's events do not appear on the first run's page.

    ``audit_events.run_id`` is nullable because auth and reindex events have no
    run, so the read that forgets its ``WHERE`` clause would return every row in
    the ledger -- including events belonging to runs the caller has no business
    reading. Asserted with two real runs so the filter is exercised rather than
    assumed.
    """
    first = _build_failed_run(factory)
    second = _build_failed_run(factory)

    rows = asyncio.run(SqlRunStore(factory).list_audit_events(first.id))

    assert rows
    ids = {str(row.id) for row in rows}
    other = asyncio.run(SqlRunStore(factory).list_audit_events(second.id))
    assert not ids & {str(row.id) for row in other}, (
        "the two runs' ledgers overlap; the read is not scoped to one run"
    )
