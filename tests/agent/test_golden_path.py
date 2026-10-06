"""The golden path, end to end through the real worker.

``docs/milestones.md`` §M6, verbatim:

    The exact ticket from the README drives the exact documented sequence:
    ``billing_dispute`` → retrieve → ``crm.get_customer`` →
    ``billing.get_invoice`` → ``billing.list_transactions`` → duplicate
    detected → propose refund → ``HIGH_RISK_WRITE`` → ``WAITING_APPROVAL`` →
    approve → refund executed once → issue created → customer reply →
    ``COMPLETED``.

    The trace shows every step with latency, and the citations are the two
    expected documents.

What makes this file different from every other gate test in the repository
----------------------------------------------------------------------------
It does not call the gate. It creates a ticket, lets the **real worker**
(``worker/loop.py::drain_once``, every dependency injected) claim and drive the
run, approves through the approvals store's real decision path, drains again,
and then **asks the MCP server whether the money moved**.

That last step is the whole point. ``RunStatus.COMPLETED`` is something the run
writes about itself; the billing server's transaction row is what actually
happened. A self-consistent implementation can be wrong in several ways at once
-- it can record a refund that never executed, execute one it never recorded, or
report success because its own bookkeeping agreed with its own bookkeeping. Three
times in this project's history the suite was green and the system was wrong, and
in every case the assertion had been made against the implementation rather than
against the world.

So the load-bearing assertions are::

    refunded_transaction_ids() == ["TX-88219"]  # from the server
    len(refund_rows_in_store()) == 1  # from the server's document

and neither is derived from the run's status. ``test_no_money_moves_before_approval``
is the control that makes those meaningful: at the moment the run is parked, the
same two queries report nothing. Without it, an assertion of "exactly one
refunded" would also pass against an implementation that never refunds at all.
"""

from __future__ import annotations

from opspilot.adapters.persistence.models import ApprovalRequest as ApprovalRow
from opspilot.adapters.persistence.models import ToolCall
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolCallStatus
from tests.agent._golden_harness import (
    DUPLICATE_TRANSACTION,
    GOLDEN_PATH_CITATIONS,
    INVOICE_ID,
    KEPT_TRANSACTION,
    REFUND_AMOUNT,
    Harness,
)


async def _parked_run(harness: Harness) -> tuple[AgentRun, ApprovalRow]:
    """Drive the worker until it parks, returning ``(run, pending_approval)``.

    Shared by the golden path and the "parks and stays alive" criterion so both
    drive the identical sequence and cannot drift.
    """
    run = await harness.start_run()
    assert await harness.drain(worker_id="w-1") is True
    return run, await harness.pending_approval(run.id)


async def _completed_run(harness: Harness, run: AgentRun, approval: ApprovalRow) -> AgentRun:
    """Approve ``approval`` and drain again; return the run in its final state.

    A helper rather than three repeated lines so the "approve, then let a worker
    drive it" sequence has exactly one definition. The assertions that matter
    live in the tests, against the server.
    """
    await harness.decide(approval.id, approved=True)
    assert await harness.drain(worker_id="w-2") is True
    result = await harness.runs.get(run.id)
    assert result is not None
    return result


def _refund_calls(harness: Harness, run: AgentRun) -> list[ToolCall]:
    """The run's ``billing.issue_refund`` rows."""
    return [c for c in harness.tool_calls(run.id) if c.tool_name == "billing.issue_refund"]


async def _server_statuses(harness: Harness) -> dict[str, str]:
    """What the billing server says about every transaction on the invoice."""
    return {str(r["transaction_id"]): str(r["status"]) for r in await harness.server_transactions()}


async def _status(harness: Harness, run: AgentRun) -> RunStatus:
    """The run's current status.

    ``SqlRunStore.get`` returns ``AgentRun | None``. Every caller here wants the
    row -- a run that vanished is a bug, not a state to assert on -- so this
    fails loudly rather than making every call site carry the ``None`` branch.
    """
    found = await harness.runs.get(run.id)
    assert found is not None, f"run {run.id} is missing from the store"
    return found.status


