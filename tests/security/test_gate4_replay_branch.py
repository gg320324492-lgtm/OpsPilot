"""Gate 4's idempotent-replay branch, and the invariant it qualifies.

``agents/runtime.py``'s module docstring states the invariant the module exists
to uphold: every ``HIGH_RISK_WRITE`` call with status ``executed`` has an
approved ``ApprovalRequest`` beside it. One branch is the exception, and the
docstring now says so -- gate 4's idempotent-replay branch returns before gate
5, so the row it writes is ``executed`` with no approval row beside it.

That branch is a *replay*: the side effect already happened for this key, so
there is nothing new for a human to approve. It is also not the branch
production takes. The worker injects ``executed_lookup_for(tool_call_store)``,
which asks for the ``executed`` rows holding the key -- the same rows
``find_executed_call_id`` asks -- so a replay always finds the row that holds
the key, and the runtime records the duplicate as ``rejected`` instead.
Nothing executed, so nothing needed approving.

The two tests here are the two halves of that claim:

* ``test_the_production_lookup_takes_the_rejecting_branch`` proves the first
  half: with the production lookup, a replay is rejected, and the CI invariant
  query (``tests/security/test_invariants.py``) still counts zero violations.
* The other branch is driven by
  ``tests/security/test_approval_bypass.py::test_replay_on_one_key_does_not_dispatch_twice``,
  which injects a lookup that reports a prior execution no row backs and
  asserts the call is ``executed`` -- the exception the docstring names.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import (
    GATE_POLICY,
    _gate_and_execute,
    executed_lookup_for,
)
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.policies import REASON_IDEMPOTENT_REPLAY, derive_idempotency_key
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.tracing.recorder import TraceRecorder

from .conftest import SpyGateway, make_context, make_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = REPO_ROOT / "src" / "opspilot" / "agents" / "runtime.py"

_TX = "TX-88219"
_CHARGED = "129.00"


async def test_the_production_lookup_takes_the_rejecting_branch(
    factory: sessionmaker[Session], spy: SpyGateway
) -> None:
    """A replay found by the production lookup is recorded as rejected.

    The prior execution is a real row in the same database the lookup reads,
    with the approval that authorised it -- which is the whole point: the
    lookup cannot report a prior execution that the store does not also find,
    so the runtime never reaches the branch that marks a replay ``executed``
    without an approval.

    The query at the end is the CI one, taken from
    ``tests/security/test_invariants.py``: an ``executed`` ``HIGH_RISK_WRITE``
    row with no approved approval beside it. It must count zero.
    """
    stores = await make_run(factory)
    ctx = make_context(stores)
    arguments: dict[str, object] = {"transaction_id": _TX, "amount": _CHARGED}
    key = derive_idempotency_key(stores.run, arguments)

    # The prior execution: a real executed row holding the derived key, and the
    # approval a human gave for it.
    prior_id = await stores.tool_call_store.record_proposed(
        run_id=stores.run.id,
        tool_name="billing.issue_refund",
        arguments=arguments,
        permission=Permission.HIGH_RISK_WRITE.value,
    )
    await stores.tool_call_store.set_status(
        prior_id,
        ToolCallStatus.EXECUTED,
        result={"refund_id": "REF-10091", "replayed": False},
        idempotency_key=key,
    )
    approval = ApprovalRequest(
        id=uuid4(),
        run_id=stores.run.id,
        tool_call_id=prior_id,
        reason="the customer was charged twice",
        risk_explanation="This is a high-risk write and cannot be undone automatically.",
        arguments_snapshot=dict(arguments),
        created_at=datetime.now(UTC),
    )
    await stores.approval_store.create(approval)
    await stores.approval_store.decide(
        approval.id,
        approved=True,
        decided_by="operator@example",
        decided_at=datetime.now(UTC),
    )

    record = await _gate_and_execute(
        ctx,
        ProposedAction(tool_name="billing.issue_refund", arguments=arguments),
        gateway=spy,
        run_store=stores.run_store,
        tool_call_store=stores.tool_call_store,
        approval_store=stores.approval_store,
        recorder=TraceRecorder(run_id=stores.run.id, session_factory=stores.factory),
        executed_lookup=executed_lookup_for(stores.tool_call_store),
    )

    # The replay did not execute and did not park: it was refused at gate 4.
    assert record.status == ToolCallStatus.REJECTED.value
    assert spy.dispatch_count == 0

    with factory() as session:
        rows = list(
            session.execute(select(models.ToolCall).order_by(models.ToolCall.created_at)).scalars()
        )
        approvals = session.execute(
            select(func.count()).select_from(models.ApprovalRequest)
        ).scalar_one()
    assert len(rows) == 2, "the replay must be a second row, not a second state"
    replayed = rows[-1]
    assert replayed.status == ToolCallStatus.REJECTED.value
    assert replayed.rejection_reason == GATE_POLICY
    assert replayed.error == REASON_IDEMPOTENT_REPLAY
    assert approvals == 1, "the replay asked for a second approval; it executed nothing"

    # The CI invariant query, copied from ``tests/security/test_invariants.py``.
    with factory() as session:
        approved_exists = exists().where(
            (models.ApprovalRequest.tool_call_id == models.ToolCall.id)
            & (models.ApprovalRequest.status == ApprovalStatus.APPROVED.value)
        )
        violations = session.execute(
            select(func.count())
            .select_from(models.ToolCall)
            .where(
                models.ToolCall.permission == Permission.HIGH_RISK_WRITE.value,
                models.ToolCall.status == ToolCallStatus.EXECUTED.value,
                ~approved_exists,
            )
        ).scalar_one()
    assert violations == 0


def _module_docstring() -> str:
    """The module docstring of ``agents/runtime.py``."""
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"), filename="runtime.py")
    return ast.get_docstring(tree) or ""


def test_the_module_docstring_qualifies_the_invariant_with_the_replay_branch() -> None:
    """The invariant is still stated, and its one exception is named.

    The docstring claimed the invariant without exception, while gate 4's
    replay branch breaks it -- so the claim and the code could not both be
    true. It now states the invariant *and* the branch that is the exception,
    and says which branch production takes instead.
    """
    docstring = _module_docstring()
    assert docstring, "the runtime module has no docstring; this guard is vacuous"

    # The invariant itself is still claimed -- the fix qualifies it, it does
    # not delete it.
    assert "For every tool call with permission HIGH_RISK_WRITE and status executed" in docstring
    assert "Count of violations = 0" in docstring

    # ...and it is qualified by the branch that is the exception.
    lowered = docstring.lower()
    assert "exception" in lowered, (
        "the invariant is stated without an exception; gate 4's replay branch "
        "breaks it, so the docstring must name it"
    )
    assert "replay" in lowered
    assert "executed_lookup_for" in docstring, (
        "the docstring must say which lookup production injects, because that is "
        "what decides which branch the replay takes"
    )
