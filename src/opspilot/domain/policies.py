"""Deterministic policy engine (gate 4) and risk explanation generation.

Responsibility: the business rules that decide whether a *structurally valid*,
*registered* tool call may proceed -- amount ceilings, idempotency pre-check,
and state preconditions (e.g. "cannot refund an already-refunded transaction").
This gate is code, not a prompt, and its verdict is reproducible.

Layer: ``domain``. Imports only the standard library, Pydantic, and sibling
domain modules. It must not touch the database or the gateway: the idempotency
*pre-check* is a fast short-circuit here, but the authoritative guarantee lives
at the MCP server, which is the only place that can prevent a duplicate row
(``docs/tool-permissions.md`` §4).

The principles: deterministic code decides. ``explain_risk`` produces the
``risk_explanation`` string shown to an approver -- generated from the tool's
static permission and its arguments, never from the model's ``reason``, which is
untrusted, and never from a fact about the transaction that only the model
claims (a refund's currency is the transaction's own, not the model's to say;
see ``format_amount``).

Honest note on scope. Phase 1's rule set is small on purpose. The two rules that
carry real weight here are the refund ceiling and the refund-exceeds-transaction
fast fail; everything else is ledger bookkeeping. The denylist check is a
*subtraction* mechanically (see ``_denied_by_denylist``), and the idempotency
short-circuit reports a remembered result rather than a new decision.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from opspilot.domain.errors import PolicyDenied
from opspilot.domain.runs import AgentRun
from opspilot.domain.tools import Permission, ToolSpec

# The two tools whose arguments carry an amount and a transaction, and so are the
# only ones ``explain_risk`` can say something specific about. A rule that named
# fields it cannot find would produce a vague explanation, which is exactly the
# rubber-stamp failure the approver gate is meant to avoid (``docs/risks.md`` R3).
_REFUND_TOOL: Final[str] = "billing.issue_refund"
_TRANSACTION_FIELD: Final[str] = "transaction_id"
_AMOUNT_FIELD: Final[str] = "amount"

# The reason strings are machine-stable tokens: the gate that wrote a rejection
# is recoverable from ``ToolCall.rejection_reason`` without parsing prose, and the
# integration tests assert on them.
REASON_REFUND_EXCEEDS_TRANSACTION: Final[str] = "refund_exceeds_transaction_amount"
REASON_REFUND_ABOVE_CEILING: Final[str] = "refund_above_ceiling"
REASON_TOOL_DENYLISTED: Final[str] = "tool_denylisted"
REASON_IDEMPOTENT_REPLAY: Final[str] = "idempotency_key_already_executed"


class PolicyDecision(BaseModel):
    """The verdict of the policy engine for one tool call.

    ``denied`` short-circuits execution and the call is recorded as ``rejected``
    with ``reason``. ``idempotent_replay`` is a third outcome: not a denial, but a
    *short-circuit* -- the side effect already happened, so ``executed_result``
    carries the remembered outcome and gate 4 returns it rather than re-executing
    (``docs/tool-permissions.md`` §4). ``explanation`` is the deterministic
    ``risk_explanation`` a human approver reads at gate 5.
    """

    model_config = ConfigDict(frozen=True)

    denied: bool = False
    reason: str = ""
    explanation: str = ""
    idempotent_replay: bool = False
    executed_result: dict[str, object] | None = None


def _as_decimal(value: object) -> Decimal | None:
    """Parse an amount-ish argument into a ``Decimal``, or ``None`` if it is not one.

    Decimal rather than float: money comparisons must not depend on binary
    roundoff, and ``Decimal("129.00") == Decimal("129")`` is true while the float
    forms are not obviously so. ``None`` means "not a parseable amount", which
    every caller treats as "this rule does not apply" rather than as zero -- a
    missing amount must never read as an amount of zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    if isinstance(value, float):
        return Decimal(str(value))
    return None


def _str_arg(arguments: dict[str, Any], field: str) -> str:
    """Read a string argument, empty string when absent or not a string."""
    value = arguments.get(field)
    return value if isinstance(value, str) else ""


def format_amount(amount: Decimal) -> str:
    """Render an amount for the approver, e.g. ``"129.00"``.

    **No currency, and that is the point.** The currency a refund moves is the
    currency the transaction was charged in, and that value lives in the billing
    server's own store -- the tool body reads it at execution time, and no gate
    can read it before then (``docs/tool-permissions.md`` §3.2). A currency
    rendered here would be one the refund would not use: a proposal naming
    ``EUR`` over a transaction charged in dollars produced "EUR 129.00" in the
    one string the approver is told to trust. The number is what the approver
    can be shown truthfully, and the currency is on the transaction they are
    looking at.

    The thousands separator is fixed rather than a locale format on purpose:
    this string is part of a security control (the human must see the number
    the refund will move), and a locale the deployment happens to have installed
    must not be able to change it.
    """
    return f"{amount:,.2f}"


