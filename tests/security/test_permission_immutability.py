"""Security control: the tool registry is a compile-time constant.

This is the authoritative ``TOOL_REGISTRY`` immutability test
(``docs/tool-permissions.md`` §2.1). It asserts the registry equals a
**hard-coded literal** transcribed from the specification, not merely that its
keys are unchanged -- so *any* change to a permission, a server binding, or the
``requires_*`` flags fails this test and a human must justify it in the diff.

Why a literal rather than a property test over the code's own idea of the
registry: a test written from the implementation agrees with the implementation
by construction and cannot catch a divergence between the code and the contract.
The literal is the contract. A permission is the load-bearing security fact in
the system, and the threat model is the agent's own influence surface (prompt
text, retrieved documents, model output, tool arguments) reaching a Python
``Final`` dict -- which it cannot, and this test is the tripwire that proves the
dict did not sprout a mutable path.

The registry here is compared by value. ``ToolSpec`` is a frozen Pydantic model,
so the literal below is written as the tuple of the fields that matter, which
keeps the assertion readable and makes a diff show the one field that changed.
"""

from __future__ import annotations

from opspilot.domain.tools import TOOL_ARGUMENT_SCHEMAS, TOOL_REGISTRY, Permission

# Transcribed by hand from docs/tool-permissions.md §2. Do not generate this from
# TOOL_REGISTRY: generating it would make the test assert the code equals itself.
# The tuple order is (name, permission, server, requires_idempotency_key,
# requires_approval).
EXPECTED_REGISTRY: dict[str, tuple[str, str, str, bool, bool]] = {
    "knowledge.search": ("knowledge.search", "read", "internal", False, False),
    "crm.get_customer": ("crm.get_customer", "read", "crm", False, False),
    "crm.get_account": ("crm.get_account", "read", "crm", False, False),
    "crm.get_subscription": ("crm.get_subscription", "read", "crm", False, False),
    "billing.get_invoice": ("billing.get_invoice", "read", "billing", False, False),
    "billing.list_transactions": ("billing.list_transactions", "read", "billing", False, False),
    "billing.issue_refund": ("billing.issue_refund", "high_risk_write", "billing", True, True),
    "issues.search": ("issues.search", "read", "issues", False, False),
    "issues.create": ("issues.create", "safe_write", "issues", False, False),
}

# There is exactly one high-risk tool in Phase 1, and it is the refund. If a
# second appears, this set changes and the diff says so.
EXPECTED_HIGH_RISK: frozenset[str] = frozenset({"billing.issue_refund"})


def test_tool_registry_equals_expected_literal() -> None:
    """The registry equals the hard-coded literal, field for field."""
    actual = {
        name: (
            spec.name,
            str(spec.permission),
            spec.server,
            spec.requires_idempotency_key,
            spec.requires_approval,
        )
        for name, spec in TOOL_REGISTRY.items()
    }
    assert actual == EXPECTED_REGISTRY


def test_registry_keys_match_expected_exactly() -> None:
    """No tool was added or removed without the literal being updated too.

    Redundant with the equality above on purpose: an added tool would fail both,
    and a reader scanning the failure sees the missing/extra name directly rather
    than a dict diff.
    """
    assert set(TOOL_REGISTRY) == set(EXPECTED_REGISTRY)


def test_only_the_refund_is_high_risk() -> None:
    """``HIGH_RISK_WRITE`` is exactly the one tool the spec names.

    A tool that quietly became high-risk would park runs unexpectedly; a tool
    that quietly stopped being high-risk would execute money movement without a
    human, which is the failure this whole model exists to prevent. Both are
    caught here by asserting set equality, not mere membership.
    """
    high_risk = {
        name
        for name, spec in TOOL_REGISTRY.items()
        if spec.permission is Permission.HIGH_RISK_WRITE
    }
    assert high_risk == EXPECTED_HIGH_RISK


def test_every_registered_tool_has_an_argument_schema() -> None:
    """Gate 1 has a schema for every registered tool, and no orphan schema.

    A registered tool without a schema cannot have its arguments validated, and
    gate 1 would have nothing to parse the model's proposal into. A schema
    without a registered tool is dead code that suggests a tool that cannot be
    called. The two maps are asserted equal so neither can drift.
    """
    assert set(TOOL_ARGUMENT_SCHEMAS) == set(TOOL_REGISTRY)


def test_registry_is_a_plain_final_dict_not_backed_by_a_store() -> None:
    """The registry is process state, not database or config.

    ``Final`` is a typing construct, so this asserts the runtime reality that
    matters: the object is a plain ``dict``, and mutating it changes nothing for
    any *other* permission source because there is none -- a mutation here would
    have to be made by code in this repository, in a reviewable diff, which is
    exactly the property §2.1 claims.
    """
    assert isinstance(TOOL_REGISTRY, dict)
