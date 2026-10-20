"""The five gates run in the documented order, and the order is observable.

``docs/tool-permissions.md`` §3 specifies the sequence -- schema validation,
registry lookup, permission lookup, policy engine, approval -- and
``agents/runtime.py``'s ``_gate_and_execute`` implements it as one linear
function with a numbered comment at each gate. The order is not a comment: it is
the property that makes a *refusal* mean something, because a refusal says which
gate made it.

This file drives one run through a sequence of proposals, each of which **more
than one gate would have refused**, and asserts which gate actually refused it.
That is what "the order" means operationally: when gate 1 and gate 4 could both
refuse a proposal, the one whose label lands in ``ToolCall.rejection_reason`` is
the one that ran first.

The sequence, and what each step pins:

* Step 1 -- a registered tool with malformed arguments, whose name is on the
  denylist: gate 4 would have denied it too. Asserted: gate 1's label.
* Step 2 -- an unregistered tool name: gate 4 has no spec to evaluate.
  Asserted: gate 2's label.
* Step 3 -- a registered, well-formed, denylisted refund: gate 5 would have
  parked it for a human. Asserted: gate 4's label, and gate 3's static
  permission on the row.
* Step 4 -- a registered, well-formed refund with nothing denied. Asserted:
  gate 5 parks it -- an approval row, ``awaiting_approval``, the run in
  ``waiting_approval``.

What this file deliberately does **not** assert:

* **that gate 3 can refuse.** It cannot: gate 3 is a *lookup* of a permission
  that is static code (``domain/tools.py``'s registry). There is no
  ``gate_3`` rejection reason in the codebase and there cannot be one -- the
  ``GATE_PERMISSION`` constant exists as a label but the runtime never writes it.
  Gate 3's position in the order is therefore pinned by a different observable:
  the *permission column* on the refused row. A row refused at gate 1 or gate 2
  carries ``read`` (the least-privileged fallback ``_reject`` writes, because no
  permission was looked up); a row refused at gate 4 carries the tool's real
  static permission (``high_risk_write`` for a refund). That difference is gate 3
  having run, and it is asserted below.
* **that the relative order of gates 1 and 2 is observable.** It is not, and the
  reason is structural: gate 1 refuses *registered* tools with malformed
  arguments, and gate 2 refuses *unregistered* tools -- disjoint sets, so no
  single proposal is refused by both. What is observable is the one that
  matters, and it is asserted here: gate 1 refuses before gate 4 does (step 1),
  and gate 2 refuses a name the denylist also names (step 2).
* anything about the *content* of a refusal's reason string -- that is
  ``test_approval_bypass.py``'s ledger.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import (
    GATE_POLICY,
    GATE_REGISTRY,
    GATE_SCHEMA,
    _gate_and_execute,
)
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.errors import RunParked
from opspilot.domain.runs import RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.settings import get_settings
from opspilot.tracing.recorder import TraceRecorder

from .conftest import SpyGateway, Stores, make_context, make_run

# Two transactions, so the two refunds derive different idempotency keys.
_TX_DENIED = "TX-88219"
_TX_ALLOWED = "TX-11111"


def _recorder(stores: Stores) -> TraceRecorder:
    """A recorder writing to the same database the stores use."""
    return TraceRecorder(run_id=stores.run.id, session_factory=stores.factory)


def _tool_calls(factory: sessionmaker[Session]) -> list[models.ToolCall]:
    """Every tool call row, ordered by creation."""
    with factory() as session:
        return list(
            session.execute(select(models.ToolCall).order_by(models.ToolCall.created_at)).scalars()
        )


def _rows_with_reason(factory: sessionmaker[Session], reason: str) -> list[models.ToolCall]:
    """The tool call rows whose rejection reason is ``reason``."""
    return [row for row in _tool_calls(factory) if row.rejection_reason == reason]


async def test_the_five_gates_run_in_the_documented_order(
    factory: sessionmaker[Session],
    spy: SpyGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each proposal is refused by the earliest gate that can refuse it.

    The sequence drives one run through four proposals and checks the recorded
    outcome of each. What makes this an *ordering* test rather than four
    independent refusal tests is that the first three proposals are ones a later
    gate would also have refused: the refund is on the denylist (so gate 4 would
    have denied it), and it is a high-risk write (so gate 5 would have parked it
    for a human). The refusal that actually happens is the *earliest* one, and
    the label on the row says which gate that was.
    """
    stores = await make_run(factory)
    ctx = make_context(stores)
    recorder = _recorder(stores)

    # The denylist names the refund for the first three steps, so gate 4 would
    # refuse each of them. It is cleared before the last step, so that proposal
    # reaches gate 5 and asks a human.
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", "billing.issue_refund,billing.wire_transfer")
    get_settings.cache_clear()
    try:
        # --- Step 1: gate 1 refuses a registered tool's bad arguments ----------
        # The tool is registered (so gate 2 would have let it through) and on the
        # denylist (so gate 4 would have denied it). Gate 1 gets there first.
        await _gate_and_execute(
            ctx,
            ProposedAction(tool_name="billing.issue_refund", arguments={"amount": "129.00"}),
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
        )

        # --- Step 2: gate 2 refuses a name the registry does not have --------
        # The name is on the denylist too, so a policy engine consulted earlier
        # would have denied it. Gate 2 refuses it first.
        await _gate_and_execute(
            ctx,
            ProposedAction(tool_name="billing.wire_transfer", arguments={"amount": "1000000"}),
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
        )

        # --- Step 3: gate 4 refuses a well-formed, denylisted refund ---------
        # Gate 1 passed (the arguments are valid) and gate 2 passed (the tool is
        # registered), so this is the first gate that can refuse it -- and gate 5
        # would have parked it for a human had it got that far.
        await _gate_and_execute(
            ctx,
            ProposedAction(
                tool_name="billing.issue_refund",
                arguments={"transaction_id": _TX_DENIED, "amount": "129.00"},
            ),
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=recorder,
        )
    finally:
        get_settings.cache_clear()

    # Nothing was dispatched at any of the three refusals.
    assert spy.dispatch_count == 0

    # Exactly one row per refusal, and each names the gate that refused it.
    schema_rows = _rows_with_reason(factory, GATE_SCHEMA)
    registry_rows = _rows_with_reason(factory, GATE_REGISTRY)
    policy_rows = _rows_with_reason(factory, GATE_POLICY)
    assert len(schema_rows) == 1
    assert len(registry_rows) == 1
    assert len(policy_rows) == 1

    # Step 1's row: gate 1 refused it, so gate 3 never classified it and the row
    # carries the least-privileged permission ``_reject`` writes.
    assert schema_rows[0].tool_name == "billing.issue_refund"
    assert schema_rows[0].permission == Permission.READ.value

    # Step 2's row: gate 2 refused it, so gate 3 never classified it either.
    assert registry_rows[0].tool_name == "billing.wire_transfer"
    assert registry_rows[0].permission == Permission.READ.value

    # Step 3's row: gate 3 *did* classify it -- the permission on the row is the
    # tool's static one, not the fallback -- and then gate 4 refused it. This is
    # the observable that puts gate 3 before gate 4.
    assert policy_rows[0].tool_name == "billing.issue_refund"
    assert policy_rows[0].permission == Permission.HIGH_RISK_WRITE.value

    # Gates 1 to 4 refused the first three proposals, so none of them reached
    # gate 5: no approval row exists for any of them.
    assert await stores.approval_store.count() == 0

    # And the run is still executing -- no refusal parked it.
    persisted = await stores.run_store.get(stores.run.id)
    assert persisted is not None
    assert persisted.status is RunStatus.EXECUTING

    # --- Step 4: gate 5 is last, and it asks a human ----------------------
    # With the denylist cleared, the same shape of proposal passes gates 1 to 4
    # and reaches gate 5, which parks the run for a human.
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RunParked):
            await _gate_and_execute(
                ctx,
                ProposedAction(
                    tool_name="billing.issue_refund",
                    arguments={"transaction_id": _TX_ALLOWED, "amount": "129.00"},
                ),
                gateway=spy,
                run_store=stores.run_store,
                tool_call_store=stores.tool_call_store,
                approval_store=stores.approval_store,
                recorder=recorder,
            )
    finally:
        get_settings.cache_clear()

    # Gate 5 refused nothing -- it parked. The row says so, and it carries no
    # rejection reason, because a park is not a refusal.
    parked_rows = [
        row for row in _tool_calls(factory) if row.status == ToolCallStatus.AWAITING_APPROVAL.value
    ]
    assert len(parked_rows) == 1
    assert parked_rows[0].rejection_reason is None
    assert parked_rows[0].permission == Permission.HIGH_RISK_WRITE.value

    # A pending approval row exists and the run is waiting for a human.
    assert await stores.approval_store.count() == 1
    approval = await stores.approval_store.get_for_tool_call(parked_rows[0].id)
    assert approval is not None
    assert await stores.approval_store.has_approved(parked_rows[0].id) is False

    final_run = await stores.run_store.get(stores.run.id)
    assert final_run is not None
    assert final_run.status is RunStatus.WAITING_APPROVAL

    # Four proposals, four rows, and still nothing dispatched.
    assert len(_tool_calls(factory)) == 4
    assert spy.dispatch_count == 0
