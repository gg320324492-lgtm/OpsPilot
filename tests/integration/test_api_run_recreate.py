"""Integration tests for ``POST /api/runs/{run_id}/recreate`` (Phase 3 M2-03).

These run against a ``TestClient`` with in-memory fakes injected through
``create_app``, the same shape as ``test_api_approvals.py``. The assertions are
the ones ``docs/api-contract.md`` §3.3 makes:

- a terminal run re-creates into a *new* ``RECEIVED`` run on the same ticket;
- the response states the original's write status, computed from the original's
  tool-call rows -- ``executed`` / ``not_executed`` / ``unknown``;
- a non-terminal run is refused with 409 ``run_not_recreatable`` -- the guard
  that keeps two agents off one ticket and a parked approval undisturbed;
- the new run inherits no approval and no executed refund row: the follow-up
  path exists so a human can *re-decide*, and a copied decision would make the
  button a replay of whatever the original did;
- an unknown run is 404 ``run_not_found``;
- a second re-create is a second sibling run, not a dedupe -- each call is one
  human decision to look again (contract §9).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.settings import get_settings
from tests.integration.fakes import FakeRunStore, FakeTicketIntake, FakeTicketStore, ToolCallRow

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


class _RunStoreWithAudit(FakeRunStore):
    """A ``FakeRunStore`` that can hand out audit recorders, as the SQL one does.

    The router probes for ``audit_recorder_for`` with ``getattr``; the bare
    fake does not offer it, which is exactly how the "store without an audit
    writer still serves the endpoint" case is exercised. This subclass offers
    it so the event-writing path is asserted too. The recorder is a stub with
    the one method the router calls; the events land in ``self.audit_events``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.audit_events: list[tuple[UUID, str, str, dict[str, object]]] = []

    def audit_recorder_for(self, run_id: UUID) -> Any:  # noqa: ANN401 -- test double
        run_store = self

        class _Recorder:
            async def record_audit(
                self, *, event_type: str, actor: str, payload: dict[str, object] | None = None
            ) -> UUID:
                event_id = uuid4()
                run_store.audit_events.append((run_id, event_type, actor, dict(payload or {})))
                return event_id

        return _Recorder()


def _finished_run(
    run_store: FakeRunStore,
    *,
    status: RunStatus = RunStatus.FAILED,
    ticket_id: UUID | None = None,
) -> AgentRun:
    """Insert a terminal run and return it."""
    run = AgentRun(
        id=uuid4(),
        ticket_id=ticket_id or uuid4(),
        status=status,
        model_provider="fake",
        model_name="fake-model",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    run_store.rows[run.id] = run
    return run


def _refund_row(
    run_store: FakeRunStore,
    run: AgentRun,
    *,
    status: ToolCallStatus,
) -> ToolCallRow:
    """Insert a ``billing.issue_refund`` tool-call row on ``run``."""
    row = ToolCallRow(
        run_id=run.id,
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": 129.0},
    )
    row.permission = Permission.HIGH_RISK_WRITE
    row.status = status
    row.idempotency_key = f"refund:{run.id}:TX-88219"
    if status is ToolCallStatus.EXECUTED:
        row.result = {"refund_id": "R-1", "replayed": False}
    run_store.tool_calls.setdefault(run.id, []).append(row)
    return row


@pytest.fixture
def stores() -> tuple[FakeTicketStore, _RunStoreWithAudit]:
    """The fake stores the app is built from."""
    return FakeTicketStore(), _RunStoreWithAudit()


@pytest.fixture
def client(
    stores: tuple[FakeTicketStore, _RunStoreWithAudit],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeTicketStore, _RunStoreWithAudit]]:
    """A ``TestClient`` over the real router with the fakes bound."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    get_settings.cache_clear()
    tickets, runs = stores
    app = create_app(
        run_store=runs,
        ticket_store=tickets,
        ticket_intake=FakeTicketIntake(tickets, runs),
    )
    with TestClient(app) as test_client:
        yield test_client, tickets, runs
    get_settings.cache_clear()


def test_a_failed_run_recreates_into_a_new_received_run_on_the_same_ticket(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """The happy path: 201, a new RECEIVED run, same ticket, sibling not clone."""
    test_client, _, run_store = client
    original = _finished_run(run_store, status=RunStatus.FAILED)

    response = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["original_run_id"] == str(original.id)
    new_ref = body["run"]
    assert new_ref["id"] != str(original.id)
    assert new_ref["status"] == "received"

    stored = run_store.rows[UUID(new_ref["id"])]
    assert stored.status is RunStatus.RECEIVED
    assert stored.ticket_id == original.ticket_id


def test_the_response_states_the_original_write_status(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """``original_write_status`` is computed from the original's own rows."""
    test_client, _, run_store = client

    executed = _finished_run(run_store)
    _refund_row(run_store, executed, status=ToolCallStatus.EXECUTED)
    response = test_client.post(f"/api/runs/{executed.id}/recreate", headers=_AUTH)
    assert response.json()["original_write_status"] == "executed"

    refused = _finished_run(run_store)
    _refund_row(run_store, refused, status=ToolCallStatus.REJECTED)
    response = test_client.post(f"/api/runs/{refused.id}/recreate", headers=_AUTH)
    assert response.json()["original_write_status"] == "not_executed"

    untouched = _finished_run(run_store)
    response = test_client.post(f"/api/runs/{untouched.id}/recreate", headers=_AUTH)
    assert response.json()["original_write_status"] == "not_executed"


