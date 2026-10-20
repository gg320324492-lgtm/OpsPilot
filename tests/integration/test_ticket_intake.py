"""A ticket and its run are one unit of work, and the database says so.

## Why this file exists

``POST /api/tickets`` used to insert the ticket, commit, insert the run, commit
again -- two transactions with a compensating delete between them. The
compensating delete was a ``getattr(tickets, "rollback", None)``, and the SQL
store does not answer that name, so in production it was a no-op: a failure
between the two commits left an **orphan ticket**, a row no worker would ever
claim and no dashboard would ever explain.

``docs/api-contract.md`` §2 promised one transaction the whole time, and so
did the dashboard's own copy -- "a ticket can never exist without having been
investigated". Both promises were false until the intake port replaced the two
writes.

The test that would have caught it did not: the only coverage of the failure
path ran against the **fakes**, and the fake is the one place the compensating
delete was implemented. The real store's path had no test at all.

So the tests here run against the **real SQL stores**, and the failure they
manufacture is a **real database failure** -- a ``NOT NULL`` violation on
``agent_runs.model_provider``, caught by SQLite and Postgres alike -- rather
than a monkeypatched exception that might have been raised before the ticket
was ever flushed.

Layer: ``tests``. Persistence only; no API, no worker.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketIntake,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import RunStatus
from opspilot.settings import Settings


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    yield factory


async def test_the_ticket_and_its_run_land_together(factory: sessionmaker[Session]) -> None:
    """The happy path: one ticket, one run, and the run points at the ticket."""
    intake = SqlTicketIntake(factory)

    ticket_id, run = await intake.create(
        subject="charged twice",
        body="invoice INV-2026-384",
        customer_email="billing@acme.example",
        external_id=None,
        model_provider="fake",
        model_name="fake-1",
    )

    assert run.ticket_id == ticket_id
    assert run.status is RunStatus.RECEIVED

    with db.session_scope(factory) as session:
        stored = await SqlTicketStore(session).get(ticket_id)
        fetched = await SqlRunStore(session).get(run.id)
    assert stored is not None
    assert stored.subject == "charged twice"
    assert fetched is not None
    assert fetched.ticket_id == ticket_id


async def test_a_failed_run_insert_leaves_no_ticket(factory: sessionmaker[Session]) -> None:
    """The atomicity property, against the real store.

    The run insert fails on a ``NOT NULL`` constraint, which is the database
    refusing the row rather than the test raising on purpose. The ticket was
    already inserted and flushed into the same transaction, so if the two were
    separate transactions the ticket would survive -- and that is exactly the
    orphan this test exists to make impossible.
    """
    intake = SqlTicketIntake(factory)

    with pytest.raises(IntegrityError):
        await intake.create(
            subject="charged twice",
            body="invoice INV-2026-384",
            customer_email="billing@acme.example",
            external_id=None,
            # ``agent_runs.model_provider`` is NOT NULL. A run that names no
            # provider is not a thing the schema accepts, and the database says
            # so -- inside the transaction that holds the ticket.
            model_provider=None,  # type: ignore[arg-type]
            model_name="fake-1",
        )

    with db.session_scope(factory) as session:
        tickets = session.query(models.Ticket).count()
        runs = session.query(models.AgentRun).count()
    assert tickets == 0, "a ticket survived a failed run insert"
    assert runs == 0


async def test_the_ticket_is_visible_to_the_run_insert(factory: sessionmaker[Session]) -> None:
    """Both writes are in **one** transaction, and this is the observable proof.

    The run's ``ticket_id`` is a foreign key to ``tickets.id``. Within one
    transaction the ticket is already there when the run is inserted, so the
    foreign key is satisfied by a row that is not yet committed. Across two
    transactions the second one would have to wait for the first to commit --
    and the point of the intake is that there is no "first" to wait for.

    The test asserts the weaker, observable thing that matters to a reader:
    the constraint is satisfied and the pair commits together. Whether it is
    *one* transaction is asserted by the rollback test above, which is the one
    that fails when it is not.
    """
    intake = SqlTicketIntake(factory)
    ticket_id, run = await intake.create(
        subject="charged twice",
        body="invoice INV-2026-384",
        customer_email="billing@acme.example",
        external_id=None,
        model_provider="fake",
        model_name="fake-1",
    )

    with db.session_scope(factory) as session:
        row = session.get(models.AgentRun, run.id)
    assert row is not None
    assert row.ticket_id == ticket_id

    # And the ticket is there, committed, in the same breath.
    with db.session_scope(factory) as session:
        assert session.get(models.Ticket, ticket_id) is not None


async def test_the_count_is_the_total_and_not_the_page(factory: sessionmaker[Session]) -> None:
    """``count()`` counts every row that matches; ``list()`` returns one page.

    The distinction is the whole point of adding ``count``. The API's ``total``
    used to be ``len(items)`` -- the length of the page -- and the dashboard
    read it as "how many there are", so a dashboard with ten recent runs
    reported "10 total" while a hundred sat behind it.

    So the test builds **more rows than one page holds** and asserts the two
    numbers differ.
    """
    with db.session_scope(factory) as session:
        tickets = SqlTicketStore(session)
        for index in range(5):
            await tickets.create(
                subject=f"ticket {index}",
                body="b",
                customer_email=f"c{index}@example.com",
            )
        assert await tickets.count() == 5
        assert len(await tickets.list(limit=2)) == 2

    with db.session_scope(factory) as session:
        tickets = SqlTicketStore(session)
        runs = SqlRunStore(session)
        ids: list[UUID] = []
        for index in range(5):
            ticket_id = await tickets.create(
                subject=f"ticket {index}",
                body="b",
                customer_email=f"c{index}@example.com",
            )
            run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
            ids.append(run.id)
        assert await runs.count() == 5
        assert len(await runs.list(limit=2)) == 2

        # The filter narrows the count exactly as it narrows the page.
        await runs.set_status(ids[0], RunStatus.COMPLETED)
        await runs.set_status(ids[1], RunStatus.COMPLETED)
        assert await runs.count(status=RunStatus.COMPLETED) == 2
        assert await runs.count() == 5
        assert await runs.count(status=RunStatus.FAILED) == 0

    with db.session_scope(factory) as session:
        approvals = SqlApprovalStore(session)
        calls = SqlToolCallStore(session)
        ticket_id = await SqlTicketStore(session).create(
            subject="one", body="b", customer_email="a@example.com"
        )
        run = await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )
        for index in range(3):
            call_id = await calls.record_proposed(
                run_id=run.id,
                tool_name="billing.issue_refund",
                arguments={"transaction_id": f"TX-{index}"},
                permission="high_risk_write",
            )
            await approvals.create(
                ApprovalRequest(
                    id=uuid4(),
                    run_id=run.id,
                    tool_call_id=call_id,
                    reason="r",
                    risk_explanation="e",
                    arguments_snapshot={},
                    created_at=run.created_at,
                )
            )
        assert await approvals.count() == 3
        assert len(await approvals.list(limit=2)) == 2
        assert await approvals.count(status=ApprovalStatus.PENDING) == 3
        assert await approvals.count(status=ApprovalStatus.APPROVED) == 0
