"""An approval nobody decides stops being decidable, and says so.

## Why this file exists

``ApprovalStatus.EXPIRED`` was in the enum from the start, ``GET
/api/approvals?status=expired`` accepted it, and **no code path could produce
it**. So an approval nobody ever answered stayed ``pending`` forever: the
inbox grew a tail of rows that looked actionable and were not, and the
dashboard deliberately refused to offer a filter for a status nothing could
ever have.

The decision was taken on 2026-10-21: **an approval expires.** A TTL, a
sweeper, and a 409 that says *expired* rather than *already decided* -- because
those are different answers and only one of them is true.

This file pins all of it, against the **real SQL stores** rather than the
fakes. The sweep is a conditional ``UPDATE``, and a fake's version of a
conditional update is a fake's answer to a question about the database; the
thing being tested here *is* the conditional update.

## What is asserted

1. **The sweep expires an old pending approval and leaves a fresh one alone.**
2. **It does not touch an approval that was already decided.** An approval
   that was approved two seconds ago must still say ``approved``, whatever the
   clock says -- the sweep is not allowed to overwrite a decision.
3. **The run stays parked.** An expired approval leaves its run in
   ``WAITING_APPROVAL``: nobody decided, so no money moved, and there is
   nothing for the worker to do.
4. **Deciding an expired approval is a 409 with the code
   ``approval_expired``** -- and specifically *not*
   ``approval_already_decided``, which would claim somebody decided it.
5. **The filter finds them.** ``GET /api/approvals?status=expired`` returns
   the expired rows and counts them, so the tab that used to be impossible to
   fill now fills.
6. **A TTL of zero disables the sweep**, which is the escape hatch for a
   deployment that wants approvals to wait forever.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, repositories
from opspilot.adapters.persistence.models import Base
from opspilot.api.app import create_app
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.errors import ApprovalExpired
from opspilot.domain.runs import RunStatus
from opspilot.settings import Settings, get_settings
from opspilot.worker.loop import (
    sweep_expired_approvals,
    sweep_expired_approvals_safely,
)
from tests.integration.fakes import FakeApprovalStore

pytestmark = pytest.mark.asyncio

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

#: How old an approval is when the sweep runs. Chosen to be comfortably on the
#: wrong side of any TTL the tests use, so the arithmetic cannot be a
#: near-miss.
_TWO_HOURS = timedelta(hours=2)
_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


async def _parked_approval(
    factory: sessionmaker[Session],
    *,
    age: timedelta,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> tuple[repositories.SqlApprovalStore, ApprovalRequest, repositories.SqlRunStore]:
    """A run parked with one approval, written through the real stores.

    The approval is created at ``_NOW - age``, so the sweep's cutoff can be
    reasoned about without a clock: the tests place the instant rather than
    wait for it.
    """
    tickets = repositories.SqlTicketStore(factory)
    runs = repositories.SqlRunStore(factory)
    calls = repositories.SqlToolCallStore(factory)
    approvals = repositories.SqlApprovalStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice for invoice INV-2026-384",
        body="We were charged twice.",
        customer_email="billing@acme.example",
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    await runs.set_status(run.id, RunStatus.CLASSIFYING)
    call_id = await calls.record_proposed(
        run_id=run.id,
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": 129.0},
        permission="high_risk_write",
    )
    approval = ApprovalRequest(
        id=uuid4(),
        run_id=run.id,
        tool_call_id=call_id,
        reason="Duplicate confirmed; proposing a full refund.",
        risk_explanation="This moves $129.00 USD out of the account.",
        arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.0},
        created_at=_NOW - age,
        status=status,
    )
    await approvals.create(approval)
    # Park the run: an approval only exists because a run is waiting.
    await runs.set_status(run.id, RunStatus.EXECUTING)
    await runs.set_status(run.id, RunStatus.WAITING_APPROVAL)
    return approvals, approval, runs


# -- the sweep ----------------------------------------------------------------


async def test_the_sweep_expires_an_old_pending_approval(
    factory: sessionmaker[Session],
) -> None:
    """An approval older than the TTL stops being pending."""
    approvals, approval, _ = await _parked_approval(factory, age=_TWO_HOURS)

    expired = await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    assert expired == 1, "the sweep did not expire the approval that was two hours old"
    stored = await approvals.get(approval.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.EXPIRED, (
        f"the approval is still {stored.status.value}; the sweep reported nothing "
        "but left the row decidable"
    )


async def test_the_sweep_leaves_a_fresh_approval_alone(
    factory: sessionmaker[Session],
) -> None:
    """An approval inside the TTL is still pending, and still decidable."""
    approvals, approval, _ = await _parked_approval(factory, age=timedelta(minutes=5))

    expired = await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    assert expired == 0, "the sweep expired an approval that was five minutes old"
    stored = await approvals.get(approval.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.PENDING


async def test_the_sweep_does_not_overwrite_a_decision(
    factory: sessionmaker[Session],
) -> None:
    """An approval that was decided stays decided, whatever its age.

    The sweep is a conditional update on ``status = 'pending'``. If it were not
    conditional, an approval approved an hour ago and swept an hour later
    would be reported as expired -- and the operator's decision, and the audit
    row that records it, would be quietly undone.
    """
    approvals, approval, _ = await _parked_approval(factory, age=_TWO_HOURS)
    await approvals.decide(
        approval.id,
        approved=True,
        decided_by="operator@example.com",
        note="Confirmed in the billing console.",
        decided_at=_NOW - timedelta(hours=1),
    )

    expired = await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    assert expired == 0, "the sweep expired an approval that had already been approved"
    stored = await approvals.get(approval.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.APPROVED
    assert stored.decided_by == "operator@example.com"
    assert stored.note == "Confirmed in the billing console."


async def test_the_sweep_leaves_the_run_parked(
    factory: sessionmaker[Session],
) -> None:
    """An expired approval does not move the run.

    Nobody decided, so the money did not move and there is nothing to
    execute. The run stays in ``WAITING_APPROVAL``: a run that failed because
    a person was slow would be a lie about why it failed.
    """
    approvals, approval, runs = await _parked_approval(factory, age=_TWO_HOURS)

    await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    run = await runs.get(approval.run_id)
    assert run is not None
    assert run.status is RunStatus.WAITING_APPROVAL, (
        f"the run moved to {run.status.value} when its approval expired; it "
        "should still be parked, because no decision was made"
    )


async def test_a_ttl_of_zero_disables_the_sweep(
    factory: sessionmaker[Session],
) -> None:
    """Zero means "wait forever", and it writes nothing at all."""
    approvals, approval, _ = await _parked_approval(factory, age=_TWO_HOURS * 100)

    expired = await sweep_expired_approvals(approval_store=approvals, ttl_seconds=0.0, now=_NOW)

    assert expired == 0
    stored = await approvals.get(approval.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.PENDING


async def test_the_store_tells_expired_apart_from_already_decided(
    factory: sessionmaker[Session],
) -> None:
    """The store's own refusal names the expiry, not a lost race.

    The router checks the approval's status before it calls ``decide``, so on
    the ordinary path this branch is never reached -- and a branch nothing
    reaches is a branch nothing tests. It *is* reached when the sweep and the
    click race: the row is pending when the router reads it and expired by the
    time the conditional update runs, and then the store's answer is the only
    thing that can tell the operator which happened.

    So the distinction is asserted **at the store**, where it is made. The
    exception type is the assertion: ``ApprovalExpired`` is a ``DomainError``
    and not a ``LookupError``, so it cannot be caught by the route's
    ``except LookupError`` branch -- which answers "somebody else decided this
    first" and would name a person who does not exist.
    """
    approvals, approval, runs = await _parked_approval(factory, age=_TWO_HOURS)
    await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    with pytest.raises(ApprovalExpired):
        await approvals.decide(
            approval.id,
            approved=True,
            decided_by="operator@example.com",
            decided_at=_NOW,
        )

    stored = await approvals.get(approval.id)
    assert stored is not None
    assert stored.status is ApprovalStatus.EXPIRED, (
        "the refused decision was recorded anyway; the refusal must leave the "
        "row exactly as the sweep left it"
    )
    assert stored.decided_by is None, (
        "a refused decision wrote a decider onto the row; an expired approval was decided by nobody"
    )
    run = await runs.get(approval.run_id)
    assert run is not None
    assert run.status is RunStatus.WAITING_APPROVAL


async def test_a_failing_sweep_does_not_kill_the_worker() -> None:
    """A sweep that raises is logged and survived, not propagated.

    An exception out of ``poll_forever`` exits the worker process, and the
    boot sweep then marks every run the worker was holding
    ``FAILED('interrupted')``. So a sweep that raises would trade one stale
    approval for a queue of interrupted runs -- the exact trade the worker's
    standing rule about model errors refuses to make.
    """

    class _BrokenStore(FakeApprovalStore):
        """A store whose sweep always fails."""

        async def expire_pending(self, *, before: datetime) -> int:  # noqa: ARG002
            raise RuntimeError("the database is briefly unreachable")

    # No assertion on the log: the point is that the call *returns*. A raise
    # here would propagate out of the poll loop and take the process with it.
    await sweep_expired_approvals_safely(approval_store=_BrokenStore(), ttl_seconds=3600.0)


async def test_the_race_between_the_sweep_and_the_click_is_answered_honestly(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A click that loses the race to the sweep gets ``approval_expired``.

    This is the branch the ordinary test cannot reach. The route reads the
    approval, sees it pending, and only then calls ``decide`` -- so the window
    between the two is where the sweep can land. The store's conditional update
    then matches no row, and the store's own answer is the only thing that can
    tell the operator which of the two things happened: somebody else decided
    it, or the clock ran out.

    The store here is a wrapper that expires the row *just before* delegating,
    which is exactly what the sweep does at that instant.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    get_settings.cache_clear()
    inner = repositories.SqlApprovalStore(factory)
    _, approval, _ = await _parked_approval(factory, age=timedelta(minutes=1))

    class _SweepsJustBeforeDeciding:
        """The real store, with the sweep landing in the race window."""

        def __init__(self, wrapped: repositories.SqlApprovalStore) -> None:
            self._wrapped = wrapped

        async def expire_pending(self, *, before: datetime) -> int:
            return await self._wrapped.expire_pending(before=before)

        async def get(self, approval_id: UUID) -> ApprovalRequest | None:
            return await self._wrapped.get(approval_id)

        async def decide(self, approval_id: UUID, **kwargs: object) -> ApprovalRequest:
            await self._wrapped.expire_pending(before=_NOW + timedelta(days=1))
            return await self._wrapped.decide(approval_id, **kwargs)  # type: ignore[arg-type]

        async def list(self, **kwargs: object) -> list[ApprovalRequest]:
            return await self._wrapped.list(**kwargs)  # type: ignore[arg-type]

        async def count(self, **kwargs: object) -> int:
            return await self._wrapped.count(**kwargs)  # type: ignore[arg-type]

    app = create_app(
        run_store=repositories.SqlRunStore(factory),
        ticket_store=repositories.SqlTicketStore(factory),
        approval_store=_SweepsJustBeforeDeciding(inner),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH)
    get_settings.cache_clear()

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "approval_expired", (
        f"the refusal says {body['error']['code']!r}. The sweep and the click "
        "raced, nobody decided it, and 'approval_already_decided' would name a "
        "person who does not exist"
    )


# -- what the API says -------------------------------------------------------


@pytest.fixture
def client(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A ``TestClient`` over an app bound to the real SQL stores.

    The operator token is set here rather than in each test, because the
    settings object is **cached**: a token set after the first read is not
    the token the app authenticates against, and the failure is a 401 that
    looks like an auth bug rather than a fixture bug.
    """
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    get_settings.cache_clear()
    app = create_app(
        run_store=repositories.SqlRunStore(factory),
        ticket_store=repositories.SqlTicketStore(factory),
        approval_store=repositories.SqlApprovalStore(factory),
    )
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


