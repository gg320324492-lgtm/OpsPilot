"""The refusal codes each MCP tool can actually return, transcribed by hand.

## Why this is transcribed rather than generated

A table generated from the servers' own source would assert the code equals
itself -- and the defect this table exists to catch is a **shared** literal
that gives every tool of a server the union of that server's codes, so two of
the three advertise a failure they cannot have. Generating the expectation
would reproduce exactly that.

So it is written by hand, from the servers' source, and the guard compares the
two. The same convention as
``tests/security/test_permission_immutability.py``'s ``EXPECTED_REGISTRY``.

## The rule it encodes

**A tool that cannot fail declares no ``code`` at all**, and its schema says
so -- that is the case for ``billing.list_transactions`` (a query that matches
nothing is an empty list) and for ``issues.search``.

**A tool that can fail declares a top-level ``code``** whose enum is exactly
the set of codes it can produce -- no more, because a code in a tool's schema
is a promise to the agent about a failure it will never see, and no less,
because a code the tool can produce but does not declare is a failure the
gateway would record as a success.
"""

from __future__ import annotations

#: Tool name -> the codes it can return. An empty set means the tool has no
#: failure path, and therefore declares no ``code`` field.
EXPECTED_CODES: dict[str, frozenset[str]] = {
    "crm.get_customer": frozenset({"validation_error", "not_found"}),
    "crm.get_account": frozenset({"not_found"}),
    "crm.get_subscription": frozenset({"not_found"}),
    "billing.get_invoice": frozenset({"not_found"}),
    "billing.list_transactions": frozenset(),
    "billing.issue_refund": frozenset(
        {
            "validation_error",
            "not_found",
            "invalid_state",
            "amount_exceeds_transaction",
        }
    ),
    "issues.search": frozenset(),
    "issues.create": frozenset({"validation_error"}),
}