def _refund_exceeds_transaction(arguments: dict[str, Any]) -> tuple[bool, Decimal | None]:
    """Whether the refund amount is (strictly) above the transaction amount.

    Returns ``(exceeds, refund_amount)``. Both amounts must be present and
    parseable for the rule to fire; a transaction that does not carry an amount
    (or a call whose arguments failed to parse) leaves the rule inapplicable and
    the MCP server remains the authoritative check. The comparison is ``>``, not
    ``>=``: a partial refund below the charge is the normal case and must pass.

    **The charged amount is not a gate-1 argument any more.** ``RefundArgs``
    does not declare ``transaction_amount`` (see ``domain.tools``): the number
    would be the model's claim about a row only the billing server can read, and
    a model that could supply it could also *suppress* this rule by claiming a
    larger charge. So on the live path the rule is inapplicable and the MCP
    server's own comparison against the transaction row is the check. The rule
    stays because it is the policy the specification asks for, and because a
    caller that can read the charged amount from an authoritative source has
    exactly this input to give it.
    """
    refund = _as_decimal(arguments.get(_AMOUNT_FIELD))
    charged = _as_decimal(arguments.get("transaction_amount"))
    if refund is None or charged is None:
        return False, refund
    return refund > charged, refund


def _ceiling() -> Decimal | None:
    """The configured refund ceiling, or ``None`` when there is no ceiling.

    Read through the settings module so the one knob lives where every other
    deployment knob does. The import is function-local because ``domain`` may not
    import the settings *instance* at module scope without coupling the pure
    policy engine to process configuration; reading it per call keeps
    ``evaluate_policy`` a pure function of its arguments and the environment.
    """
    from opspilot.settings import get_settings

    ceiling = get_settings().refund_ceiling
    return _as_decimal(ceiling)


def _denied_by_denylist(spec: ToolSpec) -> bool:
    """Whether the tool is named in ``OPSPILOT_TOOL_DENYLIST``.

    The asymmetry is the point (ADR-0003): this only ever *removes* a tool. An
    unset, empty or malformed value denies nothing, and there is no spelling of
    the variable that grants a permission -- because membership in the deny set
    is the only thing consulted. The parse itself lives in
    ``Settings.tool_denylist`` (which drops empties and whitespace) so there is
    one implementation of "what counts as a denied name".
    """
    from opspilot.settings import get_settings

    return spec.name in get_settings().tool_denylist


def explain_risk(spec: ToolSpec, arguments: dict[str, Any]) -> str:
    """Produce the deterministic risk explanation shown to a human approver.

    Built from the tool's *static* permission and its arguments, so it cannot be
    influenced by a prompt injection in the model's ``reason`` -- the two strings
    are shown side by side, labelled differently
    (``docs/data-model.md`` §2, ``docs/tool-permissions.md`` §3.2).

    Example: ``"This moves 129.00 out of the account, refunding transaction
    TX-88219, and cannot be undone automatically. The amount is in the currency
    the transaction was charged in."``

    The base sentence is derived from the permission, never from the tool's
    description (which is authored text and could drift); the refund tool adds the
    specific transaction and amount because an approver reading "129.00 for
    TX-88219" can check it against the invoice in front of them, while an
    approver reading "a refund" cannot.

    **A currency the proposal names is not read here, and cannot be.** The
    currency a refund moves is the currency of the transaction it refunds -- a
    value in the billing server's own store, read by the tool body when it
    executes. No gate can know it: there is no tool that returns a transaction by
    id, and ``billing.list_transactions`` needs an invoice id the runtime does
    not have. So a currency read out of the proposal would be the model's guess
    at a fact it cannot know, printed in the one string the approver is told to
    trust -- which is how a proposal naming ``EUR`` came to describe a refund
    the server was about to make in dollars. The explanation names the amount
    and the transaction, and says where the currency comes from.
    """
    permission = spec.permission
    if permission is Permission.HIGH_RISK_WRITE:
        base = "This is a high-risk write and cannot be undone automatically."
    elif permission is Permission.SAFE_WRITE:
        base = "This writes to an external system. It is reversible but always audited."
    else:
        base = "This reads data and has no side effect."

    if spec.name != _REFUND_TOOL:
        return base

    amount = _as_decimal(arguments.get(_AMOUNT_FIELD))
    transaction = _str_arg(arguments, _TRANSACTION_FIELD)

    if amount is None and not transaction:
        return base + " A refund moves money and cannot be undone automatically."

    if amount is None:
        return f"This issues a refund for transaction {transaction} and cannot be undone."
    rendered = format_amount(amount)
    if not transaction:
        return f"This moves {rendered} and cannot be undone automatically."
    # The currency sentence is stated only where there is a transaction to have
    # one: without a transaction there is no currency to point at, and naming the
    # amount alone is the honest thing left to say.
    return (
        f"This moves {rendered} out of the account, refunding transaction "
        f"{transaction}, and cannot be undone automatically. The amount is in the "
        f"currency the transaction was charged in."
    )


