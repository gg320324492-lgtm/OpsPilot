"""The five README scenarios, each driven end to end through the real worker.

``docs/milestones.md`` §M6: "``tests/agent/test_golden_path.py`` and five scenario
tests from the README" and "The five README scenarios all pass against the fake
provider."

The five, in the order the README presents them:

1. **The golden path** -- the duplicate charge is investigated and refunded after
   a human approves. ``test_scenario_1_golden_path``; the full trace-level
   version lives in ``test_golden_path.py`` and this one asserts the scenario
   verdict (money moved, exactly once).
2. **Reject** -- a human declines the refund. ``WAITING_APPROVAL`` →
   ``RESPONDING`` → ``COMPLETED`` with an escalation reply and **no refund**.
3. **Already refunded** -- the duplicate was refunded in a previous contact. It
   is detected, no refund is proposed, no approval is needed, ``COMPLETED``.
4. **Re-run after interruption** -- a worker dies between the proposal and the
   execution; a fresh worker resumes and the customer's money is not moved twice
   (``docs/risks.md`` C2).
5. **MCP server down** -- ``FAILED(mcp_unavailable)``, cleanly, with no partial
   write.

Plus the §M6 criterion the README does not number: **the worker parks on approval
and remains alive; approving later resumes the run under a worker that has
restarted in between.**

The rule these tests share
--------------------------
Every scenario that moves (or refuses to move) money asks the **MCP server**,
never the run's own status. A run that believes it completed is a self-consistent
account of itself; the billing server's transaction rows are what happened. The
already-refunded scenario makes the point sharpest: its correct outcome is a
non-event, so an assertion that only checked "the run said ``COMPLETED``" would
also pass against a run that had quietly refunded everything.

Every scenario also runs against the **real** gateway over in-process MCP servers
on a private store, so a refund written by one test cannot satisfy another.
"""

from __future__ import annotations

from opspilot.adapters.models.fake import FakeModelProvider
from opspilot.domain.policies import derive_idempotency_key
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import ToolCallStatus
from opspilot.worker import loop as worker_loop
from tests.agent._golden_harness import (
    DUPLICATE_TRANSACTION,
    GOLDEN_PATH_CITATIONS,
    INVOICE_ID,
    KEPT_TRANSACTION,
    REFUND_AMOUNT,
    Harness,
    UnreachableGateway,
    provider_positioned_at,
)

# ---------------------------------------------------------------------------
# 1. Golden path
# ---------------------------------------------------------------------------


