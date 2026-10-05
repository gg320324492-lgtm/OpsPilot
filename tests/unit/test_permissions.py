"""Unit tests for the tool permission model.

The guard this file implements is ``docs/tool-permissions.md`` §2.1: the registry
is compared against a hard-coded literal, so *any* change to a permission --
including an accidental one -- is a red test that a human must justify in the
diff. That is why the expected mapping is written out longhand below rather than
derived from the registry it checks.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from opspilot.domain.tools import (
    TOOL_ARGUMENT_SCHEMAS,
    TOOL_REGISTRY,
    InvalidArgumentsError,
    Permission,
    ToolSpec,
    get_tool_spec,
    validate_tool_call,
)

# Written out in full, deliberately not derived from TOOL_REGISTRY. If a
# permission is downgraded, the diff shows the old and new value side by side in
# a test named "permissions". See docs/adr/0003.
EXPECTED_PERMISSIONS: dict[str, Permission] = {
    "knowledge.search": Permission.READ,
    "crm.get_customer": Permission.READ,
    "crm.get_account": Permission.READ,
    "crm.get_subscription": Permission.READ,
    "billing.get_invoice": Permission.READ,
    "billing.list_transactions": Permission.READ,
    "billing.issue_refund": Permission.HIGH_RISK_WRITE,
    "issues.search": Permission.READ,
    "issues.create": Permission.SAFE_WRITE,
}


def test_registry_permissions_are_static() -> None:
    """The registry's permissions equal the hard-coded literal exactly."""
    assert {name: spec.permission for name, spec in TOOL_REGISTRY.items()} == EXPECTED_PERMISSIONS


def test_registry_names_match_their_keys() -> None:
    """Every spec's ``name`` equals the dict key it is filed under.

    A mismatch would make ``get_tool_spec`` return a spec whose name disagrees
    with the call that fetched it, and the permission column would be snapshotted
    under the wrong name.
    """
    for key, spec in TOOL_REGISTRY.items():
        assert spec.name == key


def test_exactly_one_high_risk_tool() -> None:
    """Phase 1 has exactly one high-risk tool, and it is the refund."""
    high_risk = {n for n, s in TOOL_REGISTRY.items() if s.permission is Permission.HIGH_RISK_WRITE}
    assert high_risk == {"billing.issue_refund"}


def test_high_risk_tool_requires_approval_and_a_key() -> None:
    refund = TOOL_REGISTRY["billing.issue_refund"]
    assert refund.requires_approval is True
    assert refund.requires_idempotency_key is True


def test_registry_is_frozen_specs() -> None:
    """A ``ToolSpec`` is a frozen model, so a permission cannot be reassigned on
    a live instance at runtime."""
    with pytest.raises(ValidationError):
        TOOL_REGISTRY["billing.issue_refund"].permission = Permission.READ


def test_every_registered_tool_has_an_argument_schema() -> None:
    """Gate 1 needs a schema for every registered tool; the two mappings must not
    drift apart."""
    assert set(TOOL_ARGUMENT_SCHEMAS) == set(TOOL_REGISTRY)


def test_get_tool_spec_returns_the_registered_spec() -> None:
    assert get_tool_spec("billing.issue_refund") is TOOL_REGISTRY["billing.issue_refund"]


def test_get_tool_spec_returns_none_for_an_unknown_tool() -> None:
    assert get_tool_spec("does.not.exist") is None


# ---------------------------------------------------------------------------
# Gate 1 -- schema validation
# ---------------------------------------------------------------------------


def test_unknown_tool_has_no_spec_to_validate() -> None:
    """Gate 1 returns ``None`` for an unregistered tool so gate 2 reports it."""
    spec, parsed = validate_tool_call("does.not.exist", {})
    assert spec is None
    assert parsed == {}


def test_valid_arguments_are_parsed_and_defaulted() -> None:
    spec, parsed = validate_tool_call(
        "billing.issue_refund", {"transaction_id": "TX-1", "amount": "1.00"}
    )
    assert spec is TOOL_REGISTRY["billing.issue_refund"]
    assert parsed["transaction_id"] == "TX-1"
    assert parsed["currency"] == "USD"  # a default is filled in, not dropped


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("crm.get_customer", {"wrong_field": "x"}),
        ("crm.get_customer", {}),
        ("billing.issue_refund", {"transaction_id": "TX-1"}),  # missing amount
        ("billing.issue_refund", {"transaction_id": "", "amount": "1.00"}),  # empty id
        ("knowledge.search", {"query": ""}),  # empty query
        ("issues.create", {"title": "t"}),  # missing body
        ("billing.list_transactions", {"account_id": "A", "limit": 0}),  # below floor
        ("billing.list_transactions", {"account_id": "A", "limit": 1000}),  # above ceiling
    ],
)
def test_invalid_arguments_are_rejected(name: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgumentsError):
        validate_tool_call(name, arguments)


def test_unknown_fields_are_forbidden() -> None:
    """``extra='forbid'`` on every argument model means an injected field cannot
    ride along into the call."""
    with pytest.raises(InvalidArgumentsError):
        validate_tool_call(
            "billing.issue_refund",
            {"transaction_id": "TX-1", "amount": "1.00", "approved": "true"},
        )


def test_validate_never_returns_a_spec_without_parsing() -> None:
    """The returned spec and the parsed arguments come out together, so a caller
    cannot get the spec and skip validation."""
    result = validate_tool_call("issues.create", {"title": "t", "body": "b"})
    spec, parsed = result
    assert isinstance(spec, ToolSpec)
    assert parsed == {"title": "t", "body": "b"}
