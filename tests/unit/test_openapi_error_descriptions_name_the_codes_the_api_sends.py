"""The generated OpenAPI document must name the codes the API actually sends.

## Why this file exists

``docs/api-contract.md`` §6 is the vocabulary of record: a table of HTTP status
to the codes the API sends with it. The contract's own preamble says the
generated OpenAPI document "is treated as part of the contract, not as
documentation that drifts" — so the document and the table must agree, and
nothing checked that they did.

They did not. The document's description for a 422 named
``validation_error``, and the API sends ``schema_invalid`` for every 422 it
raises: the one 422 path in the codebase is FastAPI's request-body validation,
and the handler answers with that code. A client generated from the document
would have typed a branch on a code the API never sends, and would have had no
typed branch on the one it does.

The module that builds the document states the rule it broke: *"The
descriptions name the *code* a client branches on, never the message."*

## What this file asserts

For every status in the generated document's shared ``responses`` mapping, each
code its description names must be one the contract's §6 table maps to **that
same status**.

A description that names no code — a prose one, or a wildcard like
``*_already_decided`` — is not checked, because it makes no claim a client
could read as a branch. A description that names a code is checked against the
table, and a mismatch is a document that would mislead a generator.

The table is the authority, not the code: the point is that the *published*
document agrees with the *written* contract, so a client that reads either one
is not told something the other denies.
"""

from __future__ import annotations

import pathlib
import re

from opspilot.api.errors import ERROR_RESPONSES

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A row of the contract's §6 table: ``| 422 | `schema_invalid` — ... |``
_TABLE_ROW = re.compile(r"^\|\s*(\d{3})\s*\|(.*)\|\s*$")

#: A code as it is written in the table and in a description.
_CODE = re.compile(r"^[a-z][a-z0-9_]*$")

#: A backticked token in a description.
_BACKTICKED = re.compile(r"`([^`]+)`")


def _contract_codes_by_status() -> dict[int, set[str]]:
    """The contract's §6 table, as ``status -> the codes it sends``.

    Read from the document rather than imported, because the document *is* the
    authority here: the question is whether the generated OpenAPI agrees with
    it, and a copy of the table in the test would be a third answer to drift
    from.
    """
    text = (REPO_ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    codes: dict[int, set[str]] = {}
    for line in text.splitlines():
        match = _TABLE_ROW.match(line.strip())
        if match is None:
            continue
        status = int(match.group(1))
        found = {token for token in _BACKTICKED.findall(match.group(2)) if _CODE.match(token)}
        if found:
            codes.setdefault(status, set()).update(found)
    return codes


def test_the_contract_table_was_found() -> None:
    """The parse found the table.

    A guard that reads nothing and checks nothing is green, and a green guard
    that checks nothing is worse than no guard. This says the table is there
    and that it has the statuses the API declares.
    """
    codes = _contract_codes_by_status()
    assert codes, "the contract's §6 table was not found; the parse is broken"
    for status in ERROR_RESPONSES:
        assert int(status) in codes, (
            f"the API declares a {status} response but the contract's §6 table "
            f"does not list one. The table's statuses are {sorted(codes)}."
        )


def test_every_description_names_a_code_the_contract_maps_to_that_status() -> None:
    """Each published description names a code that status can carry.

    The 422 is the case that motivated this: the description said
    ``validation_error`` and the API sends ``schema_invalid``.
    """
    codes = _contract_codes_by_status()
    wrong: list[str] = []
    for status, response in ERROR_RESPONSES.items():
        description = str(response.get("description", ""))
        for token in _BACKTICKED.findall(description):
            if not _CODE.match(token):
                # A wildcard or a prose phrase: it names no single code, so it
                # makes no claim a generator could read as a branch.
                continue
            allowed = codes.get(int(status), set())
            if token not in allowed:
                wrong.append(
                    f"{status}: the description names `{token}`, and the "
                    f"contract's §6 table maps {status} to "
                    f"{sorted(allowed) or 'nothing'}"
                )
    assert not wrong, (
        "the generated document names codes the contract does not map to that "
        "status:\n  " + "\n  ".join(wrong)
    )


def test_the_422_the_api_raises_is_the_one_the_document_names() -> None:
    """The one 422 the API can raise is named in the document.

    This is the specific case the general check would catch, said plainly so
    that a future edit to the general check cannot quietly stop catching it.
    """
    description = str(ERROR_RESPONSES[422].get("description", ""))
    assert "schema_invalid" in description, (
        "the document's 422 description does not name `schema_invalid`, which "
        "is the code the API sends for every 422 it raises"
    )
    assert "validation_error" not in description, (
        "the document's 422 description names `validation_error`, which the API "
        "never sends with a 422 — it is the 400's code"
    )
