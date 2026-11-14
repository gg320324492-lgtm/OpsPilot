"""Re-creating a run across the idempotency boundary (Phase 3 M2-03).

## Why this file exists

``tests/integration/test_api_run_recreate.py`` pins the endpoint's *contract*
against in-memory fakes: statuses, error codes, response shape. What it cannot
pin is the property the feature is actually for -- **the cross-run boundary**.
A re-created run derives its idempotency key from its *own* run id
(``domain/policies.py``), so the MCP server's replay guard -- which is keyed on
the key alone -- does not reach from the original run to the follow-up. The
boundary is held by the workflow instead: the follow-up is a fresh ``RECEIVED``
run that must pass every gate and win a **fresh** human approval before
anything moves. That property has to be tested against the real worker, the
real stores and the real billing server, or it is a story.

So this file drives the real ``drain_once`` loop the way
``test_readme_scenarios.py`` does, with the ``harness`` fixture: real SQL
stores over in-memory SQLite, real in-process MCP servers, the fake model
provider replaying the committed ``duplicate_charge`` scenario.

## What is asserted

1. **Original already refunded** (the C2-adjacent case the milestone names):
   the original run refunds once; the follow-up re-proposes the same refund
   under a *new* key, a human approves it, and the server refuses with
   ``invalid_state`` because the transaction is already ``refunded``. The
   server ends with exactly one refund row -- the customer's card is charged
   once, across both runs.
2. **Original rejected**: the follow-up *can* complete the refund -- the
   follow-up path exists so a human can re-decide, and this is the case where
   re-deciding changes the outcome. One executed refund across both runs.
3. The API's ``original_write_status`` classification, computed by the real
   router helper against real rows: ``executed`` for the refunded original,
   ``not_executed`` for the rejected one and for the refused follow-up.
4. The ``run_recreated`` audit event, written through the SQL store's real
   recorder onto the new run's ledger.
"""

from __future__ import annotations

from opspilot.adapters.models.fake import FakeModelProvider
from opspilot.api.routers.runs import (
    OriginalWriteStatus,
    _original_write_status,
    _record_recreated_event,
)
from opspilot.domain.policies import derive_idempotency_key
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import ToolCallStatus
from tests.agent._golden_harness import DUPLICATE_TRANSACTION, Harness