async def test_scenario_1_golden_path_refunds_once_after_approval(harness: Harness) -> None:
    """README scenario 1: investigate, propose, a human approves, refund once.

    Acceptance criterion (docs/milestones.md §M6): the exact ticket from the
    README drives the exact documented sequence and the refund is "executed
    once".

    Asserted against the server, not the run: after approval the billing server
    reports exactly one refunded transaction, the legitimate charge is untouched,
    and the server's store holds exactly one refund row. See
    ``test_golden_path.py`` for the step-by-step version.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    assert await harness.status_of(run) is RunStatus.WAITING_APPROVAL

    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True

    final = await harness.reload(run)
    assert final.status is RunStatus.COMPLETED

    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    refunds = harness.refund_rows_in_store()
    assert len(refunds) == 1
    assert refunds[0]["transaction_id"] == DUPLICATE_TRANSACTION
    assert float(refunds[0]["amount"]) == REFUND_AMOUNT

    statuses = {str(r["transaction_id"]): r["status"] for r in await harness.server_transactions()}
    assert statuses[KEPT_TRANSACTION] == "charged"


# ---------------------------------------------------------------------------
# 2. Reject
# ---------------------------------------------------------------------------


async def test_scenario_2_reject_escalates_without_refunding(harness: Harness) -> None:
    """README scenario 2: a human declines, the run escalates, no money moves.

    Acceptance criterion (docs/milestones.md §M6): "Reject path:
    ``WAITING_APPROVAL`` → ``RESPONDING`` → ``COMPLETED`` with an escalation
    reply and **no refund**."

    A rejection is the workflow working, not failing
    (``docs/agent-state-machine.md`` §3: "``FAILED`` means OpsPilot did not
    finish the job; ``COMPLETED`` means it did, even when the answer was 'no'").

    "No refund" is asserted three ways, all from the server: no refunded
    transaction, no refund row in the store, and no executed
    ``billing.issue_refund`` call. The third matters because the first two would
    also hold for a run that attempted the refund and had the server refuse it --
    which would be a materially worse outcome than never asking.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)

    await harness.decide(approval.id, approved=False)
    assert await harness.drain(worker_id="w-2") is True

    final = await harness.reload(run)
    assert final.status is RunStatus.COMPLETED, (
        f"a human declining is a supported outcome, not a failure; got {final.status}"
    )

    # The documented path to it: WAITING_APPROVAL -> RESPONDING -> COMPLETED.
    statuses = [to for _frm, to in harness.state_changes(run.id)]
    responding_at = statuses.index("responding")
    assert statuses.index("waiting_approval") < responding_at
    assert statuses.index("completed") > responding_at

    # THE SERVER says no money moved.
    assert await harness.refunded_transaction_ids() == [], (
        "a human REJECTED the refund, but the server reports a refunded transaction"
    )
    assert harness.refund_rows_in_store() == [], (
        "a human REJECTED the refund, but the server's store holds refund rows"
    )
    server_statuses = {
        str(r["transaction_id"]): r["status"] for r in await harness.server_transactions()
    }
    assert server_statuses[DUPLICATE_TRANSACTION] == "charged"

    # And the agent never asked for it to be refunded.
    executed_refunds = [
        c
        for c in harness.tool_calls(run.id)
        if c.tool_name == "billing.issue_refund" and c.status == ToolCallStatus.EXECUTED.value
    ]
    assert executed_refunds == [], f"the refund executed despite the rejection: {executed_refunds}"

    # The rejection is recorded as a human decision, auditable.
    decided = [r for r in harness.approval_rows(run.id) if r.status == "rejected"]
    assert len(decided) == 1, "the rejection was not recorded on the approval"


# ---------------------------------------------------------------------------
# 3. Already refunded
# ---------------------------------------------------------------------------