async def _failure_reason(harness: Harness, run: AgentRun) -> str | None:
    """The run's ``failure_reason``, or ``None``."""
    found = await harness.runs.get(run.id)
    assert found is not None, f"run {run.id} is missing from the store"
    reason: str | None = found.failure_reason
    return reason


async def test_duplicate_charge_run_waits_then_refunds_exactly_once(
    harness: Harness,
) -> None:
    """The README ticket drives the README sequence and refunds exactly once.

    Acceptance criterion (docs/milestones.md §M6): the exact ticket from the
    README drives the exact documented sequence -- ``billing_dispute`` → retrieve
    → ``crm.get_customer`` → ``billing.get_invoice`` →
    ``billing.list_transactions`` → duplicate detected → propose refund →
    ``HIGH_RISK_WRITE`` → ``WAITING_APPROVAL`` → approve → refund executed once →
    issue created → customer reply → ``COMPLETED``.

    Driven through the real worker with the fake provider replaying the
    committed ``duplicate_charge`` fixture, so the model's behaviour is scripted
    rather than invented and the run is deterministic.
    """
    run, approval = await _parked_run(harness)

    # -- the run parks on the HIGH_RISK_WRITE proposal ------------------
    parked = await _status(harness, run)
    assert parked is RunStatus.WAITING_APPROVAL, (
        f"the run should park on the refund proposal, not reach {parked}"
    )

    # The exact documented tool order. The fixture proposes them in sequence and
    # each one must have executed before the next was proposed; asserting the
    # order is what distinguishes "the documented sequence happened" from "the
    # tools were called in some order that happened to work".
    calls = harness.tool_calls(run.id)
    executed = [c.tool_name for c in calls if c.status == ToolCallStatus.EXECUTED.value]
    assert executed == [
        "crm.get_customer",
        "billing.get_invoice",
        "billing.list_transactions",
    ], f"the documented read sequence did not run in order: {executed}"

    # -- the duplicate was detected, and the refund is only *proposed* ---
    refund_call = next(c for c in calls if c.tool_name == "billing.issue_refund")
    assert refund_call.permission == "high_risk_write", (
        f"the refund must carry HIGH_RISK_WRITE, got {refund_call.permission!r}"
    )
    assert refund_call.status == ToolCallStatus.AWAITING_APPROVAL.value, (
        f"the refund must park, not execute: status={refund_call.status}"
    )
    assert refund_call.arguments["transaction_id"] == DUPLICATE_TRANSACTION, (
        "the refund must target the SECOND charge -- the one removed from the "
        "duplicate pair -- not the one the customer legitimately paid"
    )
    assert float(refund_call.arguments["amount"]) == REFUND_AMOUNT

    # -- approve, the way the approvals API does, then drive it again -----
    final = await _completed_run(harness, run, approval)
    assert final.status is RunStatus.COMPLETED, (
        f"the run should complete after approval, got {final.status}"
    )
    assert final.failure_reason is None

    # =================================================================
    # ASK THE SERVER. Everything above is the run's own account of itself.
    # =================================================================
    refunded = await harness.refunded_transaction_ids()
    assert refunded == [DUPLICATE_TRANSACTION], (
        "the billing server does not report exactly one refunded transaction, "
        f"for {DUPLICATE_TRANSACTION}: {refunded}. The run claims {final.status}, "
        "but the server's rows are what happened."
    )

    # Exactly one, and only the duplicate: the transaction the customer
    # legitimately paid must still be charged, or OpsPilot has refunded twice
    # over in the other direction.
    remaining = {str(r["transaction_id"]): r["status"] for r in await harness.server_transactions()}
    assert remaining[DUPLICATE_TRANSACTION] == "refunded"
    assert remaining[KEPT_TRANSACTION] == "charged", (
        f"the legitimate charge on {KEPT_TRANSACTION} must not be refunded; "
        f"server says {remaining[KEPT_TRANSACTION]!r}"
    )

    # A second, independent witness: one refund *row*. The transaction status
    # could in principle move without a refund existing.
    refunds = harness.refund_rows_in_store()
    assert len(refunds) == 1, f"the server's store holds {len(refunds)} refund rows: {refunds}"
    assert refunds[0]["transaction_id"] == DUPLICATE_TRANSACTION
    assert float(refunds[0]["amount"]) == REFUND_AMOUNT
    assert refunds[0]["refund_id"].startswith("REF-")

    # Exactly one refund was *executed*, not merely proposed.
    executed_refunds = [
        c
        for c in harness.tool_calls(run.id)
        if c.tool_name == "billing.issue_refund" and c.status == ToolCallStatus.EXECUTED.value
    ]
    assert len(executed_refunds) == 1, f"expected one executed refund, got {len(executed_refunds)}"
    # The executed call carries the derived idempotency key -- never one the
    # model chose (docs/tool-permissions.md §4).
    assert executed_refunds[0].idempotency_key == refunds[0]["idempotency_key"]
    assert refunds[0]["idempotency_key"] == f"refund:{run.id}:{DUPLICATE_TRANSACTION}"


