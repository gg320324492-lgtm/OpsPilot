"""A list endpoint's ``total`` is how many rows match, not how many came back.

## Why this file exists

Every list endpoint reported ``total=len(items)`` -- the length of the page it
had just assembled -- and the dashboard read that number as the size of the
collection. So a dashboard with ten recent runs showed "10 total" while a
hundred sat behind the page, and the headline counters were pinned at the
page limit: the code even said so, in a comment that was wrong in the
direction that mattered.

    # limit=200 is the contract's maximum, so a full page of parked runs still
    # returns a count that is *at least* the truth.

It was *at most* the truth, and exactly the truth only when the collection
happened to be smaller than the page.

The contract never defined ``total`` at all, so the code chose and the client
believed. The fix is the other way round: ``total`` counts the rows that match
the filter, ignoring the page, and this file pins that at the **API**, where
the lie was visible to a user.

Every test here builds more rows than one page holds, so a ``total`` that is
really the page length cannot pass.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.ports.stores import ApprovalStore, RunStore, TicketStore
from opspilot.settings import get_settings
from tests.integration.fakes import (
    FakeApprovalStore,
    FakeRunStore,
    FakeTicketIntake,
    FakeTicketStore,
    TicketRow,
)

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

#: More rows than any page used below holds, so "the total" and "the page" are
#: two different numbers and a test cannot pass by accident.
_ROWS = 7
_PAGE = 2


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
    """A ``TestClient`` over an app whose stores are the in-memory fakes."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("MODEL_NAME", "fake-model")
    get_settings.cache_clear()

    ticket_store, run_store, approval_store = stores
    app = create_app(
        run_store=run_store,
        ticket_store=ticket_store,
        ticket_intake=FakeTicketIntake(ticket_store, run_store),
        approval_store=approval_store,
    )
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


async def _seed(
    stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore],
) -> None:
    """Fill the three stores with more rows than a page holds."""
    ticket_store, run_store, approval_store = stores
    for index in range(_ROWS):
        ticket_id = await ticket_store.create(
            subject=f"ticket {index}",
            body="b",
            customer_email=f"c{index}@example.com",
        )
        run = await run_store.create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
        await approval_store.create(
            ApprovalRequest(
                id=uuid4(),
                run_id=run.id,
                tool_call_id=uuid4(),
                reason="r",
                risk_explanation="e",
                arguments_snapshot={},
                created_at=datetime.now(UTC),
                status=ApprovalStatus.PENDING,
            )
        )


async def test_the_tickets_total_counts_every_ticket(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """``total`` is the number of tickets, and ``items`` is the page."""
    await _seed(stores)

    response = client.get(f"/api/tickets?limit={_PAGE}", headers=_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == _PAGE, "the page should hold one page"
    assert body["total"] == _ROWS, "the total must count the rows the page did not return"


async def test_the_runs_total_counts_every_run(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """``total`` is the number of runs, and ``items`` is the page."""
    await _seed(stores)

    response = client.get(f"/api/runs?limit={_PAGE}", headers=_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == _PAGE
    assert body["total"] == _ROWS


async def test_the_approvals_total_counts_every_matching_approval(
    client: TestClient, stores: tuple[FakeTicketStore, FakeRunStore, FakeApprovalStore]
) -> None:
    """``total`` is the number of approvals that match the filter."""
    await _seed(stores)

    response = client.get(f"/api/approvals?status=pending&limit={_PAGE}", headers=_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == _PAGE
    assert body["total"] == _ROWS

    # And the filter narrows the total exactly as it narrows the page: the
    # approvals list defaults to ``pending``, and there are no approved ones.
    approved = client.get("/api/approvals?status=approved", headers=_AUTH)
    assert approved.status_code == 200
    assert approved.json()["total"] == 0


def test_the_store_ports_require_a_count() -> None:
    """``count`` is on the three store ports, so no router can do without it.

    The count was originally an *optional* capability the routers probed for
    with a ``getattr``, and the fallback -- when the store had no count -- was
    ``len(items)``. That fallback is the lie contract §10 forbids, and it was
    invisible precisely because it only fired on a store that does not exist
    today. Making ``count`` a port member turns that fallback into a type
    error instead of a runtime lie: a store without a count no longer
    satisfies the port the routers are typed against.

    This test asserts the membership directly, because "the protocol has the
    method" is the fact the whole arrangement rests on, and mypy is the only
    thing currently checking it.
    """
    for port in (TicketStore, RunStore, ApprovalStore):
        assert hasattr(port, "count"), (
            f"{port.__name__} does not require a count; the routers would fall back to "
            "the page length, which contract §10 forbids"
        )


class _CountlessTicketStore:
    """A ticket store that can list but cannot count.

    This object does **not** satisfy ``TicketStore`` -- the port requires a
    count, which is the point of the test above. It is here to pin what
    happens when one is bound anyway: the endpoint must fail **loudly**, not
    answer 200 with the page length wearing the label ``total``. A silent
    wrong number is the failure this whole file exists to prevent, and the
    loud failure is the one that says "bind a store that satisfies the port".
    """

    def __init__(self, inner: FakeTicketStore) -> None:
        self._inner = inner

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[TicketRow]:
        return await self._inner.list(limit=limit, offset=offset)


async def test_a_store_that_cannot_count_fails_loudly_rather_than_reporting_the_page_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store without a count cannot make the endpoint answer a wrong total."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("MODEL_NAME", "fake-model")
    get_settings.cache_clear()

    ticket_store = FakeTicketStore()
    run_store = FakeRunStore()
    app = create_app(
        run_store=run_store,
        ticket_store=_CountlessTicketStore(ticket_store),  # type: ignore[arg-type]
        ticket_intake=FakeTicketIntake(ticket_store, run_store),
        approval_store=FakeApprovalStore(run_store=run_store),
    )
    # ``raise_server_exceptions=False`` so the raised error is observed as a
    # 500 rather than re-raised into the test -- the endpoint's answer is the
    # thing under test.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/tickets?limit={_PAGE}", headers=_AUTH)
    get_settings.cache_clear()

    assert response.status_code == 500, (
        "a store that cannot count must make the endpoint fail, not answer 200 with "
        "the page length as 'total'"
    )
    assert response.json().get("items") is None, "the endpoint must not answer a list at all"