def evaluate_policy(
    spec: ToolSpec,
    arguments: dict[str, Any],
    run: AgentRun,
    *,
    executed: tuple[bool, dict[str, object] | None] | None = None,
) -> PolicyDecision:
    """Gate 4: apply the deterministic business rules to a tool call.

    Args:
        spec: The registered spec; ``spec.permission`` is static code.
        arguments: The call's arguments (already schema-valid for its tool).
        run: The run the call belongs to. Currently unused: no Phase 1 rule
            depends on run-level state, and inventing one (a per-run refund cap,
            say) would be a rule the spec does not ask for. It is in the
            signature because the gate is specified with it and a future rule
            will need it.
        executed: The outcome of a prior *executed* call with this call's
            idempotency key, as ``(found, result)``, supplied by the runtime's
            database read. ``domain`` may not read the database itself
            (``docs/tool-permissions.md`` §4), so the runtime performs the lookup
            and this pure function decides what the lookup *means*.

    Returns:
        A ``PolicyDecision``. ``denied=True`` records the call as ``rejected``;
        ``idempotent_replay=True`` returns the remembered result instead of
        executing again.

    Raises:
        PolicyDenied: If ``arguments`` contains an ``idempotency_key`` for a tool
            that does not declare one. This is not reachable from a model
            proposal -- extra arguments are a programming error at the call
            site -- and it is raised loudly rather than silently ignored so that
            a misplaced key cannot masquerade as idempotency.
    """
    explanation = explain_risk(spec, arguments)

    # Rule 0 -- the denylist. Checked first because it is a *removal* of
    # capability: if an operator has taken a tool away, no later rule should be
    # reached that could look like a grant.
    if _denied_by_denylist(spec):
        return PolicyDecision(
            denied=True,
            reason=REASON_TOOL_DENYLISTED,
            explanation=explanation,
        )

    # Idempotency short-circuit. The key is derived deterministically for the
    # tool, not read from the proposal: a model that could choose its own key
    # could sidestep the duplicate check (and the MCP server's map) by picking a
    # fresh one each time. Only HIGH_RISK_WRITE tools carry keys in Phase 1.
    if spec.requires_idempotency_key:
        key = derive_idempotency_key(run, arguments)
        if executed is not None and executed[0]:
            return PolicyDecision(
                idempotent_replay=True,
                reason=REASON_IDEMPOTENT_REPLAY,
                explanation=explanation,
                executed_result=executed[1],
            )
        _ = key  # the key is returned to the caller via derive_idempotency_key
    elif arguments.get("idempotency_key") is not None:
        raise PolicyDenied("idempotency_key_present_on_tool_without_key_semantics")

    if spec.name == _REFUND_TOOL:
        exceeds, refund_amount = _refund_exceeds_transaction(arguments)
        if exceeds:
            return PolicyDecision(
                denied=True,
                reason=REASON_REFUND_EXCEEDS_TRANSACTION,
                explanation=explanation,
            )
        ceiling = _ceiling()
        if ceiling is not None and refund_amount is not None and refund_amount > ceiling:
            return PolicyDecision(
                denied=True,
                reason=REASON_REFUND_ABOVE_CEILING,
                explanation=explanation,
            )

    return PolicyDecision(denied=False, explanation=explanation)


def derive_idempotency_key(run: AgentRun, arguments: dict[str, Any]) -> str:
    """Derive the deterministic idempotency key for a write tool.

    Shape: ``f"refund:{run.id}:{transaction_id}"``. Not a random UUID (which
    would make every call unique and defeat the purpose) and not timestamp-based
    (a retry minutes later would become a new refund). The key never comes from
    the model's proposal: it is a function of the run and the intended effect, so
    a replan of the same refund lands on the same key
    (``docs/tool-permissions.md`` §4).
    """
    transaction = _str_arg(arguments, _TRANSACTION_FIELD)
    return f"refund:{run.id}:{transaction}"


__all__ = [
    "REASON_IDEMPOTENT_REPLAY",
    "REASON_REFUND_ABOVE_CEILING",
    "REASON_REFUND_EXCEEDS_TRANSACTION",
    "REASON_TOOL_DENYLISTED",
    "PolicyDecision",
    "derive_idempotency_key",
    "evaluate_policy",
    "explain_risk",
    "format_amount",
]