async def test_a_refunded_original_recreates_into_a_fresh_approval_that_the_server_refuses(
    harness: Harness,
) -> None:
    """Original refunded -> follow-up re-approved -> server refuses: one refund.

    The property under test is the cross-run boundary: the follow-up's key is
    new (so the server's replay guard does not short-circuit it), the refund is
    refused on the transaction's state instead, and the server's store ends
    with exactly one refund row.
    """
    # 1. The original run: golden path, refunded once, COMPLETED.
    original = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    original_approval = await harness.pending_approval(original.id)
    await harness.decide(original_approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    original_final = await harness.reload(original)
    assert original_final.status is RunStatus.COMPLETED
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    assert len(harness.refund_rows_in_store()) == 1

    # The API would report the original's write as executed -- the row says so.
    assert (
        await _original_write_status(harness.runs, original_final) is OriginalWriteStatus.EXECUTED
    )

    # 2. The human follow-up: a new run on the same ticket, the way the API's
    # recreate endpoint creates it -- RECEIVED, same ticket, nothing inherited.
    harness.provider = FakeModelProvider(scenario="duplicate_charge")
    follow_up = await harness.runs.create(
        ticket_id=original_final.ticket_id, model_provider="fake", model_name="fake-1"
    )
    assert follow_up.id != original.id

    # 3. The follow-up parks on its OWN refund proposal. The original's
    # approval is not inherited: this run needs its own human decision.
    assert await harness.drain(worker_id="w-3") is True
    parked = await harness.reload(follow_up)
    assert parked.status is RunStatus.WAITING_APPROVAL, (
        f"the follow-up did not park for its own approval; got {parked.status}"
    )
    follow_up_approval = await harness.pending_approval(follow_up.id)
    assert follow_up_approval.id != original_approval.id

    follow_up_calls = harness.tool_calls(follow_up.id)
    refund_proposals = [c for c in follow_up_calls if c.tool_name == "billing.issue_refund"]
    assert len(refund_proposals) == 1
    # The key is a function of THIS run's id -- the mechanism statement: the
    # server's replay guard cannot reach across runs, which is why the
    # workflow-level guard (fresh gates, fresh approval) is the boundary.
    assert refund_proposals[0].idempotency_key == f"refund:{follow_up.id}:{DUPLICATE_TRANSACTION}"
    assert refund_proposals[0].idempotency_key != f"refund:{original.id}:{DUPLICATE_TRANSACTION}"

    # 4. A human re-decides -- approves. The server refuses: the transaction is
    # already refunded, and a new key does not change that.
    await harness.decide(follow_up_approval.id, approved=True)
    assert await harness.drain(worker_id="w-4") is True

    final = await harness.reload(follow_up)
    refund_attempts = [
        c for c in harness.tool_calls(follow_up.id) if c.tool_name == "billing.issue_refund"
    ]
    assert refund_attempts, "the approved follow-up never dispatched its refund"
    assert all(c.status != ToolCallStatus.EXECUTED.value for c in refund_attempts), (
        "the follow-up executed a second refund"
    )
    assert any(
        c.status == ToolCallStatus.FAILED.value and c.error == "invalid_state"
        for c in refund_attempts
    ), f"the server did not refuse the duplicate on the transaction's state: {refund_attempts}"

    # THE SERVER: still exactly one refund, across both runs.
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    assert len(harness.refund_rows_in_store()) == 1

    # The follow-up ended as an escalation -- a COMPLETED run whose reply says a
    # human is needed, not a silent second refund. (A refused effect-bearing
    # write sets ``escalated``; the run completes via RESPONDING.)
    assert final.status is RunStatus.COMPLETED, f"the follow-up ended {final.status}"

    # The follow-up's own write status is not_executed: its refund never moved.
    assert await _original_write_status(harness.runs, final) is OriginalWriteStatus.NOT_EXECUTED

    # And the two runs' derived keys genuinely differ, restated on the rows.
    assert derive_idempotency_key(original_final, {"transaction_id": DUPLICATE_TRANSACTION}) != (
        derive_idempotency_key(final, {"transaction_id": DUPLICATE_TRANSACTION})
    )


async def test_a_rejected_original_recreates_and_the_follow_up_completes_the_refund(
    harness: Harness,
) -> None:
    """Original rejected -> follow-up approved -> the refund happens, once.

    This is the case the follow-up path exists for: a human re-decides, and the
    re-decision changes the outcome. The original's rejection moved nothing
    (``not_executed``), so the follow-up is safe to run from scratch -- and the
    server ends with exactly one refund, from the follow-up's own approved call.
    """
    # 1. The original run: proposed, a human declined, escalated, no refund.
    original = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    original_approval = await harness.pending_approval(original.id)
    await harness.decide(original_approval.id, approved=False)
    assert await harness.drain(worker_id="w-2") is True

    original_final = await harness.reload(original)
    assert original_final.status is RunStatus.COMPLETED  # terminal -> recreatable
    assert await harness.refunded_transaction_ids() == []
    # A rejected refund never dispatched: not_executed, the safe-to-rerun answer.
    assert (
        await _original_write_status(harness.runs, original_final)
        is OriginalWriteStatus.NOT_EXECUTED
    )

    # 2. The follow-up, created the way the API creates it.
    harness.provider = FakeModelProvider(scenario="duplicate_charge")
    follow_up = await harness.runs.create(
        ticket_id=original_final.ticket_id, model_provider="fake", model_name="fake-1"
    )

    # 3. The follow-up parks, and this time the human approves.
    assert await harness.drain(worker_id="w-3") is True
    follow_up_approval = await harness.pending_approval(follow_up.id)
    await harness.decide(follow_up_approval.id, approved=True)
    assert await harness.drain(worker_id="w-4") is True

    final = await harness.reload(follow_up)
    assert final.status is RunStatus.COMPLETED, f"the follow-up ended {final.status}"

    # THE SERVER: exactly one refund -- the follow-up's, executed once.
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    refunds = harness.refund_rows_in_store()
    assert len(refunds) == 1
    executed = [
        c
        for c in harness.tool_calls(follow_up.id)
        if c.tool_name == "billing.issue_refund" and c.status == ToolCallStatus.EXECUTED.value
    ]
    assert len(executed) == 1, f"expected exactly one executed refund on the follow-up: {executed}"
    assert executed[0].idempotency_key == f"refund:{follow_up.id}:{DUPLICATE_TRANSACTION}"

    # The original's rows are untouched by the follow-up.
    original_refunds = [
        c
        for c in harness.tool_calls(original.id)
        if c.tool_name == "billing.issue_refund" and c.status == ToolCallStatus.EXECUTED.value
    ]
    assert original_refunds == [], "the rejected original executed a refund after the fact"

    # 4. The API's audit event, written through the real SQL recorder onto the
    # follow-up's ledger: who re-created, and from which original.
    await _record_recreated_event(harness.runs, original=original_final, new_run=final)
    recreated = [
        event for event in harness.audit_events(follow_up.id) if event.event_type == "run_recreated"
    ]
    assert len(recreated) == 1, harness.audit_events(follow_up.id)
    assert recreated[0].actor == "operator"
    assert recreated[0].payload == {"original_run_id": str(original.id)}
    # And nothing landed on the original's ledger: it records what that run did.
    assert not [
        event for event in harness.audit_events(original.id) if event.event_type == "run_recreated"
    ]
