"""The list endpoints served from the *real* SQL stores, not from test doubles.

The companion to ``test_run_detail_against_the_real_store.py``, and the same
argument applied to a different set of methods.

Every list-endpoint test in this suite builds the app with ``FakeTicketStore``,
``FakeRunStore`` and ``FakeApprovalStore``, and all three fakes implement
``list``. ``SqlTicketStore``, ``SqlRunStore`` and ``SqlApprovalStore`` -- what a
deployment actually binds -- did not. Each router reaches its list through an
optional-method probe (``tickets.py``'s ``getattr(tickets, "list", None)``,
``runs.py``'s ``_optional_method``, ``approvals.py``'s
``getattr(approvals, "list", None)``), so against the real store every probe
found nothing and every endpoint returned its documented empty page:

    GET /api/tickets    -> 200 {"items": [], "total": 0}
    GET /api/runs       -> 200 {"items": [], "total": 0}
    GET /api/approvals  -> 200 {"items": [], "total": 0}

An empty page is the *documented* degradation, so nothing logged an error, and
the response shape is valid. The consequence is the one the M7 dashboard is
built to prevent: three screens reporting "nothing has happened" for a system
in which a refund was parked and waiting for a human. The Approvals case is the
worst -- an inbox that claims nothing is awaiting approval while
``billing.issue_refund`` sits in ``WAITING_APPROVAL``.

The same probe is why ``GET /api/approvals/{id}`` answered 404 for an approval
that existed: ``_get_approval`` reads ``getattr(approvals, "get", None)``, and
the real store had no ``get``. That also silently disabled the 409 path's
read-back, which returns 404 instead of ``approval_already_decided``.

So this file drives the concrete SQL stores over a real schema, so each probe's
answer is the answer a deployment gets. The fakes answer every probe; the
production object must be the one that does.

``docs/api-contract.md`` §1 names the endpoints; §5 the approvals inbox; §10 the
pagination.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models, repositories
from opspilot.adapters.persistence.models import Base
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import RunStatus
from opspilot.settings import Settings

pytestmark = pytest.mark.asyncio


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


async def _seed(factory: sessionmaker[Session]) -> tuple[repositories.SqlRunStore, object]:
    """Two runs and one parked approval, written through the real stores."""
    tickets = repositories.SqlTicketStore(factory)
    runs = repositories.SqlRunStore(factory)
    calls = repositories.SqlToolCallStore(factory)
    approvals = repositories.SqlApprovalStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice for invoice INV-2026-384",
        body="We were charged twice.",
        customer_email="billing@acme.example",
    )
    parked = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    await runs.set_status(parked.id, RunStatus.CLASSIFYING)
    call_id = await calls.record_proposed(
        run_id=parked.id,
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": 129.0},
        permission="high_risk_write",
    )
    approval = ApprovalRequest(
        id=uuid4(),
        run_id=parked.id,
        tool_call_id=call_id,
        reason="Duplicate confirmed; proposing a full refund.",
        risk_explanation="This moves $129.00 USD out of the account.",
        arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.0},
        created_at=datetime.now(UTC),
    )
    await approvals.create(approval)

    second_ticket = await tickets.create(
        subject="How do I rotate my API key?",
        body="The key in our CI log looks exposed.",
        customer_email="dev@acme.example",
    )
    await runs.create(ticket_id=second_ticket, model_provider="fake", model_name="fake-1")

    return runs, approval


# -- the probes --------------------------------------------------------------


async def test_the_real_ticket_store_answers_the_list_probe(factory: sessionmaker[Session]) -> None:
    """``SqlTicketStore.list`` exists, so ``GET /api/tickets`` is not an empty page."""
    tickets = repositories.SqlTicketStore(factory)
    assert hasattr(tickets, "list"), (
        "SqlTicketStore has no list; the tickets router's getattr probe returns "
        "None and serves an empty page for a system that has tickets"
    )


async def test_the_real_run_store_answers_the_list_probe(factory: sessionmaker[Session]) -> None:
    """``SqlRunStore.list`` exists, so ``GET /api/runs`` is not an empty page."""
    runs = repositories.SqlRunStore(factory)
    assert hasattr(runs, "list"), (
        "SqlRunStore has no list; the runs router's optional-method probe finds "
        "nothing and every list response is empty regardless of the status filter"
    )


async def test_the_real_approval_store_answers_the_list_probe(
    factory: sessionmaker[Session],
) -> None:
    """``SqlApprovalStore.list`` exists, so the approvals inbox is not empty."""
    approvals = repositories.SqlApprovalStore(factory)
    assert hasattr(approvals, "list"), (
        "SqlApprovalStore has no list; the approvals inbox reports nothing "
        "awaiting a human while a HIGH_RISK_WRITE sits parked -- the exact "
        "lie this dashboard exists to prevent"
    )


async def test_the_real_approval_store_answers_the_get_by_id_probe(
    factory: sessionmaker[Session],
) -> None:
    """``SqlApprovalStore.get`` exists, so ``GET /api/approvals/{id}`` is not a 404.

    The same probe serves the 409's read-back in ``approvals.py``'s
    ``except LookupError`` branch, so its absence also silently turned a
    lost-the-race 409 into a 404.
    """
    approvals = repositories.SqlApprovalStore(factory)
    assert hasattr(approvals, "get"), (
        "SqlApprovalStore has no get; _get_approval returns None, the route "
        "answers 404 approval_not_found for an approval that exists, and the "
        "second-click path cannot report approval_already_decided"
    )


# -- the behaviour, against the real rows ------------------------------------


async def test_ticket_list_returns_the_written_tickets_newest_first(
    factory: sessionmaker[Session],
) -> None:
    """Both tickets come back, newest first (contract §10)."""
    await _seed(factory)
    tickets = repositories.SqlTicketStore(factory)

    rows = await tickets.list()

    assert len(rows) == 2
    assert rows[0].subject == "How do I rotate my API key?"
    created = [row.created_at for row in rows]
    assert created == sorted(created, reverse=True)


async def test_run_list_returns_the_written_runs(factory: sessionmaker[Session]) -> None:
    """``GET /api/runs``'s payload is built from real rows, not an empty list."""
    await _seed(factory)
    runs = repositories.SqlRunStore(factory)

    rows = await runs.list()

    assert len(rows) == 2
    assert {row.status for row in rows} == {RunStatus.CLASSIFYING, RunStatus.RECEIVED}