def test_a_dangling_proposed_refund_is_unknown(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """The C2 window: a dangling ``proposed`` refund row -> ``unknown``.

    A run that died between the proposal and its gate outcome -- or in the C2
    window, dispatched and lost before the row committed -- leaves a refund row
    that cannot say whether the money moved. ``not_executed`` would tell the
    operator the re-run is safe when the billing system may say otherwise, so
    ``unknown`` is the answer that sends them to check.
    """
    test_client, _, run_store = client
    original = _finished_run(run_store)
    _refund_row(run_store, original, status=ToolCallStatus.PROPOSED)

    response = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    assert response.status_code == 201
    assert response.json()["original_write_status"] == "unknown"


def test_refused_and_undecided_refunds_are_not_executed(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """Gate refusals and an undecided park moved nothing -> ``not_executed``.

    ``rejected`` was refused at a gate and never dispatched; ``failed`` was
    dispatched and refused by the server atomically; ``awaiting_approval`` was
    parked and never decided. None of the three moved money, and the answer
    says so rather than hedging into ``unknown`` -- the hedge is reserved for
    the one state where the evidence genuinely cannot settle it.
    """
    test_client, _, run_store = client
    for status in (
        ToolCallStatus.REJECTED,
        ToolCallStatus.AWAITING_APPROVAL,
        ToolCallStatus.FAILED,
    ):
        original = _finished_run(run_store)
        _refund_row(run_store, original, status=status)
        response = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
        assert response.status_code == 201, (status, response.text)
        assert response.json()["original_write_status"] == "not_executed", status


def test_a_non_terminal_run_is_refused_with_409_run_not_recreatable(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """Only a terminal run can be re-created -- every other status is a 409.

    The parked case is the one that costs the most to get wrong: a run in
    ``WAITING_APPROVAL`` has a live human decision pending, and re-creating
    under it would put a second agent on the ticket while the first decision is
    still open.
    """
    test_client, _, run_store = client
    for status in (
        RunStatus.RECEIVED,
        RunStatus.CLASSIFYING,
        RunStatus.RETRIEVING,
        RunStatus.PLANNING,
        RunStatus.EXECUTING,
        RunStatus.WAITING_APPROVAL,
        RunStatus.RESPONDING,
    ):
        run = _finished_run(run_store, status=status)
        run.completed_at = None
        response = test_client.post(f"/api/runs/{run.id}/recreate", headers=_AUTH)
        assert response.status_code == 409, (status, response.text)
        assert response.json()["error"]["code"] == "run_not_recreatable"
        assert UUID(response.json()["error"]["details"]["run_id"]) == run.id


def test_the_new_run_inherits_no_approval_and_no_refund_row(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """The follow-up is a re-decision, not a replay: the new run starts clean.

    The original holds an executed refund row; the new run must carry neither
    that row nor any approval. Anything else would mean the button re-did
    whatever the original did without the gates and the human the state machine
    requires -- which is exactly the shortcut the feature exists to avoid.
    """
    test_client, _, run_store = client
    original = _finished_run(run_store, status=RunStatus.COMPLETED)
    _refund_row(run_store, original, status=ToolCallStatus.EXECUTED)

    response = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    assert response.status_code == 201
    new_id = UUID(response.json()["run"]["id"])

    assert run_store.tool_calls.get(new_id, []) == []
    assert run_store.tool_calls.get(original.id)  # the original's rows are intact


def test_an_unknown_run_is_404_run_not_found(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """A run id that does not exist is the contract's 404 ``run_not_found``."""
    test_client, _, _ = client
    missing = uuid4()
    response = test_client.post(f"/api/runs/{missing}/recreate", headers=_AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_a_second_recreate_is_a_second_sibling_run(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """No dedupe: each call is one human decision to look again (§9)."""
    test_client, _, run_store = client
    original = _finished_run(run_store)

    first = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    second = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["run"]["id"] != second.json()["run"]["id"]

    ticket_runs = [run for run in run_store.rows.values() if run.ticket_id == original.ticket_id]
    assert {run.id for run in ticket_runs} == {
        original.id,
        UUID(first.json()["run"]["id"]),
        UUID(second.json()["run"]["id"]),
    }


def test_the_recreate_writes_one_audit_event_against_the_new_run(
    client: tuple[TestClient, FakeTicketStore, _RunStoreWithAudit],
) -> None:
    """The ledger records the action on the run it acted on -- the new one."""
    test_client, _, run_store = client
    original = _finished_run(run_store)

    response = test_client.post(f"/api/runs/{original.id}/recreate", headers=_AUTH)
    assert response.status_code == 201
    new_id = UUID(response.json()["run"]["id"])

    events = [event for event in run_store.audit_events if event[0] == new_id]
    assert len(events) == 1, run_store.audit_events
    _, event_type, actor, payload = events[0]
    assert event_type == "run_recreated"
    assert actor == "operator"
    assert payload == {"original_run_id": str(original.id)}
    # And nothing was written against the original's ledger: that ledger is a
    # record of what the original run did, not of what was done to it.
    assert not [event for event in run_store.audit_events if event[0] == original.id]


def test_recreate_requires_the_operator_token(
    stores: tuple[FakeTicketStore, _RunStoreWithAudit],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write is guarded like every other write (contract §8)."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    get_settings.cache_clear()
    tickets, runs = stores
    app = create_app(
        run_store=runs,
        ticket_store=tickets,
        ticket_intake=FakeTicketIntake(tickets, runs),
    )
    original = _finished_run(runs)
    with TestClient(app) as test_client:
        response = test_client.post(f"/api/runs/{original.id}/recreate")
        assert response.status_code == 401
    get_settings.cache_clear()
