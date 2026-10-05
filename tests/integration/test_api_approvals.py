"""Integration tests for the approvals API.

These are the M1 tests for the human half of gate 5. They run against a
``TestClient`` with in-memory fakes injected through ``create_app``.

The assertions that matter, all from ``docs/api-contract.md`` §5:

- approving transitions the run ``waiting_approval -> executing``;
- rejecting transitions it ``waiting_approval -> responding`` -- **not** failed;
- a second approve returns 409, not a double grant;
- approving a run that is not awaiting approval returns 409;
- **the approve handler executes no tool** -- asserted with the run store's tool
  spy, because "a human clicking a button must not run a payment call inline" is
  the whole reason approval is a state change rather than a callback.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.settings import get_settings
from tests.integration.fakes import FakeApprovalStore, FakeRunStore, FakeTicketStore

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _parked_run(run_store: FakeRunStore) -> AgentRun:
    """Insert a run parked in ``WAITING_APPROVAL`` and return it."""
    run = AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=RunStatus.WAITING_APPROVAL,
        model_provider="fake",
        model_name="fake-model",
        created_at=datetime.now(UTC),
    )
    run_store.rows[run.id] = run
    return run


def _pending_approval(
    approval_store: FakeApprovalStore,
    run: AgentRun,
    *,
    tool_call_id: UUID | None = None,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> ApprovalRequest:
    """Insert an approval bound to a tool call on ``run``."""
    approval = ApprovalRequest(
        id=uuid4(),
        run_id=run.id,
        tool_call_id=tool_call_id or uuid4(),
        status=status,
        reason="Verified duplicate charge on TX-88219; proposing full refund of $129.00.",
        risk_explanation="This moves $129.00 and cannot be undone automatically.",
        arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.00},
        created_at=datetime.now(UTC),
        decided_at=datetime.now(UTC) if status is not ApprovalStatus.PENDING else None,
        decided_by="someone" if status is not ApprovalStatus.PENDING else None,
    )
    approval_store.rows[approval.id] = approval
    return approval


@pytest.fixture
def stores() -> tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]:
    """The fake stores, with the approval store sharing the run store."""
    run_store = FakeRunStore()
    return FakeTicketStore(), run_store, FakeApprovalStore(run_store=run_store)


@pytest.fixture
def client(
    stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A ``TestClient`` over an app whose stores are the in-memory fakes."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
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


# -- listing -----------------------------------------------------------------


def test_list_pending_approvals(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """The inbox defaults to pending and carries the approver context."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.get("/api/approvals?status=pending", headers=_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(approval.id)
    assert item["status"] == "pending"
    assert item["arguments_snapshot"]["amount"] == 129.0
    assert item["decided_at"] is None


def test_get_unknown_approval_is_404_with_code(client: TestClient) -> None:
    """An unknown approval id is 404 ``approval_not_found``."""
    response = client.get(f"/api/approvals/{uuid4()}", headers=_AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "approval_not_found"


# -- approve -----------------------------------------------------------------


def test_approving_transitions_the_run_to_executing(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """Approve: ``waiting_approval -> executing``, and the decision is recorded."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == "operator"
    assert body["run"]["status"] == "executing"
    assert run_store.rows[run.id].status is RunStatus.EXECUTING


def test_approve_handler_does_not_execute_any_tool(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """Approval is a state change; it must not run the refund inline.

    The spy records any tool execution; the assertion is that none happened. If a
    future refactor "helpfully" executed the approved call in the handler, this is
    the test that fails.
    """
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert response.status_code == 200
    assert run_store.tool_spy.call_count == 0, run_store.tool_spy.calls
    assert run_store.worker_spy.call_count == 0, run_store.worker_spy.calls


def test_a_second_approve_returns_409(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """The double-click case: the second approve is 409, not a second grant."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    first = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert first.status_code == 200

    second = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "approval_already_decided"


def test_approving_a_run_not_awaiting_approval_returns_409(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """An approval whose run has moved on is 409 ``run_not_awaiting_approval``."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    run.status = RunStatus.RESPONDING  # something else advanced it
    approval = _pending_approval(approval_store, run)

    response = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_awaiting_approval"


# -- reject ------------------------------------------------------------------


def test_rejecting_transitions_the_run_to_responding(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """Reject: ``waiting_approval -> responding`` -- a rejection is not a failure."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.post(f"/api/approvals/{approval.id}/reject", headers=_AUTH, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["run"]["status"] == "responding"
    stored = run_store.rows[run.id]
    assert stored.status is RunStatus.RESPONDING
    # Spelled out rather than left to the equality above: the point of the test is
    # that a rejection is *not* a failure, and a reader should see that asserted.
    assert stored.status not in {RunStatus.FAILED, RunStatus.COMPLETED}


def test_reject_executes_no_tool(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """A rejection moves state only; no tool runs and no worker is invoked."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.post(f"/api/approvals/{approval.id}/reject", headers=_AUTH, json={})
    assert response.status_code == 200
    assert run_store.tool_spy.call_count == 0, run_store.tool_spy.calls
    assert run_store.worker_spy.call_count == 0, run_store.worker_spy.calls


def test_rejecting_an_approved_request_returns_409(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """Rejecting after an approve is 409 ``approval_already_decided`` (contract §9)."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    first = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH, json={})
    assert first.status_code == 200

    rejected = client.post(f"/api/approvals/{approval.id}/reject", headers=_AUTH, json={})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "approval_already_decided"


def test_decision_body_records_decided_by_and_note(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """An explicit ``decided_by`` overrides the operator default."""
    _, run_store, approval_store = stores
    run = _parked_run(run_store)
    approval = _pending_approval(approval_store, run)

    response = client.post(
        f"/api/approvals/{approval.id}/approve",
        headers=_AUTH,
        json={"decided_by": "admin", "note": "Confirmed in billing console."},
    )
    assert response.status_code == 200
    assert response.json()["decided_by"] == "admin"


def test_approvals_require_a_token(client: TestClient) -> None:
    """The approvals surface is behind the bearer token like everything else."""
    response = client.get("/api/approvals")
    assert response.status_code == 401