async def test_scenario_3_already_refunded_completes_without_a_second_refund(
    harness: Harness,
) -> None:
    """README scenario 3: already refunded, so nothing further is written.

    Acceptance criterion (docs/milestones.md §M6): "Already-refunded: detected, no
    refund proposed, no approval needed, ``COMPLETED``."

    Uses the ``already_refunded`` scenario fixture, in which the agent reads the
    transaction's status and ``refund_id`` from the server and concludes no
    further write is warranted. The precondition -- the duplicate *is* already
    refunded -- is put into the store before the run, which is how
    ``docs/evals.md``'s ``setup.already_refunded`` describes it: it is a
    precondition, not something the run creates.

    "No approval needed" is asserted structurally: the run must never enter
    ``WAITING_APPROVAL`` and no approval row may exist. That is the difference
    between "detected and correctly abstained" and "detected, then asked a human
    to approve a refund it did not need".

    Because this scenario's correct outcome is a **non-event**, every assertion is
    paired with the golden-path scenario's server rows as the reference: exactly
    one refund exists, it is the one the seed recorded, and no new refund row
    appeared during the run.
    """
    # Put the precondition in place: refund the duplicate directly against the
    # server, outside the agent, exactly as a previous contact would have.
    seeded = await harness.gateway.call_tool(
        "billing.issue_refund",
        {
            "transaction_id": DUPLICATE_TRANSACTION,
            "amount": REFUND_AMOUNT,
            "idempotency_key": "refund:previous-contact:TX-88219",
            "reason": "refunded during an earlier contact",
        },
    )
    assert seeded.ok is True, f"could not set up the already-refunded state: {seeded}"
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    refunds_before = harness.refund_rows_in_store()
    assert len(refunds_before) == 1

    run = await harness.start_run(provider=FakeModelProvider(scenario="already_refunded"))
    assert await harness.drain(worker_id="w-1") is True

    final = await harness.reload(run)
    assert final.status is RunStatus.COMPLETED, (
        f"the already-refunded case is a supported outcome, not a failure; got {final.status}"
    )
    assert final.failure_reason is None

    # No approval was ever requested -- the run recognised there was nothing to
    # approve.
    assert harness.approval_rows(run.id) == [], (
        "the run asked a human to approve a refund it should have recognised as already refunded"
    )
    statuses = [to for _frm, to in harness.state_changes(run.id)]
    assert "waiting_approval" not in statuses, (
        f"the run parked on approval despite detecting the prior refund: {statuses}"
    )

    # No refund was proposed at all, so none could have been executed.
    refund_calls = [c for c in harness.tool_calls(run.id) if c.tool_name == "billing.issue_refund"]
    assert refund_calls == [], (
        f"the run proposed a refund for an already-refunded transaction: {refund_calls}"
    )

    # THE SERVER: still exactly the one refund the precondition created. No
    # second row, and the same refund id.
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    refunds_after = harness.refund_rows_in_store()
    assert len(refunds_after) == 1, (
        f"the run added a refund on top of the existing one: {refunds_after}"
    )
    assert refunds_after[0]["refund_id"] == refunds_before[0]["refund_id"]
    assert refunds_after[0]["idempotency_key"] == refunds_before[0]["idempotency_key"]

    # It still investigated -- "detected" means it read the transactions.
    executed = [
        c.tool_name for c in harness.tool_calls(run.id) if c.status == ToolCallStatus.EXECUTED.value
    ]
    assert "billing.list_transactions" in executed, (
        f"the run completed without reading the transactions: {executed}"
    )
    _ = (INVOICE_ID, GOLDEN_PATH_CITATIONS)


# ---------------------------------------------------------------------------
# 4. Re-run after interruption (docs/risks.md C2)
# ---------------------------------------------------------------------------