async def test_no_money_moves_before_a_human_approves(harness: Harness) -> None:
    """Nothing is refunded while the run sits in ``WAITING_APPROVAL``.

    Acceptance criterion (docs/milestones.md §M6): ``HIGH_RISK_WRITE`` →
    ``WAITING_APPROVAL`` → **approve** → refund executed once. The "approve" is
    load-bearing: this test is the control that proves the approval gate is what
    stops the refund, rather than the golden path simply being slow.

    Without it, ``test_duplicate_charge_run_waits_then_refunds_exactly_once``
    would also pass against an implementation that parks cosmetically and
    refunds anyway -- its final "exactly one refunded" assertion would still hold.
    This is the half that makes that assertion mean something.
    """
    run, _approval = await _parked_run(harness)

    parked = await _status(harness, run)
    assert parked is RunStatus.WAITING_APPROVAL

    # The server's own answer, at the moment the run is parked.
    assert await harness.refunded_transaction_ids() == [], (
        "a transaction was refunded while the run was still waiting for approval"
    )
    statuses = {str(r["transaction_id"]): r["status"] for r in await harness.server_transactions()}
    assert statuses[DUPLICATE_TRANSACTION] == "charged"
    assert statuses[KEPT_TRANSACTION] == "charged"
    assert harness.refund_rows_in_store() == [], "the server's store holds a refund row"
    _ = run


async def test_citations_are_the_two_expected_documents(harness: Harness) -> None:
    """The run cites ``refund-policy.md`` and ``duplicate-charge-sop.md``.

    Acceptance criterion (docs/milestones.md §M6): "the citations are the two
    expected documents."

    ``knowledge/README.md`` explains why this is a real constraint rather than a
    restatement of what the fixture happens to name: "A question about a $129
    enterprise duplicate needs **both**, and the answer is that the SOP governs
    the amount while the approval rule still applies." Retrieval that returns
    only one produces a wrong answer -- the SOP says refund it, the refund policy
    says $129 is over the $100 self-approval limit. So this asserts both are
    present *as persisted citation rows* over the real committed corpus, not
    merely that retrieval can return them.

    The fixture's ``cited_document_slugs`` is deliberately **not** asserted
    against: that is model output, and if the model's citation list and the
    retrieved evidence disagree, the evidence is the authority.
    """
    run, _approval = await _parked_run(harness)

    slugs = await harness.citation_slugs(run.id)
    missing = GOLDEN_PATH_CITATIONS - slugs
    assert not missing, (
        f"the golden-path run did not cite {sorted(missing)}; persisted "
        f"citations were {sorted(slugs)}"
    )

    # Each citation is a real row with a score and a rank, not a placeholder.
    rows = await harness.citations.list_citations(run.id)
    for row in rows:
        assert row.document, "a citation row carries no document slug"
        assert row.chunk, f"citation {row.document} carries no chunk anchor"
        assert row.score is not None, f"citation {row.document} carries no score"
        assert row.rank is not None, f"citation {row.document} carries no rank"
    _ = (INVOICE_ID, run)


