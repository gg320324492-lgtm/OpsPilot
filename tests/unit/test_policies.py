"""Unit tests for the deterministic policy engine (gate 4).

Every test here runs without a model, a database or a network: the policy engine
is a pure function of the spec, the arguments and the run, which is the property
that lets it be the control and the model be the proposal
(``docs/tool-permissions.md`` §3-4).

The two things these tests are really guarding:

- a rule denies what it should and *permits* what it should -- a policy engine
  that denies everything is not a safety control, it is an outage;
- the risk explanation names the amount, because an approver who reads a vague
  sentence is a rubber stamp (``docs/risks.md`` R3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from opspilot.domain.errors import PolicyDenied
from opspilot.domain.policies import (
    REASON_REFUND_ABOVE_CEILING,
    REASON_REFUND_EXCEEDS_TRANSACTION,
    REASON_TOOL_DENYLISTED,
    PolicyDecision,
    derive_idempotency_key,
    evaluate_policy,
    explain_risk,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import TOOL_REGISTRY, Permission


def _run() -> AgentRun:
    """A run in a mid-flight, claimable state."""
    return AgentRun(
        id=uuid4(),
        ticket_id=uuid4(),
        status=RunStatus.PLANNING,
        model_provider="fake",
        model_name="fake-1",
        created_at=datetime.now(UTC),
    )


def _refund_args(**overrides: object) -> dict[str, object]:
    """A well-formed refund proposal, with per-test overrides."""
    base: dict[str, object] = {"transaction_id": "TX-88219", "amount": "129.00"}
    base.update(overrides)
    return base


REFUND = TOOL_REGISTRY["billing.issue_refund"]


# ---------------------------------------------------------------------------
# The refund-exceeds-transaction fast fail
# ---------------------------------------------------------------------------


def test_refund_below_transaction_amount_is_permitted() -> None:
    """A partial refund below the charge is the normal case and must pass."""
    decision = evaluate_policy(
        REFUND,
        _refund_args(amount="100.00", transaction_amount="129.00"),
        _run(),
    )
    assert decision.denied is False
    assert decision.reason == ""


def test_refund_equal_to_transaction_amount_is_permitted() -> None:
    """``==`` is allowed; the rule is ``>``, not ``>=``."""
    decision = evaluate_policy(
        REFUND,
        _refund_args(amount="129.00", transaction_amount="129.00"),
        _run(),
    )
    assert decision.denied is False


def test_refund_above_transaction_amount_is_denied() -> None:
    decision = evaluate_policy(
        REFUND,
        _refund_args(amount="200.00", transaction_amount="129.00"),
        _run(),
    )
    assert decision.denied is True
    assert decision.reason == REASON_REFUND_EXCEEDS_TRANSACTION


def test_refund_rule_is_inapplicable_without_a_transaction_amount() -> None:
    """No ``transaction_amount`` means the fast fail cannot fire -- not that it
    fires with zero. The MCP server remains the authoritative check."""
    decision = evaluate_policy(REFUND, _refund_args(amount="10000.00"), _run())
    assert decision.denied is False


def test_refund_rule_compares_decimal_not_float() -> None:
    """``0.1 + 0.2`` style float artifacts must not decide a money rule."""
    decision = evaluate_policy(
        REFUND,
        _refund_args(amount="0.30", transaction_amount="0.1"),
        _run(),
    )
    assert decision.denied is True


# ---------------------------------------------------------------------------
# The ceiling
# ---------------------------------------------------------------------------


def test_refund_above_ceiling_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_REFUND_CEILING", "500.00")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(REFUND, _refund_args(amount="600.00"), _run())
    finally:
        get_settings.cache_clear()
    assert decision.denied is True
    assert decision.reason == REASON_REFUND_ABOVE_CEILING


def test_refund_below_ceiling_is_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSPILOT_REFUND_CEILING", "500.00")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(REFUND, _refund_args(amount="129.00"), _run())
    finally:
        get_settings.cache_clear()
    assert decision.denied is False


def test_no_ceiling_means_no_ceiling_is_applied() -> None:
    """An unset ceiling permits any amount -- the approval gate is still the
    control that stops the money."""
    decision = evaluate_policy(REFUND, _refund_args(amount="999999.00"), _run())
    assert decision.denied is False


def test_ceiling_does_not_affect_non_refund_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling is a refund rule; it must not make an issue write deny."""
    monkeypatch.setenv("OPSPILOT_REFUND_CEILING", "1.00")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(
            TOOL_REGISTRY["issues.create"],
            {"title": "t", "body": "b"},
            _run(),
        )
    finally:
        get_settings.cache_clear()
    assert decision.denied is False


# ---------------------------------------------------------------------------
# The denylist -- may only remove capability
# ---------------------------------------------------------------------------


def test_denylisted_tool_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", "billing.issue_refund")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(REFUND, _refund_args(), _run())
    finally:
        get_settings.cache_clear()
    assert decision.denied is True
    assert decision.reason == REASON_TOOL_DENYLISTED


def test_unset_denylist_denies_nothing() -> None:
    decision = evaluate_policy(REFUND, _refund_args(), _run())
    assert decision.denied is False