async def test_deciding_an_expired_approval_is_a_409_that_says_so(
    factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    """The click on an expired approval is refused, and the refusal is honest.

    The code is ``approval_expired`` and not ``approval_already_decided``:
    the second of those names a person ("was already decided by an operator
    at ..."), and for an expired approval there is no person and no moment.
    """
    approvals, approval, _ = await _parked_approval(factory, age=_TWO_HOURS)
    await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    response = client.post(f"/api/approvals/{approval.id}/approve", headers=_AUTH)

    assert response.status_code == 409, (
        f"deciding an expired approval answered {response.status_code}; it must be refused"
    )
    body = response.json()
    assert body["error"]["code"] == "approval_expired", (
        f"the refusal says {body['error']['code']!r}; an expired approval was "
        "decided by nobody, so 'approval_already_decided' would name a person "
        "who does not exist"
    )


async def test_the_expired_filter_returns_the_expired_approvals(
    factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    """``?status=expired`` finds them, and counts them.

    Before the TTL existed this filter was a ``WHERE status = 'expired'`` that
    returned nothing, always -- a tab that could only ever be empty, telling
    the operator "there are no expired approvals" when the truth was "no
    approval can expire".
    """
    approvals, approval, _ = await _parked_approval(factory, age=_TWO_HOURS)
    await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    response = client.get("/api/approvals?status=expired", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(approval.id)]
    assert body["total"] == 1, "the total does not count the expired approvals"


async def test_an_expired_approval_is_not_in_the_pending_inbox(
    factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    """The default inbox stops showing it.

    The point of the TTL is that the pending inbox answers "what is waiting
    for me" honestly. An expired approval is not waiting for anybody.
    """
    approvals, _, _ = await _parked_approval(factory, age=_TWO_HOURS)
    await sweep_expired_approvals(approval_store=approvals, ttl_seconds=3600.0, now=_NOW)

    response = client.get("/api/approvals", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [], "an expired approval is still in the pending inbox"
    assert body["total"] == 0
