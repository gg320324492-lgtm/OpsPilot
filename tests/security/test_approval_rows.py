"""The approval row's lifecycle: who may create one, what one authorises, and
how many may exist.

The approval row is the only thing that can authorise a high-risk write, so
three properties of it are load-bearing and each is pinned here:

1. **Only gate 5 creates one.** A tool the policy engine denies (gate 4) is
   refused before the approval gate is reached, so a denied tool never reaches
   a human: there is no approval row for a human to approve. The denial is
   recorded on the *tool call* row instead, and the run is not parked.
   ``test_a_gate_4_denial_creates_no_approval_row``.
2. **Only an *approved* row authorises.** ``has_approved`` filters on
   ``status == 'approved'``, so a row that exists is not a row that grants.
   A *pending* row is already covered by
   ``test_approval_bypass.py::test_pending_approval_does_not_authorise_execution``;
   a *rejected* row was not, and a rejected row is the one a human wrote
   deliberately. Re-entering the gates with it in place must park again, not
   execute. ``test_a_rejected_approval_does_not_authorise_execution``.
3. **One call, one row.** ``_park_run`` reuses the pending approval a call
   already has, because inserting a second one would reset a human's in-flight
   decision (and would trip the UNIQUE index on ``tool_call_id``).
   ``test_re_parking_does_not_create_a_second_approval_row``.

What this file deliberately does **not** assert:

* that a human *can* approve -- the decision endpoints are the API's business
  (``tests/integration/test_api_approvals.py``);
* that the approval row is well-formed -- the schema and its columns are
  ``docs/data-model.md``'s business;
* that a parked run is resumed after the approval -- that is the worker's
  resume path, and this file drives the gate directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import GATE_POLICY, _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import RunParked
from opspilot.domain.policies import REASON_TOOL_DENYLISTED
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import ToolCallStatus
from opspilot.settings import get_settings
from opspilot.tracing.recorder import TraceRecorder

from .conftest import SpyGateway, Stores, make_context, make_run

# The transaction the refund proposals name. It is one of ACME's two duplicate
# October charges.
_TX = "TX-88219"


def _recorder(stores: Stores) -> TraceRecorder:
    """A recorder writing to the same database the stores use."""
    return TraceRecorder(run_id=stores.run.id, session_factory=stores.factory)


def _tool_calls(factory: sessionmaker[Session]) -> list[models.ToolCall]:
    """Every tool call row, ordered by creation."""
    with factory() as session:
        return list(
            session.execute(select(models.ToolCall).order_by(models.ToolCall.created_at)).scalars()
        )


def _refund(transaction: str = _TX) -> ProposedAction:
    """A well-formed refund proposal for ``transaction``."""
    return ProposedAction(
        tool_name="billing.issue_refund",
        arguments={"transaction_id": transaction, "amount": "129.00"},
    )


async def test_a_gate_4_denial_creates_no_approval_row(
    factory: sessionmaker[Session], spy: SpyGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool the policy engine denies never reaches a human.

    Gate 4 (the policy engine) runs before gate 5 (the approval gate), so a
    denylisted refund is *refused*, not *parked*: no approval row is written for
    it, so there is nothing for an operator to approve, and the run is never
    moved to ``waiting_approval``. The refusal is recorded on the tool call
    row -- which is where the operator finds out what was blocked and why.

    The spy is the instrument: a denial that reached the gateway would have
    dispatched, and ``dispatch_count == 0`` says it did not.
    """
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", "billing.issue_refund")
    get_settings.cache_clear()
    try:
        stores = await make_run(factory)
        ctx = make_context(stores)

        record = await _gate_and_execute(
            ctx,
            _refund(),
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=_recorder(stores),
        )
    finally:
        get_settings.cache_clear()

    assert record.status == ToolCallStatus.REJECTED.value
    assert spy.dispatch_count == 0

    rows = _tool_calls(factory)
    assert len(rows) == 1
    call = rows[0]
    assert call.status == ToolCallStatus.REJECTED.value
    assert call.rejection_reason == GATE_POLICY
    assert call.error == REASON_TOOL_DENYLISTED

    # The claim under test: the denial happened at gate 4, before gate 5, so
    # there is no approval row for a human to act on.
    assert await stores.approval_store.get_for_tool_call(call.id) is None
    assert await stores.approval_store.count() == 0

    # And the run was not parked for a human.
    persisted = await stores.run_store.get(stores.run.id)
    assert persisted is not None
    assert persisted.status is RunStatus.EXECUTING
    assert ctx.run.status is RunStatus.EXECUTING


