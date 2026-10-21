"""Gate 4's rule set: the refusals it performs, and the one it cannot.

``agents/runtime.py``'s gate-4 comment used to enumerate four deterministic
rules -- the denylist, the amount ceiling, a "refund-exceeds-transaction fast
fail", and the idempotency short-circuit. Three of those are real. The fourth
is a refusal the runtime **cannot perform**: the rule needs the charged amount,
``RefundArgs`` deliberately does not declare ``transaction_amount``
(``domain/tools.py``), and a model that could supply the number could also
suppress the rule by claiming a larger charge. On the runtime path the rule
therefore never fires -- the check that does fire is the billing server's own
``amount_exceeds_transaction`` against the transaction row
(``docs/tool-permissions.md`` §4).

The evidence in this file is the behaviour, not the wording. A refund of
999,999.00 against a transaction charged 129.00 is **not** refused at gate 4:
it parks for a human, because the approval gate is the control that can stop
it, and the server refuses it afterwards
(``tests/integration/test_mcp_billing.py::test_refund_amount_exceeding_transaction_is_refused``).
A comment claiming a fast fail here described a rejection nobody could
produce, and a reader who trusted it would have believed a layer of defence
existed that does not.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import models
from opspilot.agents.runtime import _gate_and_execute
from opspilot.agents.schemas import ProposedAction
from opspilot.domain.errors import RunParked
from opspilot.domain.policies import REASON_REFUND_EXCEEDS_TRANSACTION, evaluate_policy
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import TOOL_REGISTRY, RefundArgs, ToolCallStatus
from opspilot.tracing.recorder import TraceRecorder

from .conftest import SpyGateway, make_context, make_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = REPO_ROOT / "src" / "opspilot" / "agents" / "runtime.py"

# The transaction the billing seed charges 129.00 for
# (``mcp_servers/billing/seed.json``), and a proposal far above that charge.
# The gap is deliberate: a rule that compared the two would have to fire here,
# and the test must not be able to pass on a rounding difference.
_TX = "TX-88219"
_CHARGED = 129.00
_OVER = 999_999.00


@pytest.fixture
def no_refund_ceiling(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Switch the refund ceiling off, so only the rule under test can refuse.

    The ceiling is a *real* gate-4 rule and an operator's environment may set
    it. This test is about the other rule, so the ceiling is pinned off and
    the settings cache is cleared on the way out -- an ambient
    ``OPSPILOT_REFUND_CEILING`` would otherwise decide what the test means.
    """
    from opspilot.settings import get_settings

    monkeypatch.setenv("OPSPILOT_REFUND_CEILING", "")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _run() -> AgentRun:
    """A run in a mid-flight state, for the pure policy engine."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=RunStatus.PLANNING,
        model_provider="fake",
        model_name="fake-1",
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# The rule cannot fire on the runtime path.
# ---------------------------------------------------------------------------


async def test_the_runtime_refuses_no_refund_for_exceeding_the_transaction(
    factory: sessionmaker[Session],
    spy: SpyGateway,
    no_refund_ceiling: None,
) -> None:
    """A refund of 999,999.00 on a 129.00 charge is not refused at gate 4.

    The gate-4 comment used to claim a "refund-exceeds-transaction fast fail"
    here. It cannot perform one: the charged amount is a row only the billing
    server can read, and ``RefundArgs`` refuses to carry a model's claim about
    it.

    So the call parks. It does not execute (the approval gate stops it) and it
    is not rejected (no rule applies). The refusal that does exist happens
    later, at the server, which knows the charge.
    """
    stores = await make_run(factory)
    ctx = make_context(stores)

    with pytest.raises(RunParked):
        await _gate_and_execute(
            ctx,
            ProposedAction(
                tool_name="billing.issue_refund",
                arguments={"transaction_id": _TX, "amount": _OVER},
                reason="the customer was charged twice",
            ),
            gateway=spy,
            run_store=stores.run_store,
            tool_call_store=stores.tool_call_store,
            approval_store=stores.approval_store,
            recorder=TraceRecorder(run_id=stores.run.id, session_factory=stores.factory),
        )

    assert spy.dispatch_count == 0, "a parked call must not have been dispatched"

    with factory() as session:
        rows = list(session.execute(select(models.ToolCall)).scalars())
    assert len(rows) == 1
    row = rows[0]
    assert row.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert row.rejection_reason is None, (
        f"the call was refused by {row.rejection_reason!r}; gate 4 has no rule "
        "that can compare a proposal against a charge it cannot read"
    )


@pytest.mark.parametrize("amount", [_CHARGED, 200.00, _OVER, 0.01])
def test_a_validated_refund_proposal_can_never_yield_the_exceeds_reason(
    amount: float,
    no_refund_ceiling: None,
) -> None:
    """The rule is unreachable through the arguments a proposal may carry.

    The arguments are parsed by ``RefundArgs`` -- the model gate 1 validates
    against -- and the parsed result is what gate 4 evaluates. The rule needs
    ``transaction_amount``, which that model does not declare, so no amount a
    proposal can carry makes the rule fire.
    """
    arguments = RefundArgs(transaction_id=_TX, amount=amount).model_dump()
    decision = evaluate_policy(TOOL_REGISTRY["billing.issue_refund"], arguments, _run())
    assert decision.reason != REASON_REFUND_EXCEEDS_TRANSACTION
    assert decision.denied is False


def test_the_charged_amount_cannot_even_be_proposed() -> None:
    """``RefundArgs`` refuses a model-supplied ``transaction_amount``.

    This is the root cause of the unreachable rule: the field does not exist
    on the schema, so a proposal carrying one is refused at gate 1 and the
    policy engine never sees a number it could compare against. A model that
    could supply it could also suppress the rule by claiming a larger charge
    -- which is why the check belongs at the server, where the real charge is
    read (``docs/tool-permissions.md`` §4).
    """
    with pytest.raises(ValidationError):
        RefundArgs.model_validate(
            {"transaction_id": _TX, "amount": _CHARGED, "transaction_amount": _CHARGED}
        )


# ---------------------------------------------------------------------------
# The comment says what the gate does.
# ---------------------------------------------------------------------------


def _gate_4_comment() -> str:
    """The gate-4 comment block of ``agents/runtime.py``, verbatim.

    Read from the source rather than imported, because the claim under test is
    a claim in prose: no code change can make this test fail, only the comment
    can.
    """
    lines = RUNTIME_SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.strip() == "# GATE 4 -- POLICY ENGINE"
    )
    block: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        block.append(stripped)
    return " ".join(block)


def test_the_gate_4_comment_does_not_claim_a_refusal_it_cannot_perform() -> None:
    """The comment names the three rules that can refuse, and where the fourth is.

    The old comment listed a "refund-exceeds-transaction fast fail" among gate
    4's rules. The sentence is gone, and the truth is named in its place: the
    rule exists in ``domain/policies.py`` and the check that fires is the
    billing server's ``amount_exceeds_transaction``.
    """
    comment = _gate_4_comment()
    assert comment, "the gate-4 comment block was not found; this guard is vacuous"

    assert "refund-exceeds-transaction" not in comment, (
        "the gate-4 comment still lists the refund-exceeds-transaction rule as "
        "one of its refusals; the runtime cannot perform it"
    )
    # The three rules that can refuse here are named...
    assert "denylist" in comment
    assert "ceiling" in comment
    assert "idempotency" in comment
    # ...and the rule that cannot is named where it actually lives.
    assert "_refund_exceeds_transaction" in comment
    assert "amount_exceeds_transaction" in comment
