"""The approver's risk explanation must describe the refund that executes.

The ``risk_explanation`` is the one string an approver is told to trust: it is
generated deterministically from the tool's static permission and its arguments,
while the model's own ``reason`` sits beside it labelled as untrusted
(``docs/tool-permissions.md`` §3.2). Whatever it says therefore has to be true
of the call that will run.

It was not. A proposal could name a currency, the currency was rendered into the
explanation, and the currency a refund moves is the one the transaction was
charged in -- a row only the billing server can read, read by the tool body
when it executes. A proposal naming ``EUR`` produced "This moves EUR 129.00"
over a refund the server was about to make in dollars, and the approver read a
currency the money would never move in.

What this file asserts
-----------------------

The explanation and the executed refund are computed from the same proposal and
then compared. The transaction here is charged in euros, so the explanation must
name no currency at all -- there is no currency a gate could name truthfully --
and the refund the server performs must be in the transaction's own currency.
The explanation cannot contradict the execution, because it says nothing the
execution could contradict.

The currency is set on *this test's* store rather than in the committed seed,
so the seed stays the demo's data and the committed golden path stays in
dollars.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from mcp.types import CallToolResult

from mcp_servers._store import Store
from mcp_servers.billing.server import create_server
from opspilot.domain.policies import derive_idempotency_key, explain_risk
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import validate_tool_call

_SEED_PATH = Path(__file__).resolve().parents[2] / "mcp_servers" / "billing" / "seed.json"

#: The transaction the duplicate-charge case refunds.
_TRANSACTION = "TX-88219"
#: What the refund moves.
_AMOUNT = 129.00
#: The currency this test charges the transaction in. The committed seed charges
#: every transaction in dollars, and a transaction charged in something else is
#: the only way to see what a currency in the explanation does.
_TRANSACTION_CURRENCY = "EUR"

#: The currency codes and the symbols the explanation must not contain: the
#: codes are the ones the explanation used to render, the symbols their signs.
_CURRENCY_CODES = ("USD", "EUR", "GBP", "JPY")
_CURRENCY_SYMBOLS = ("$", "€", "£", "¥")


def _run() -> AgentRun:
    """A run in a mid-flight state, for deriving the idempotency key."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=RunStatus.PLANNING,
        model_provider="fake",
        model_name="fake-1",
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def euro_store(tmp_path: Path) -> Store:
    """A billing store whose duplicate charge was made in euros."""
    store = Store(_SEED_PATH, filename="billing.json", data_dir=tmp_path)
    for row in store.data["transactions"]:
        if row.get("transaction_id") == _TRANSACTION:
            row["currency"] = _TRANSACTION_CURRENCY
    return store


async def _refund(store: Store, proposal: dict[str, object]) -> dict[str, object]:
    """Run the proposal the way the runtime would, and return what the server did.

    The dispatch mirrors ``agents/runtime.py``'s EXECUTE step: the gate-1 parsed
    arguments, plus the key gate 4 derived from the run and the transaction.
    """
    server = create_server(store)
    spec, parsed = validate_tool_call("billing.issue_refund", proposal)
    assert spec is not None, "the refund tool must be registered"
    dispatch = {**parsed, "idempotency_key": derive_idempotency_key(_run(), parsed)}
    result = await server.call_tool("billing.issue_refund", dispatch)
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_the_explanation_describes_the_refund_the_server_actually_makes(
    euro_store: Store,
) -> None:
    """The string the approver reads must match the refund that executes.

    The proposal names a currency -- the model may, and did -- and the
    transaction it refunds is charged in euros. The explanation must not name
    any currency, because no gate can know which one the server will use, and
    the refund the server performs must be in the transaction's own currency.
    """
    proposal: dict[str, object] = {
        "transaction_id": _TRANSACTION,
        "amount": _AMOUNT,
        "currency": "USD",
        "reason": "Duplicate charge confirmed against invoice INV-2026-384.",
    }

    spec, parsed = validate_tool_call("billing.issue_refund", proposal)
    assert spec is not None
    explanation = explain_risk(spec, parsed)

    # What the server does with the same proposal.
    executed = await _refund(euro_store, proposal)
    executed_currency = executed["currency"]

    # What the approver is shown: the number and the transaction, and no
    # currency -- the one thing a gate cannot know.
    assert "129.00" in explanation
    assert _TRANSACTION in explanation
    for code in _CURRENCY_CODES:
        assert code not in explanation, (
            f"the risk explanation names {code!r}: {explanation!r}. The refund "
            f"moves {executed_currency!r} -- the transaction's own currency -- so "
            f"a currency in the explanation is a currency the money will not move in."
        )
    for symbol in _CURRENCY_SYMBOLS:
        assert symbol not in explanation, (
            f"the risk explanation carries the currency symbol {symbol!r}: {explanation!r}"
        )

    assert executed["code"] is None
    assert executed["amount"] == _AMOUNT
    assert executed_currency == _TRANSACTION_CURRENCY
    # The number the approver was shown is the number that moved.
    assert f"{_AMOUNT:,.2f}" in explanation
    assert executed["amount"] == _AMOUNT


async def test_a_proposal_that_names_no_currency_is_told_no_currency_either(
    euro_store: Store,
) -> None:
    """The absence of a currency in the proposal is not an invitation to invent one.

    The explanation used to default to ``USD`` when the proposal said nothing,
    which is the same defect by another route: a default is not a reading of
    the transaction, and this transaction is not charged in dollars.
    """
    proposal: dict[str, object] = {
        "transaction_id": _TRANSACTION,
        "amount": _AMOUNT,
        "reason": "Duplicate charge confirmed against invoice INV-2026-384.",
    }

    spec, parsed = validate_tool_call("billing.issue_refund", proposal)
    assert spec is not None
    explanation = explain_risk(spec, parsed)

    for code in _CURRENCY_CODES:
        assert code not in explanation, f"the risk explanation names {code!r}: {explanation!r}"
    for symbol in _CURRENCY_SYMBOLS:
        assert symbol not in explanation, (
            f"the risk explanation carries the currency symbol {symbol!r}: {explanation!r}"
        )

    executed = await _refund(euro_store, proposal)
    assert executed["currency"] == _TRANSACTION_CURRENCY


async def test_the_approvers_snapshot_carries_no_currency_the_server_will_ignore(
    euro_store: Store,
) -> None:
    """The arguments the approver approves against are the ones the server acts on.

    The snapshot is documented as "the exact arguments the gate will execute"
    (``docs/tool-permissions.md`` §3.2). A currency in it would be a field the
    server ignores, sitting in front of the human who is deciding whether to
    release the money -- so the currency must not be in the snapshot either.
    """
    proposal: dict[str, object] = {
        "transaction_id": _TRANSACTION,
        "amount": _AMOUNT,
        "currency": "EUR",
        "reason": "Duplicate charge confirmed against invoice INV-2026-384.",
    }

    _, parsed = validate_tool_call("billing.issue_refund", proposal)
    assert "currency" not in parsed
    assert parsed["transaction_id"] == _TRANSACTION
    assert parsed["amount"] == _AMOUNT