async def test_a_rejected_approval_does_not_authorise_execution(
    factory: sessionmaker[Session], spy: SpyGateway
) -> None:
    """A rejected approval is a decision, and the decision was "no".

    Gate 5 asks the store whether an *approved* request exists for this tool
    call. A rejected row is a decision a human made deliberately, and treating
    it as authorisation would execute the very thing they refused.

    So the attack is: park a refund, have a human reject it, then re-enter the
    gates for that same call. The run must park again -- the approval gate is
    still unsatisfied -- and the spy must show no dispatch.
    """
    stores = await make_run(factory)
    ctx = make_context(stores)
    recorder = _recorder(stores)
    proposal = _refund()

    with pytest.raises(RunParked):
        await _gate_and_execute(
            ctx,
            proposal,
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
        )

    call = _tool_calls(factory)[0]
    approval = await stores.approval_store.get_for_tool_call(call.id)
    assert approval is not None
    assert approval.status is ApprovalStatus.PENDING

    # A human declines.
    decided = await stores.approval_store.decide(
        approval.id,
        approved=False,
        decided_by="human@example.com",
        decided_at=datetime.now(UTC),
    )
    assert decided.status is ApprovalStatus.REJECTED

    # Re-enter the gates for that same call, with the rejected row in place.
    with pytest.raises(RunParked):
        await _gate_and_execute(
            ctx,
            proposal,
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
            tool_call_id=call.id,
        )

    assert spy.dispatch_count == 0
    assert await stores.approval_store.has_approved(call.id) is False
    assert _tool_calls(factory)[0].status == ToolCallStatus.AWAITING_APPROVAL.value

    # The rejection stands: re-entering the gates neither rewrote the human's
    # decision nor turned it into an approval.
    after = await stores.approval_store.get_for_tool_call(call.id)
    assert after is not None
    assert after.status is ApprovalStatus.REJECTED


async def test_re_parking_does_not_create_a_second_approval_row(
    factory: sessionmaker[Session], spy: SpyGateway
) -> None:
    """Re-parking a call reuses its pending approval rather than filing another.

    A parked call can be re-driven -- the worker re-enters the gates for the
    call it found waiting. ``_park_run`` reuses the pending approval that call
    already has, for two reasons: a second row would reset a human's in-flight
    decision (they would be looking at a stale card), and the UNIQUE index on
    ``tool_call_id`` would turn a legitimate re-park into a crash.

    The assertion is the count: one approval row for one tool call, before and
    after the re-park, and it is the *same* row -- same id, same creation time,
    still pending.
    """
    stores = await make_run(factory)
    ctx = make_context(stores)
    recorder = _recorder(stores)
    proposal = _refund()

    with pytest.raises(RunParked):
        await _gate_and_execute(
            ctx,
            proposal,
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
        )

    call = _tool_calls(factory)[0]
    first = await stores.approval_store.get_for_tool_call(call.id)
    assert first is not None
    assert first.status is ApprovalStatus.PENDING
    assert await stores.approval_store.count() == 1

    # Re-drive the same parked call, the way a worker resuming it would.
    with pytest.raises(RunParked):
        await _gate_and_execute(
            ctx,
            proposal,
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
            tool_call_id=call.id,
        )

    assert spy.dispatch_count == 0
    assert await stores.approval_store.count() == 1

    second = await stores.approval_store.get_for_tool_call(call.id)
    assert second is not None
    assert second.id == first.id
    assert second.created_at == first.created_at
    assert second.status is ApprovalStatus.PENDING