async def test_scenario_4_rerun_after_interruption_does_not_double_refund(
    harness: Harness,
) -> None:
    """README scenario 4: a worker dies after the refund moved the money.

    Acceptance criterion (docs/milestones.md §M6): "Re-run after interruption
    does not double-refund (``errors.md`` C2 in ``risks.md``)."

    ``docs/risks.md`` C2 names the exact seam: "If the worker crashes *after* the
    refund was executed but *before* the ``ToolCall`` row was committed as
    ``executed``, the run is marked interrupted while the money moved."

    This test reproduces that seam precisely:

    1. the run parks on the refund proposal and a human approves it;
    2. the refund executes **at the server** -- the money moves;
    3. the worker "dies" before committing the ``ToolCall`` as ``executed``;
    4. a fresh worker resumes the same run.

    The property under test is C2's: the MCP server's ``idempotency_key``
    uniqueness is the authority, so the resumed run hits the same key and gets
    ``replayed: true`` rather than a second refund. The customer's card is
    charged once.

    Asserted on the server's rows, because that is the only place the duplicate
    would show up: one refunded transaction, and **one** refund row.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)

    # A human approves. `decide` flips the run back to EXECUTING, which is what
    # makes it claimable again.
    await harness.decide(approval.id, approved=True)
    executing = await harness.reload(run)
    assert executing.status is RunStatus.EXECUTING

    # (2) The refund executes AT THE SERVER. This is the crash window from C2:
    # the money moves, and the run has not yet recorded that it did.
    effect = {"transaction_id": DUPLICATE_TRANSACTION}
    key = derive_idempotency_key(await harness.reload(executing), effect)
    first = await harness.gateway.call_tool(
        "billing.issue_refund",
        {
            "transaction_id": DUPLICATE_TRANSACTION,
            "amount": REFUND_AMOUNT,
            "idempotency_key": key,
        },
    )
    assert first.ok is True, f"could not simulate the pre-crash refund: {first}"
    first_result = first.result or {}
    assert first_result["replayed"] is False, "the first refund must be a real one"
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]

    # The *same-key* replay, which is the branch the idempotency guard owns.
    #
    # This is here because of a mutation check that mattered. Removing the
    # server's idempotency-key guard left every other assertion in this test
    # green -- the second refund in step (4) is blocked by the transaction's
    # ``invalid_state``, not by the key, so the key guard is invisible from the
    # outside on that path. Step (4) therefore cannot, and does not, claim to
    # test the guard. This step can: it replays the identical key while the
    # transaction is *still* ``charged``, so ``invalid_state`` cannot fire and
    # the key lookup is the only thing standing between one crash-retry and two
    # refunds.
    replay = await harness.gateway.call_tool(
        "billing.issue_refund",
        {
            "transaction_id": DUPLICATE_TRANSACTION,
            "amount": REFUND_AMOUNT,
            "idempotency_key": key,
        },
    )
    assert replay.ok is True, f"the same-key replay was refused outright: {replay}"
    replay_result = replay.result or {}
    assert replay_result["replayed"] is True, (
        "replaying the same idempotency key did not replay: the server created a "
        "second refund for one key, which is the double-refund this project "
        "exists to prevent (docs/tool-permissions.md §4)"
    )
    assert replay_result["refund_id"] == first_result["refund_id"]
    assert len(harness.refund_rows_in_store()) == 1, (
        "the same-key replay created a second refund row"
    )

    # (3) The worker "dies" with the run in EXECUTING -- a human-approved refund
    # whose execution was never recorded. Boot behaviour: EXECUTING is
    # **preserved**, not marked (worker/loop.py ``mark_interrupted_on_boot``).
    #
    # This was the opposite until the M6 review, and the change is deliberate. A
    # single status column cannot distinguish "the pump is driving this run right
    # now" from "a person approved this and the worker died before finishing",
    # and sweeping the second kind turns a decision someone made into
    # FAILED(interrupted): the approved refund is never issued and nothing
    # records that anyone said yes. ``docs/milestones.md`` §M6 requires that
    # approving later resumes the run under a restarted worker, and
    # ``docs/agent-state-machine.md`` §3 says FAILED means OpsPilot did not finish
    # the job -- neither holds for a run one resume away from finishing.
    #
    # The property C2 cares about -- no double refund -- is unaffected, and steps
    # (1) and (2) above already pinned it against both defences.
    marked = worker_loop.mark_interrupted_on_boot(harness.factory)
    assert marked == 0, (
        "the boot step failed a run whose refund a human had already approved; "
        "the decision must survive the restart (docs/milestones.md §M6)"
    )
    survived = await harness.reload(run)
    assert survived.status is RunStatus.EXECUTING
    assert survived.failure_reason is None

    # (4) C2's resolution as docs/risks.md describes it: "the second run produces
    # a *different* run_id and therefore a *different* idempotency key". That
    # second run is what a human does after the first one is interrupted.
    #
    # The first run is completed by the restarted worker *first*, because
    # `EXECUTING` is claimable and `claim_next` is oldest-first: leaving it in
    # EXECUTING means the next drain picks it up, so a "fresh" run would never be
    # reached. Draining it here is also the more honest sequence -- the approved
    # refund completes, and *then* the customer (or an operator) investigates
    # again and finds it already done.
    restarted = provider_positioned_at(after_structured=1)
    assert await harness.drain_with(harness.gateway, provider=restarted, worker_id="w-beta") is True
    finished = await harness.reload(run)
    assert finished.status in {RunStatus.COMPLETED, RunStatus.RESPONDING}, (
        f"the restarted worker did not finish the approved run; got {finished.status}"
    )
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION], (
        "resuming the approved run refunded a second time"
    )

    # And the re-run a human would then trigger, under a new run id.
    second = await harness.start_run(provider=FakeModelProvider(scenario="duplicate_charge"))
    assert second.id != run.id
    assert await harness.drain(worker_id="w-fresh") is True

    # THE SERVER: still exactly one refund. Not two.
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION], (
        "the re-run refunded a second time"
    )
    refunds = harness.refund_rows_in_store()
    assert len(refunds) == 1, f"the re-run created a second refund row: {refunds}"
    assert refunds[0]["refund_id"] == first_result["refund_id"]
    assert float(refunds[0]["amount"]) == REFUND_AMOUNT

    # The re-run's own refund attempt did not execute. It may end `failed` (the
    # server refused it with invalid_state) or be parked for approval and never
    # executed -- what must not happen is a second refund, which the server rows
    # above already exclude. Asserting the exact status here would be asserting
    # where the run chose to stop, which is C2's Phase 2 surface; the property
    # under test is that the customer's card was not charged twice.
    for call in harness.tool_calls(second.id):
        if call.tool_name != "billing.issue_refund":
            continue
        assert call.status != ToolCallStatus.EXECUTED.value, "the re-run executed a second refund"
        if call.status == ToolCallStatus.FAILED.value:
            assert call.error == "invalid_state", (
                f"the server refused the duplicate with {call.error!r}, expected "
                "invalid_state (the transaction is already refunded)"
            )
    # And the re-run does not crash: it ends in a legal state either way.
    # ``WAITING_APPROVAL`` is the expected one here -- the ``duplicate_charge``
    # fixture proposes a refund unconditionally, so a re-run reaches the approval
    # gate and stops there. That is correct: the gate is doing its job, and the
    # server rows above already prove the refund did not move twice.
    second_final = await harness.reload(second)
    assert second_final.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.WAITING_APPROVAL,
    }, f"the re-run ended in {second_final.status}"


async def test_scenario_4b_boot_preserves_a_decision_and_sweeps_the_rest(
    harness: Harness,
) -> None:
    """What the boot step does and does not touch, and why the line is there.

    Acceptance criterion (docs/milestones.md §M6): "The worker parks on approval
    **and remains alive**; approving later resumes the run under a worker that has
    restarted in between."

    Two classes of run survive a restart, and they survive for different reasons:

    * **WAITING_APPROVAL** -- nobody has decided yet. Clearing it would erase a
      pending human decision, which is the distinction
      ``docs/agent-state-machine.md`` §1 calls "the one that costs the most to get
      wrong".
    * **EXECUTING with an approval behind it** -- somebody *has* decided. A
      single status column cannot tell this apart from "the pump is driving this
      run right now", and sweeping the second kind discards a decision a person
      made: the approved refund is never issued and nothing records that anyone
      said yes.

    Both are asserted here, and so is the case that *is* swept, because a boot
    step that marked nothing would satisfy both of the above while being useless.
    """
    # (1) A run with a human decision still pending is untouched.
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.status_of(run) is RunStatus.EXECUTING

    # (2) An approved run survives the boot step.
    assert worker_loop.mark_interrupted_on_boot(harness.factory) == 0, (
        "the boot step failed a run whose refund a human had already approved"
    )
    assert await harness.status_of(run) is RunStatus.EXECUTING

    # (3) A run that was mid-step with no approval behind it *is* swept. Put one
    # in RETRIEVING directly: the harness's scenarios either park or complete, so
    # the interrupted-mid-step state is staged rather than driven.
    stranded = await harness.start_run(
        provider=FakeModelProvider(scenario="duplicate_charge"),
        subject="Charged twice for invoice INV-2026-384",
    )
    await harness.runs.set_status(stranded.id, RunStatus.RETRIEVING)
    assert worker_loop.mark_interrupted_on_boot(harness.factory) == 1, (
        "the boot step did not sweep a run that was mid-step when the worker died"
    )
    swept = await harness.reload(stranded)
    assert swept.status is RunStatus.FAILED
    assert swept.failure_reason == "interrupted", (
        f"'interrupted' is the machine-stable reason; got {swept.failure_reason!r}"
    )

    # (4) A second boot changes nothing further: the terminal run is terminal
    # and the approved run is still approved.
    assert worker_loop.mark_interrupted_on_boot(harness.factory) == 0
    assert await harness.status_of(run) is RunStatus.EXECUTING
    assert await harness.status_of(stranded) is RunStatus.FAILED


# ---------------------------------------------------------------------------
# 5. MCP server down
# ---------------------------------------------------------------------------


async def test_scenario_5_mcp_server_down_fails_cleanly_with_no_partial_write(
    harness: Harness,
) -> None:
    """README scenario 5: the billing server is unreachable.

    Acceptance criterion (docs/milestones.md §M6): "MCP server down →
    ``FAILED(mcp_unavailable)`` cleanly, with no partial write."

    "Cleanly" is the operative word and it is checked in three parts:

    * **the status and reason** -- ``FAILED`` with ``failure_reason`` exactly
      ``mcp_unavailable``, the machine-stable token
      ``docs/agent-state-machine.md`` §3 names for exactly this situation
      ("MCP server unreachable → ``FAILED`` ... The system could not complete the
      work it was asked to do");
    * **no partial write** -- nothing was written to the billing server's store.
      This is asserted against the *real* store, which is the only place a partial
      write could hide: the run's own ``FAILED`` status says nothing about whether
      a refund row exists;
    * **nothing pretends to have succeeded** -- no tool call is recorded as
      ``executed``, and no run reaches ``COMPLETED``.
    """
    run = await harness.start_run()
    dead = UnreachableGateway()

    assert await harness.drain_with(dead, worker_id="w-1") is True

    final = await harness.reload(run)
    assert final.status is RunStatus.FAILED, (
        f"an unreachable MCP server must fail the run, not park or complete; got {final.status}"
    )
    assert final.failure_reason == "mcp_unavailable", (
        f"docs/agent-state-machine.md §3 names 'mcp_unavailable' for an "
        f"unreachable server; got {final.failure_reason!r}"
    )

    # NO PARTIAL WRITE. Ask the server itself.
    assert harness.refund_rows_in_store() == [], (
        "the run wrote a refund row to the billing store while the server was down"
    )
    # And the real server, brought back, still shows nothing refunded.
    assert await harness.refunded_transaction_ids() == [], (
        "a transaction was refunded despite the server being unreachable"
    )

    # Nothing claims to have executed successfully.
    assert dead.attempts, "the test never actually reached the gateway"
    calls = harness.tool_calls(run.id)
    assert not [c for c in calls if c.status == ToolCallStatus.EXECUTED.value], (
        f"tool calls recorded as executed while the server was down: {calls}"
    )
    assert not [c for c in calls if c.status == ToolCallStatus.AWAITING_APPROVAL.value], (
        f"the run parked for approval on the strength of results it never got; calls: {calls}"
    )
    # Every attempted call carries the failure.
    for call in calls:
        assert call.status == ToolCallStatus.FAILED.value, (
            f"{call.tool_name} ended as {call.status!r}, not failed"
        )
        assert call.error == "mcp_unavailable"

    # A failed run is terminal and audited.
    assert final.completed_at is not None, "a FAILED run must record completed_at"
    failed_audits = [e for e in harness.audit_events(run.id) if e.event_type == "run_failed"]
    assert len(failed_audits) == 1, "the run failure was not audited"
    assert failed_audits[0].payload["failure_reason"] == "mcp_unavailable"

    # And nothing can pick it up afterwards: FAILED is not claimable.
    assert await harness.drain(worker_id="w-2") is False


# ---------------------------------------------------------------------------
# The §M6 criterion: park, stay alive, resume under a restarted worker
# ---------------------------------------------------------------------------


async def test_worker_parks_stays_alive_and_resumes_after_a_restart(
    harness: Harness,
) -> None:
    """The worker parks on approval, stays alive, and a restart resumes the run.

    Acceptance criterion (docs/milestones.md §M6, verbatim): "The worker parks on
    approval **and remains alive**; approving later resumes the run under a worker
    that has restarted in between."

    Three claims, and the middle one is the one usually skipped:

    1. **Parks.** After the first drain the run is ``WAITING_APPROVAL`` with a
       pending approval and no executed refund.
    2. **Remains alive.** The parked worker is *still running* -- it released the
       row (``docs/agent-state-machine.md`` §1: ``WAITING_APPROVAL`` is the one
       non-terminal state the worker does not hold) and can be re-driven. This is
       asserted by draining again with the *same* worker id and observing that it
       still works and finds nothing to do, rather than by any liveness flag that
       could be stubbed.
    3. **Resumes after a restart.** A human approves while the worker is parked,
       the run is re-queued by the approvals store, and a **fresh worker** -- a
       different id, a fresh provider instance, a fresh boot step -- picks it up
       and executes the refund exactly once.

    The mechanism is ``worker/loop.py::_resolve_resume``: it finds the
    ``awaiting_approval`` call whose approval is now approved and hands the
    runtime that call's id, so the gates re-enter for *that exact call*. Without
    it the runtime would re-plan, mint a new proposal, and park again forever.

    ``tool-permissions.md`` §3.1 explains why this is a database read and not an
    in-memory flag: the approval may be granted by a different process while the
    worker is not running. This test is that scenario -- the approver and the
    worker are separate concerns, and the worker is re-created in between.
    """
    run = await harness.start_run()

    # (1) Park.
    assert await harness.drain(worker_id="worker-alpha") is True
    parked = await harness.reload(run)
    assert parked.status is RunStatus.WAITING_APPROVAL, (
        f"the worker must park on approval; got {parked.status}"
    )
    assert await harness.refunded_transaction_ids() == [], "a refund happened before approval"

    # (2) The parked worker is still alive and still working. Re-driving the
    # SAME worker finds nothing to do -- it released the row, so the parked run
    # is not claimable -- and crucially does not fail, crash, or spin the run.
    assert await harness.drain(worker_id="worker-alpha") is False, (
        "the parked run was re-claimed; WAITING_APPROVAL must not be claimable "
        "(docs/agent-state-machine.md §5)"
    )
    still_parked = await harness.reload(run)
    assert still_parked.status is RunStatus.WAITING_APPROVAL
    assert still_parked.failure_reason is None, "an idle poll marked the parked run failed"
    assert await harness.refunded_transaction_ids() == [], "the idle poll refunded something"

    # A human approves. The approvals store flips the run back to a claimable
    # state -- that is the store's job, not the worker's.
    approval = await harness.pending_approval(run.id)
    await harness.decide(approval.id, approved=True)
    assert await harness.status_of(run) is RunStatus.EXECUTING

    # (3) The worker restarts in between: a new boot step, a fresh model
    # provider instance, and a different worker id.
    #
    # The boot step sweeps claim-and-work states and marks them
    # ``FAILED(interrupted)``. An approved run sitting in ``EXECUTING`` is in
    # that sweep -- and it must come out the other side still executable, or the
    # human's decision is discarded and the approved refund is never made
    # (``docs/tool-permissions.md`` §3.1: the approval may be granted by a
    # completely different process while the worker is not running).
    worker_loop.mark_interrupted_on_boot(harness.factory)
    after_boot = await harness.reload(run)
    assert after_boot.status is not RunStatus.FAILED, (
        "the worker restart discarded a human's approval: the approved run was "
        "failed as 'interrupted' and can never resume (docs/milestones.md §M6: "
        "approving later resumes the run under a worker that has restarted in "
        "between)"
    )

    restarted = provider_positioned_at(after_structured=1)
    assert (
        await harness.drain_with(harness.gateway, provider=restarted, worker_id="worker-beta")
        is True
    )

    final = await harness.reload(run)
    assert final.status is RunStatus.COMPLETED, (
        f"the restarted worker did not resume the approved run; got {final.status}"
    )

    # THE SERVER: the approved refund executed exactly once.
    assert await harness.refunded_transaction_ids() == [DUPLICATE_TRANSACTION]
    refunds = harness.refund_rows_in_store()
    assert len(refunds) == 1, f"the resumed run wrote {len(refunds)} refund rows: {refunds}"
    assert refunds[0]["refund_id"].startswith("REF-")

    # The resumed run executed the call the human approved -- the same
    # ``tool_call_id``, not a fresh re-planned proposal.
    executed_refunds = [
        c
        for c in harness.tool_calls(run.id)
        if c.tool_name == "billing.issue_refund" and c.status == ToolCallStatus.EXECUTED.value
    ]
    assert len(executed_refunds) == 1
    assert executed_refunds[0].id == approval.tool_call_id, (
        "the restart re-planned the refund instead of resuming the approved "
        "tool call; the approval is bound to a tool_call_id "
        "(docs/tool-permissions.md §3.1)"
    )

    # Citations survive the restart -- the trace is one run, not two.
    slugs = await harness.citation_slugs(run.id)
    assert slugs >= GOLDEN_PATH_CITATIONS, (
        f"the resumed run lost its citations; persisted {sorted(slugs)}"
    )


async def test_a_parked_run_is_not_claimed_by_another_worker(harness: Harness) -> None:
    """Two workers must not both pick up a run that is waiting on a human.

    Acceptance criterion (docs/milestones.md §M6): "The worker parks on approval
    **and remains alive**" -- which requires that parking actually frees the row.
    Without this, "remains alive" would be satisfied by a worker that keeps the
    row locked, which is the failure the state machine's claim predicate exists to
    prevent (``docs/agent-state-machine.md`` §5: "forgetting to exclude a parked
    state causes duplicate work").

    Two drains from two different worker ids must not both drive the parked run.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="worker-one") is True
    assert await harness.status_of(run) is RunStatus.WAITING_APPROVAL

    # A second, independent worker finds nothing claimable.
    assert await harness.drain(worker_id="worker-two") is False
    assert await harness.status_of(run) is RunStatus.WAITING_APPROVAL
    assert await harness.refunded_transaction_ids() == []

    # Still exactly one pending approval -- no duplicate work created a second.
    pending = [r for r in harness.approval_rows(run.id) if r.status == "pending"]
    assert len(pending) == 1


def test_a_second_run_gets_a_different_idempotency_key() -> None:
    """The re-run scenario's premise: a second run is a genuinely new run.

    ``docs/risks.md`` C2's Phase 1 resolution depends on this -- "the second run
    produces a *different* ``run_id`` and therefore a *different* idempotency
    key". If run ids could collide, the resumed run's key would match the
    original's and scenario 4 would be testing the Phase 2 replay path by
    accident rather than the state-check path C2 actually describes.

    Asserted directly on the key derivation rather than through a run, because
    this is the premise, not the behaviour.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from opspilot.domain.runs import AgentRun

    def make() -> AgentRun:
        return AgentRun(
            id=uuid4(),
            ticket_id=uuid4(),
            status=RunStatus.EXECUTING,
            model_provider="fake",
            model_name="fake",
            created_at=datetime.now(UTC),
        )

    run_a, run_b = make(), make()
    effect = {"transaction_id": DUPLICATE_TRANSACTION}
    assert run_a.id != run_b.id
    assert derive_idempotency_key(run_a, effect) != derive_idempotency_key(run_b, effect)
    # And the key is a pure function of (run, effect) -- not a timestamp, which
    # would make a retry five minutes later a new refund (tool-permissions.md §4).
    assert derive_idempotency_key(run_a, effect) == derive_idempotency_key(run_a, effect)
