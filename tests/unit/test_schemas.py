"""Unit tests for the agent's Pydantic schemas.

Two schemas are in scope here, and they are one subject: the values a model is
allowed to produce, and the values a model is *not* allowed to produce.

* ``src/opspilot/agents/schemas.py`` -- the structured output the model is
  allowed to emit. Its module docstring claims "unknown fields, wrong types and
  bad enums are rejected before anything else happens"; these tests are what
  makes that a checked claim rather than a description.
* ``src/opspilot/domain/tools.py``'s argument schemas -- what gate 1 validates
  a proposal against.

## The placeholder this file used to hold

It was a skipped stub whose name claimed that *"issue_refund without
idempotency_key fails validation"*. That is the opposite of what the code
does, and it stayed skipped through M4, M5, M6 and M7 -- long after the
"implemented in M4" its docstring promised. The real behaviour is the reverse
of the name:

* A refund *without* an ``idempotency_key`` **validates**. The field is not
  merely optional; it is not a field at all. ``RefundArgs`` declares
  ``transaction_id``, ``amount`` and ``reason``, and the key is derived by the
  policy engine from the run and the transaction and added by the runtime at
  dispatch -- never supplied by the model.
* A refund that *names* an ``idempotency_key`` is **refused**, at gate 1. The
  key is what the duplicate check compares against, so a model-chosen one would
  let a proposal pick the key that decides whether it replays.

A test that reports nothing while appearing to promise something is worse than
no test, and a name that states the opposite of the behaviour is worse still:
readers trust it. So the test is written against the real property, and it is
no longer skipped.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from opspilot.agents.schemas import (
    AgentResponse,
    ProposedAction,
    TicketCategory,
    TicketClassification,
)
from opspilot.domain.tools import InvalidArgumentsError, validate_tool_call

#: A refund the model is allowed to propose: the three fields ``RefundArgs``
#: declares, and nothing else.
_A_REFUND: dict[str, object] = {
    "transaction_id": "TX-88219",
    "amount": 129.00,
    "reason": "Duplicate charge",
}


def test_a_refund_without_an_idempotency_key_validates() -> None:
    """A refund the model may propose carries no key, and that is valid.

    The opposite of the placeholder's name, and the property the runtime
    depends on: gate 1 accepts the proposal and the runtime adds the derived
    key afterwards, so a key-less refund is the *normal* shape and refusing it
    would refuse every refund.
    """
    spec, parsed = validate_tool_call("billing.issue_refund", dict(_A_REFUND))

    assert spec is not None, "billing.issue_refund is not registered"
    assert "idempotency_key" not in parsed, (
        f"the validated arguments carry an idempotency_key: {parsed}. The key is "
        "derived by the policy engine and added at dispatch; the model does not "
        "supply it and it does not reach the approver's snapshot"
    )
    assert parsed["transaction_id"] == "TX-88219"
    assert parsed["amount"] == 129.00


def test_a_refund_that_names_an_idempotency_key_is_refused() -> None:
    """A model-chosen key is refused at gate 1, not silently dropped.

    ``RefundArgs`` forbids unknown fields, so naming the key is a validation
    error rather than a field that is accepted and then ignored. The refusal
    has to happen here: the key is what the duplicate check compares against,
    so a proposal that picks its own would choose whether it replays.
    """
    with pytest.raises(InvalidArgumentsError) as excinfo:
        validate_tool_call(
            "billing.issue_refund",
            {**_A_REFUND, "idempotency_key": "attacker-chosen-key"},
        )

    # The refusal names the offending field, so a trace can show what was wrong.
    fields = {str(error["loc"][-1]) for error in excinfo.value.error.errors()}
    assert "idempotency_key" in fields, (
        f"the refusal named {sorted(fields)}, not the field the model wrongly supplied"
    )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            TicketClassification,
            {"category": "duplicate_charge", "confidence": 0.9, "rationale": "x"},
        ),
        (ProposedAction, {"tool_name": "billing.get_invoice", "arguments": {}}),
        (AgentResponse, {"body": "done"}),
    ],
)
def test_the_model_schemas_forbid_unknown_fields(
    model: type[BaseModel], payload: dict[str, object]
) -> None:
    """Unknown fields are refused: the model may not smuggle anything extra in.

    The claim is ``agents/schemas.py``'s module docstring -- "unknown fields,
    wrong types and bad enums are rejected before anything else happens" -- and
    it is the reason the parsed value can be trusted to hold only what the
    schema declares.
    """
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "not_a_declared_field": "surprise"})


def test_a_bad_category_is_refused() -> None:
    """A category outside the enum is rejected, not coerced to a string.

    A ``StrEnum`` that accepted an unknown member would make the classification
    column carry a value no downstream code has a branch for.
    """
    with pytest.raises(ValidationError):
        TicketClassification(
            category="not_a_category",  # type: ignore[arg-type]
            confidence=0.5,
            rationale="x",
        )

    assert TicketCategory.DUPLICATE_CHARGE.value == "duplicate_charge"