async def test_run_list_filters_by_status(factory: sessionmaker[Session]) -> None:
    """The ``?status=`` filter of contract §1 is honoured by the store itself.

    Asserted here rather than only through the route because the route passes
    the filter through a probe: a store that ignored the argument would still
    return a correctly-shaped response with every row in it.
    """
    await _seed(factory)
    runs = repositories.SqlRunStore(factory)

    classifying = await runs.list(status=RunStatus.CLASSIFYING)

    assert len(classifying) == 1
    assert classifying[0].status is RunStatus.CLASSIFYING


async def test_run_list_paginates_over_a_total_order(factory: sessionmaker[Session]) -> None:
    """``limit``/``offset`` page without repeating or dropping a run.

    Both runs are created in the same transaction in the two-ticket case, and
    ``created_at`` is a server default, so this is the shape the tie-break on
    ``id`` exists for: without a total order two pages could disagree.
    """
    await _seed(factory)
    runs = repositories.SqlRunStore(factory)

    first = await runs.list(limit=1)
    second = await runs.list(limit=1, offset=1)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id != second[0].id


async def test_approval_list_returns_the_pending_row(factory: sessionmaker[Session]) -> None:
    """The pending approval is in the inbox, with its two texts and its snapshot."""
    _runs, approval = await _seed(factory)
    approvals = repositories.SqlApprovalStore(factory)

    rows = await approvals.list(status=ApprovalStatus.PENDING)

    assert len(rows) == 1
    assert rows[0].id == approval.id
    assert rows[0].risk_explanation == "This moves $129.00 USD out of the account."
    assert rows[0].reason == "Duplicate confirmed; proposing a full refund."
    assert rows[0].arguments_snapshot == {"transaction_id": "TX-88219", "amount": 129.0}


async def test_approval_get_returns_the_row_by_its_own_id(
    factory: sessionmaker[Session],
) -> None:
    """``get`` is keyed on the approval id, which is what the route is keyed on."""
    _runs, approval = await _seed(factory)
    approvals = repositories.SqlApprovalStore(factory)

    found = await approvals.get(approval.id)

    assert found is not None
    assert found.id == approval.id
    assert found.status is ApprovalStatus.PENDING


# -- the double-approval window ---------------------------------------------


async def test_two_pending_approvals_for_one_run_are_both_listed(
    factory: sessionmaker[Session],
) -> None:
    """A resumed run's second proposal produces two rows, and both are visible.

    The M6 decision to preserve ``EXECUTING`` across a restart means a run
    killed between committing ``EXECUTING`` and committing the tool call's
    terminal status resumes, re-proposes the same refund under a **new**
    ``tool_call_id``, and parks again. The idempotency key blocks the money
    moving twice; it does not stop two approval rows existing.

    So the store must list both. A limit of one, or a per-run dedupe, would
    leave an operator approving the second card believing the first had already
    been handled -- while the first is still pending.
    """
    tickets = repositories.SqlTicketStore(factory)
    runs = repositories.SqlRunStore(factory)
    calls = repositories.SqlToolCallStore(factory)
    approvals = repositories.SqlApprovalStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice", body="Twice.", customer_email="billing@acme.example"
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")

    snapshot = {"transaction_id": "TX-88219", "amount": 129.0}
    for _ in range(2):
        call_id = await calls.record_proposed(
            run_id=run.id,
            tool_name="billing.issue_refund",
            arguments=dict(snapshot),
            permission="high_risk_write",
        )
        await approvals.create(
            ApprovalRequest(
                id=uuid4(),
                run_id=run.id,
                tool_call_id=call_id,
                reason="Duplicate confirmed.",
                risk_explanation="This moves $129.00 USD out of the account.",
                arguments_snapshot=dict(snapshot),
                created_at=datetime.now(UTC),
            )
        )

    pending = await approvals.list(status=ApprovalStatus.PENDING)

    assert len(pending) == 2
    assert len({row.tool_call_id for row in pending}) == 2
    assert all(row.run_id == run.id for row in pending)
    # Same run, same arguments: this is what the dashboard groups on, so the
    # grouping has a row in hand to find.
    assert all(row.arguments_snapshot == snapshot for row in pending)


