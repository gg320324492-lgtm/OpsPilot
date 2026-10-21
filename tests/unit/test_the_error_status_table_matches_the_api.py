"""The code-to-status table must be the API's, in both directions.

## Why this file exists

``src/opspilot/api/errors.py`` carries a table mapping every machine code to
the HTTP status it is served as, and the comment above it says what the table
is for:

> The codes are the contract; the numbers are how the contract is served.
> Keeping the pair in one table means **a new code cannot be added with a
> made-up status**.

None of that was true. The table listed eleven codes; the API could send
nineteen. It listed ``not_ready``, which nothing sends -- the 503 the
readiness route returns carries a readiness body, not an error envelope. And
**nothing read the table**: ``_status_code_for`` had no callers, so every
router wrote its status and its code as two separate arguments and a code
paired with the wrong status would have been served, unnoticed, to a client
that branches on the code.

A table that nothing reads is a table that cannot keep a promise. This file
is what makes it one.

## What this file asserts

1. **Every code the API can send is in the table**, and every code in the
   table is one the API can send -- the codes come from the API's own source,
   read out of the routers' ``ApiError(status, code)`` calls and the exception
   handlers' ``_json_error(status_code=..., code=...)`` calls.
2. **Every ``(status, code)`` the API serves agrees with the table.** This is
   the promise the comment makes, checked against the code rather than
   against another copy of the table.
3. **A mismatched pair is refused at construction.** ``ApiError`` consults
   the table, so a router that pairs ``ticket_not_found`` with a 500 fails
   loudly instead of being served.

## The one code that is not in the table

``error`` is the code ``_code_for_status`` hands out for a status it has no
specific code for, and it travels with whatever status the framework raised --
400, 404 or 500. A code that can be several statuses has no single status to
put it against, and putting one there would be the table lying in the other
direction. It is excluded here, and the exclusion is asserted.
"""

from __future__ import annotations

import pytest

from opspilot.api.errors import _CODE_STATUS, ApiError
from tests._api_codes import backend_codes_by_status, backend_handler_codes_by_status

#: The code that travels with whatever status the framework raised, and so
#: has no single status to be listed against.
_STATUS_FOLLOWS_CODE = "error"


def _served_codes() -> dict[int, set[str]]:
    """Every ``(status, code)`` the API serves, from the API's own source."""
    served: dict[int, set[str]] = {}
    for source in (backend_codes_by_status(), backend_handler_codes_by_status()):
        for status, codes in source.items():
            served.setdefault(status, set()).update(codes)
    return served


def test_the_table_covers_exactly_the_codes_the_api_sends() -> None:
    """The table is the API's code list, not a shorter one and not a longer one."""
    from tests._api_codes import backend_error_codes

    producible = backend_error_codes() - {_STATUS_FOLLOWS_CODE}

    missing = sorted(producible - set(_CODE_STATUS))
    assert not missing, (
        f"the API can send {missing} and the code-to-status table does not "
        "list them. A code that is not in the table has no status, so a "
        "router that raised it would be free to pair it with any status at "
        "all."
    )

    extra = sorted(set(_CODE_STATUS) - producible)
    assert not extra, (
        f"the table lists {extra}, which no code path in the API can send. A "
        "code nobody sends is a promise about the contract that nothing "
        "keeps."
    )


def test_every_status_the_api_serves_agrees_with_the_table() -> None:
    """The table's number is the status the code is actually served as."""
    wrong: list[str] = []
    for status, codes in sorted(_served_codes().items()):
        for code in sorted(codes):
            expected = _CODE_STATUS.get(code)
            if expected is None:
                continue
            if expected != status:
                wrong.append(f"{code}: served as {status}, the table says {expected}")
    assert not wrong, (
        "the API serves a code with a status the table does not name:\n  "
        + "\n  ".join(wrong)
        + "\n\nThe table's whole purpose is to be the one place that says "
        "which status a code is served as."
    )


def test_the_status_following_code_is_not_in_the_table() -> None:
    """``error`` is excluded, and the exclusion is deliberate.

    It is the code for a status with no specific code, and it travels with
    whatever status the framework raised -- 400, 404 or 500. Listing it
    against one of those would be the table making a claim it cannot keep.
    """
    from tests._api_codes import backend_error_codes

    assert _STATUS_FOLLOWS_CODE not in _CODE_STATUS
    assert _STATUS_FOLLOWS_CODE in backend_error_codes(), (
        "the status-following code is what _code_for_status hands out, so it "
        "must be one the API can send -- it just has no single status"
    )


def test_a_mismatched_pair_is_refused_at_construction() -> None:
    """The table is consulted, not merely present.

    A table that only a test reads is documentation; a table the error
    constructor reads is a constraint.
    """
    with pytest.raises(ValueError, match="is served as 404, not 500"):
        ApiError(500, "ticket_not_found", "no such ticket")

    # The correct pair still works.
    error = ApiError(404, "ticket_not_found", "no such ticket")
    assert error.status_code == 404
    assert error.code == "ticket_not_found"