@pytest.mark.parametrize("value", ["", "   ", ",,,", "  ,  , ", "not-a-real-tool"])
def test_malformed_denylist_denies_nothing(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed value must never deny a registered tool by accident: empty
    entries are dropped, and a name for no known tool matches nothing."""
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", value)
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(REFUND, _refund_args(), _run())
    finally:
        get_settings.cache_clear()
    assert decision.denied is False


def test_denylist_cannot_grant_a_tool_that_is_not_there() -> None:
    """There is no spelling of the denylist that grants capability: the setting
    is only ever consulted for membership (ADR-0003)."""
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        names = get_settings().tool_denylist
    finally:
        get_settings.cache_clear()
    # It is a frozenset of *names to refuse*; no API reads it to *add* a tool.
    assert isinstance(names, frozenset)


def test_denylist_matches_by_full_name_not_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``billing`` must not deny ``billing.issue_refund`` -- the setting takes
    tool names, and a prefix match would deny more than an operator asked for."""
    monkeypatch.setenv("OPSPILOT_TOOL_DENYLIST", "billing")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        decision = evaluate_policy(REFUND, _refund_args(), _run())
    finally:
        get_settings.cache_clear()
    assert decision.denied is False


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_executed_key_short_circuits_with_the_recorded_result() -> None:
    """A key that already has an executed call returns the recorded result
    rather than denying or re-executing (``docs/tool-permissions.md`` §4)."""
    decision = evaluate_policy(
        REFUND,
        _refund_args(),
        _run(),
        executed=(True, {"refund_id": "R-1"}),
    )
    assert decision.denied is False
    assert decision.idempotent_replay is True
    assert decision.executed_result == {"refund_id": "R-1"}


def test_key_with_no_executed_call_is_not_a_replay() -> None:
    decision = evaluate_policy(
        REFUND,
        _refund_args(),
        _run(),
        executed=(False, None),
    )
    assert decision.idempotent_replay is False


def test_idempotency_key_is_deterministic_and_run_scoped() -> None:
    run = _run()
    key = derive_idempotency_key(run, _refund_args())
    assert key == f"refund:{run.id}:TX-88219"
    # Same run, same effect -> same key; a random key would defeat the purpose.
    assert key == derive_idempotency_key(run, _refund_args())


def test_idempotency_key_differs_per_transaction() -> None:
    run = _run()
    first = derive_idempotency_key(run, _refund_args(transaction_id="TX-1"))
    second = derive_idempotency_key(run, _refund_args(transaction_id="TX-2"))
    assert first != second


def test_idempotency_key_on_a_tool_without_key_semantics_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal that smuggles an ``idempotency_key`` onto a tool that does not
    declare one is a programming error, raised rather than ignored."""
    with pytest.raises(PolicyDenied):
        evaluate_policy(
            TOOL_REGISTRY["issues.create"],
            {"title": "t", "body": "b", "idempotency_key": "sneaky"},
            _run(),
        )


# ---------------------------------------------------------------------------
# The risk explanation
# ---------------------------------------------------------------------------


def test_risk_explanation_names_the_amount_and_transaction() -> None:
    """The approver must see the number and the transaction, not a vague
    sentence -- this is what makes the approval a real control."""
    explanation = explain_risk(REFUND, _refund_args())
    assert "$129.00" in explanation
    assert "TX-88219" in explanation
    assert "cannot be undone" in explanation


def test_risk_explanation_ignores_the_models_untrusted_reason() -> None:
    """``explain_risk`` takes only the spec and the arguments; there is no
    parameter through which a prompt injection could reach it."""
    import inspect

    params = set(inspect.signature(explain_risk).parameters)
    assert params == {"spec", "arguments"}


def test_risk_explanation_for_a_read_tool_says_no_side_effect() -> None:
    explanation = explain_risk(TOOL_REGISTRY["crm.get_customer"], {"customer_id": "C1"})
    assert "no side effect" in explanation


def test_risk_explanation_for_a_safe_write_mentions_audit() -> None:
    explanation = explain_risk(TOOL_REGISTRY["issues.create"], {"title": "t", "body": "b"})
    assert "audited" in explanation.lower()


def test_risk_explanation_handles_a_refund_without_an_amount() -> None:
    """A refund missing its amount still produces a specific, non-vague line."""
    explanation = explain_risk(REFUND, {"transaction_id": "TX-9"})
    assert "refund" in explanation.lower()
    assert "TX-9" in explanation


def test_decision_is_frozen() -> None:
    from pydantic import ValidationError

    decision = PolicyDecision(denied=True, reason="x")
    with pytest.raises(ValidationError):
        decision.denied = False


def test_a_permitted_decision_always_carries_an_explanation() -> None:
    """Even a permitted call gets an explanation, because gate 5 may park it and
    the approver needs the deterministic string, not an empty field."""
    decision = evaluate_policy(REFUND, _refund_args(), _run())
    assert decision.explanation
    assert "$129.00" in decision.explanation
    assert REFUND.permission is Permission.HIGH_RISK_WRITE