async def test_get_pending_approval_returns_the_earliest_of_two(
    factory: sessionmaker[Session],
) -> None:
    """Run detail nests the *first* pending approval, not an arbitrary one.

    Contract §3 allows exactly one ``pending_approval``, so with two rows the
    choice has to be a rule rather than a side effect of the query plan. The
    earliest is the one an operator should act on: it is the proposal that has
    been waiting longest.
    """
    tickets = repositories.SqlTicketStore(factory)
    runs = repositories.SqlRunStore(factory)
    calls = repositories.SqlToolCallStore(factory)
    approvals = repositories.SqlApprovalStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice", body="Twice.", customer_email="billing@acme.example"
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    await runs.set_status(run.id, RunStatus.CLASSIFYING)

    first_id: object = None
    for index in range(2):
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
            reason=f"Duplicate confirmed ({index}).",
            risk_explanation="This moves $129.00 USD out of the account.",
            arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.0},
            created_at=datetime.now(UTC),
        )
        if index == 0:
            first_id = approval.id
        await approvals.create(approval)

    nested = await runs.get_pending_approval(run.id)

    assert nested is not None
    assert nested.id == first_id


async def test_get_pending_approval_is_none_once_decided(factory: sessionmaker[Session]) -> None:
    """A decided approval no longer nests: the run has moved on."""
    _runs, approval = await _seed(factory)
    runs = repositories.SqlRunStore(factory)
    approvals = repositories.SqlApprovalStore(factory)

    await approvals.decide(
        approval.id,
        approved=True,
        decided_by="admin",
        decided_at=datetime.now(UTC),
    )

    # `decide` moved the run to EXECUTING, and the router only asks for a
    # pending approval while the run is WAITING_APPROVAL; the store's own answer
    # must agree even when asked directly.
    assert await runs.get_pending_approval(approval.run_id) is None


# -- the customer reply ------------------------------------------------------


async def test_customer_reply_is_read_back_from_the_response_step(
    factory: sessionmaker[Session],
) -> None:
    """A completed run's reply is persisted, and readable back.

    ``docs/api-contract.md`` §3 promises ``customer_reply`` "set only at
    ``COMPLETED``". There is no reply table: the ``response`` step is the only
    place the model's output is recorded, so storing the body's length and not
    the body left every completed run's reply permanently unrenderable.
    """
    runs = repositories.SqlRunStore(factory)
    tickets = repositories.SqlTicketStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice", body="Twice.", customer_email="billing@acme.example"
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")

    body = "We found the duplicate charge and issued your refund of $129.00."
    with db.session_scope(factory) as session:
        session.add(
            models.AgentStep(
                run_id=run.id,
                sequence=1,
                step_type="response",
                output={"escalated": False, "chars": len(body), "body": body},
                started_at=datetime.now(UTC),
            )
        )

    reply = await runs.get_customer_reply(run.id)

    assert reply is not None
    assert reply.body == body
    assert reply.escalated is False


async def test_customer_reply_carries_the_escalation_flag(
    factory: sessionmaker[Session],
) -> None:
    """A rejection's escalation reply is distinguishable from a success.

    The two are the same sentence shape with opposite meanings -- "we could not
    do this, a human is on it" versus "here is your refund" -- and the flag is
    the only thing on screen that tells them apart.
    """
    runs = repositories.SqlRunStore(factory)
    tickets = repositories.SqlTicketStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice", body="Twice.", customer_email="billing@acme.example"
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")

    body = "I could not issue this refund; a human colleague is reviewing it."
    with db.session_scope(factory) as session:
        session.add(
            models.AgentStep(
                run_id=run.id,
                sequence=1,
                step_type="response",
                output={"escalated": True, "chars": len(body), "body": body},
                started_at=datetime.now(UTC),
            )
        )

    reply = await runs.get_customer_reply(run.id)

    assert reply is not None
    assert reply.escalated is True


async def test_customer_reply_is_none_when_no_response_step_was_recorded(
    factory: sessionmaker[Session],
) -> None:
    """A run with no ``response`` step has no reply, and says so.

    Asserting the ``None`` rather than an empty string matters: an empty body
    would render as a blank reply panel, which reads as a bug rather than as
    the absence the contract permits.
    """
    runs = repositories.SqlRunStore(factory)
    tickets = repositories.SqlTicketStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice", body="Twice.", customer_email="billing@acme.example"
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")

    assert await runs.get_customer_reply(run.id) is None
