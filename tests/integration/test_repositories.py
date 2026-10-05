"""Integration tests for the persistence repositories against SQLite.

The four properties asserted here are the ones the safety story rests on:

- a run is claimed while claimable and **not** claimed while waiting approval --
  the property that makes the approval gate stop work rather than loop;
- ``has_approved`` is a database read that flips only after a decision;
- a second ``decide`` on an already-decided approval does not grant again;
- the partial unique idempotency index rejects a second executed call.

The schema is built from ``Base.metadata.create_all`` (ADR-0004), not migrations.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus
from opspilot.settings import Settings


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


async def test_store_can_bind_a_sessionmaker(factory: sessionmaker[Session]) -> None:
    # The API's default wiring builds the stores from a sessionmaker; each call
    # then owns (and commits) its own transaction.
    ticket_store = SqlTicketStore(factory)
    run_store = SqlRunStore(factory)
    ticket_id = await ticket_store.create(subject="s", body="b", customer_email="a@example.com")
    run = await run_store.create(ticket_id=ticket_id, model_provider="fake", model_name="m")
    # The write from the first call is visible to the second's fresh session.
    fetched = await run_store.get(run.id)
    assert fetched is not None
    assert fetched.ticket_id == ticket_id


async def _make_ticket_and_run(session: Session) -> tuple[UUID, AgentRun]:
    """Create a ticket and its run in one transaction-level unit of work."""
    ticket_id = await SqlTicketStore(session).create(
        subject="duplicate charge", body="charged twice", customer_email="a@example.com"
    )
    run = await SqlRunStore(session).create(
        ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
    )
    return ticket_id, run


async def test_ticket_and_run_created_in_one_transaction(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        ticket_id, run = await _make_ticket_and_run(session)
        assert run.status is RunStatus.RECEIVED
    # Both persisted together.
    with db.session_scope(factory) as session:
        fetched = await SqlRunStore(session).get(run.id)
        assert fetched is not None
        assert fetched.ticket_id == ticket_id


async def test_rollback_discards_both_writes(factory: sessionmaker[Session]) -> None:
    run_id: UUID
    with pytest.raises(RuntimeError), db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        run_id = run.id
        raise RuntimeError("boom")
    with db.session_scope(factory) as session:
        assert await SqlRunStore(session).get(run_id) is None


async def test_claim_returns_received_run(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
    with db.session_scope(factory) as session:
        claimed = await SqlRunStore(session).claim_next(worker_id="w1")
    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.status is RunStatus.RECEIVED


async def test_waiting_approval_is_not_claimed(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        await _make_ticket_and_run(session)
    with db.session_scope(factory) as session:
        store = SqlRunStore(session)
        claimed = await store.claim_next(worker_id="w1")
        assert claimed is not None
        await store.set_status(claimed.id, RunStatus.WAITING_APPROVAL)

    with db.session_scope(factory) as session:
        assert await SqlRunStore(session).claim_next(worker_id="w1") is None


async def test_set_status_writes_state_change_step(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        store = SqlRunStore(session)
        await store.set_status(run.id, RunStatus.CLASSIFYING)
        await store.set_status(run.id, RunStatus.RETRIEVING)
    with db.session_scope(factory) as session:
        updated = await SqlRunStore(session).get(run.id)
        assert updated is not None
        assert updated.status is RunStatus.RETRIEVING
        assert updated.started_at is not None


async def _make_tool_call(session: Session, run_id: UUID) -> UUID:
    return await SqlToolCallStore(session).record_proposed(
        run_id=run_id,
        tool_name="billing.issue_refund",
        arguments={"transaction_id": "TX-88219", "amount": "129.00"},
        permission="high_risk_write",
    )


async def test_has_approved_is_false_before_and_true_after(
    factory: sessionmaker[Session],
) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        call_id = await _make_tool_call(session, run.id)
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=call_id,
            status=ApprovalStatus.PENDING,
            reason="duplicate charge confirmed",
            risk_explanation="This will move $129.00 and cannot be undone automatically.",
            arguments_snapshot={"transaction_id": "TX-88219", "amount": "129.00"},
            created_at=datetime.now(UTC),
        )
        approval_id = approval.id
        await SqlApprovalStore(session).create(approval)

    with db.session_scope(factory) as session:
        assert await SqlApprovalStore(session).has_approved(call_id) is False

    with db.session_scope(factory) as session:
        decided = await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
        assert decided.status is ApprovalStatus.APPROVED

    with db.session_scope(factory) as session:
        assert await SqlApprovalStore(session).has_approved(call_id) is True


async def test_has_approved_is_bound_to_tool_call_not_run(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        first = await _make_tool_call(session, run.id)
        second = await SqlToolCallStore(session).record_proposed(
            run_id=run.id,
            tool_name="billing.issue_refund",
            arguments={"transaction_id": "TX-99000", "amount": "20.00"},
            permission="high_risk_write",
        )
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=first,
            reason="r",
            risk_explanation="e",
            arguments_snapshot={"transaction_id": "TX-88219", "amount": "129.00"},
            created_at=datetime.now(UTC),
        )
        approval_id = approval.id
        await SqlApprovalStore(session).create(approval)

    with db.session_scope(factory) as session:
        await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )

    with db.session_scope(factory) as session:
        store = SqlApprovalStore(session)
        assert await store.has_approved(first) is True
        assert await store.has_approved(second) is False


async def test_second_decide_does_not_grant_again(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        call_id = await _make_tool_call(session, run.id)
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=call_id,
            reason="r",
            risk_explanation="e",
            arguments_snapshot={"transaction_id": "TX-88219", "amount": "129.00"},
            created_at=datetime.now(UTC),
        )
        approval_id = approval.id
        await SqlApprovalStore(session).create(approval)

    first_at = datetime.now(UTC)
    with db.session_scope(factory) as session:
        granted = await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=first_at
        )
        assert granted.status is ApprovalStatus.APPROVED
        assert granted.decided_by == "operator:1"

    second_at = datetime.now(UTC)
    with db.session_scope(factory) as session:
        second = await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:2", decided_at=second_at
        )
    # The second decide is a no-op: still approved by the original operator.
    assert second.status is ApprovalStatus.APPROVED
    assert second.decided_by == "operator:1"
    # SQLite returns naive datetimes; compare on the instant, not tzinfo.
    assert second.decided_at is not None
    assert second.decided_at.replace(tzinfo=None) == first_at.replace(tzinfo=None)


async def test_second_decide_cannot_flip_to_rejected(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        call_id = await _make_tool_call(session, run.id)
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=call_id,
            reason="r",
            risk_explanation="e",
            arguments_snapshot={"transaction_id": "TX-88219"},
            created_at=datetime.now(UTC),
        )
        approval_id = approval.id
        await SqlApprovalStore(session).create(approval)

    with db.session_scope(factory) as session:
        await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
    with db.session_scope(factory) as session:
        second = await SqlApprovalStore(session).decide(
            approval_id, approved=False, decided_by="operator:2", decided_at=datetime.now(UTC)
        )
    assert second.status is ApprovalStatus.APPROVED


async def test_duplicate_executed_idempotency_key_is_rejected(
    factory: sessionmaker[Session],
) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        first = await _make_tool_call(session, run.id)

    with db.session_scope(factory) as session:
        await SqlToolCallStore(session).set_status(
            first, ToolCallStatus.EXECUTED, idempotency_key="refund:X:TX-88219"
        )

    # A second call, same key, marked executed -> the partial unique index fires.
    with pytest.raises(IntegrityError), db.session_scope(factory) as session:
        _, run2 = await _make_ticket_and_run(session)
        second = await _make_tool_call(session, run2.id)
        await SqlToolCallStore(session).set_status(
            second, ToolCallStatus.EXECUTED, idempotency_key="refund:X:TX-88219"
        )


async def test_proposed_calls_may_share_an_idempotency_key(factory: sessionmaker[Session]) -> None:
    # The partial index only applies where status='executed', so a proposed call
    # does not collide -- two in-flight proposals with one key is legal.
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        first = await _make_tool_call(session, run.id)
        second = await _make_tool_call(session, run.id)
        store = SqlToolCallStore(session)
        await store.set_status(first, ToolCallStatus.PROPOSED, idempotency_key="refund:X:TX-88219")
        await store.set_status(second, ToolCallStatus.PROPOSED, idempotency_key="refund:X:TX-88219")


async def test_set_status_on_executed_call_raises(factory: sessionmaker[Session]) -> None:
    from opspilot.domain.errors import ApprovalArgumentsChanged

    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        call_id = await _make_tool_call(session, run.id)
        store = SqlToolCallStore(session)
        await store.set_status(call_id, ToolCallStatus.EXECUTED, idempotency_key="k")

    with pytest.raises(ApprovalArgumentsChanged), db.session_scope(factory) as session:
        await SqlToolCallStore(session).set_status(call_id, ToolCallStatus.EXECUTED)


async def test_approval_lookup_by_tool_call(factory: sessionmaker[Session]) -> None:
    with db.session_scope(factory) as session:
        _, run = await _make_ticket_and_run(session)
        call_id = await _make_tool_call(session, run.id)
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=call_id,
            reason="r",
            risk_explanation="e",
            arguments_snapshot={"transaction_id": "TX-88219"},
            created_at=datetime.now(UTC),
        )
        await SqlApprovalStore(session).create(approval)

    with db.session_scope(factory) as session:
        found = await SqlApprovalStore(session).get_for_tool_call(call_id)
    assert found is not None
    assert found.tool_call_id == call_id
    assert found.status is ApprovalStatus.PENDING