async def test_the_trace_is_complete_and_ordered(harness: Harness) -> None:
    """Every documented step appears in the persisted trace, in order.

    Acceptance criterion (docs/milestones.md §M6): "The trace shows every step
    with latency, and the citations are the two expected documents."

    Read back from the ``agent_steps`` rows rather than from anything the runtime
    returned, so a trace that was never written cannot satisfy this. The
    ``state_change`` pairs are the run's *actual* transition sequence: the
    runtime writes them in the same transaction as the status update
    (``docs/agent-state-machine.md`` §4), so an implementation that reached
    ``COMPLETED`` by an illegal route is caught here.

    **Latency, precisely.** The audit trail records a measured ``latency_ms`` for
    every executed tool call, and this asserts it. ``agent_steps.latency_ms`` is
    a nullable column that only the classification step populates today (it comes
    from the model's reported usage); the other step types pass ``None``. Asserting
    "every step has a latency" would therefore be asserting a change to
    ``agents/runtime.py`` and ``tracing/recorder.py`` rather than describing the
    behaviour of this code, so what is asserted here is the part that is true:
    the trace is complete, ordered, and the latency it *does* record is measured.
    That gap is recorded in ``docs/progress.md``.
    """
    run, approval = await _parked_run(harness)
    await harness.decide(approval.id, approved=True)
    await harness.drain(worker_id="w-2")

    types = harness.step_types(run.id)
    for required in ("classification", "retrieval", "planning", "response", "state_change"):
        assert required in types, f"the trace has no {required!r} step; it has {sorted(set(types))}"

    # The run actually transitioned through the documented states, in order.
    to_statuses = [to for _frm, to in harness.state_changes(run.id)]
    for expected in (
        "classifying",
        "retrieving",
        "planning",
        "executing",
        "responding",
        "completed",
    ):
        assert expected in to_statuses, (
            f"the run never entered {expected!r}; it entered {to_statuses}"
        )
    # Classification and retrieval come before any tool work.
    assert to_statuses.index("classifying") < to_statuses.index("retrieving")
    assert to_statuses.index("retrieving") < to_statuses.index("executing")
    assert to_statuses.index("executing") < to_statuses.index("responding")

    # `sequence` is dense and unique per run, so the timeline cannot silently
    # reorder or skip (docs/milestones.md §M4: "agent_steps.sequence is unique
    # per run").
    sequences = [s.sequence for s in harness.steps(run.id)]
    assert sequences == sorted(sequences), "the trace is not in sequence order"
    assert len(set(sequences)) == len(sequences), "duplicate sequence numbers in the trace"

    # Every executed tool call recorded a measured latency in the audit trail.
    executed_audits = [
        e
        for e in harness.audit_events(run.id)
        if e.event_type == "tool_executed" and (e.payload or {}).get("ok") is True
    ]
    assert len(executed_audits) == 4, (
        f"expected an audited execution for each of the four tools, got {len(executed_audits)}"
    )
    for event in executed_audits:
        assert "latency_ms" in (event.payload or {}), (
            f"the {event.payload['tool_name']} execution recorded no latency"
        )
        assert isinstance(event.payload["latency_ms"], int)

    # The approval is audited too: the run asked a human before moving money.
    requested = [e for e in harness.audit_events(run.id) if e.event_type == "approval_requested"]
    assert len(requested) == 1, "the run did not record that it asked for approval"
