"""The codes the API raises must be the codes the table and the contract name.

## Why this file exists

The error envelope has one shape and one vocabulary, and three places have to
agree about it:

* ``src/opspilot/api/errors.py``'s ``_CODE_STATUS`` maps every code to the
  status it travels with.
* ``docs/api-contract.md`` §6 prints the same mapping as a table, because a
  client branches on ``code``.
* The routes raise ``ApiError(status, code, ...)`` with the pair inline.

Nothing compared the three. A route that raises ``ApiError(409,
"ticket_exists")`` while the table says ``ticket_exists`` is 404 would produce
an envelope the contract does not describe, and the contract is what a client
is written against.

**Direction matters here.** The table can hold a code no route ever raises --
the table is a vocabulary, and a vocabulary is allowed to be larger than the
speech. What cannot happen is the other way round: a route raising a code the
table does not know, or raising it with the wrong status.

## What this file asserts

1. **Every code a route raises is in ``_CODE_STATUS``**, with the status the
   route used.
2. **Every code a route raises is in the contract's §6 table**, with the same
   status there.
3. **The table and the code agree with each other** for every code the table
   lists that the code also knows -- so a status edited in one place and not
   the other is caught.
4. **The routes are actually read.** A guard that finds no ``ApiError`` call
   would pass on an empty tree, so the count is asserted.

## What it does not do

It does not check that every code in the table is reachable, and it does not
check the messages or the ``details``. Reachability is a question about which
route a code can come from, and this file is about the vocabulary.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ERRORS = REPO_ROOT / "src" / "opspilot" / "api" / "errors.py"
ROUTERS = REPO_ROOT / "src" / "opspilot" / "api" / "routers"
CONTRACT = REPO_ROOT / "docs" / "api-contract.md"


def _registered_codes() -> dict[str, int]:
    """``_CODE_STATUS``: every code the API knows, and the status it travels with."""
    tree = ast.parse(ERRORS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "_CODE_STATUS"
            and isinstance(node.value, ast.Dict)
        ):
            codes: dict[str, int] = {}
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if not isinstance(key, ast.Constant) or not isinstance(value, ast.Constant):
                    continue
                if not isinstance(key.value, str) or not isinstance(value.value, int):
                    continue
                codes[key.value] = value.value
            return codes
    raise AssertionError("errors.py no longer declares _CODE_STATUS")


def _raised_codes() -> dict[str, set[int]]:
    """Every ``ApiError(status, code)`` the routers raise, and the statuses used."""
    raised: dict[str, set[int]] = {}
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "ApiError" or len(node.args) < 2:
                continue
            status, code = node.args[0], node.args[1]
            if not isinstance(status, ast.Constant) or not isinstance(code, ast.Constant):
                continue
            if not isinstance(status.value, int) or not isinstance(code.value, str):
                continue
            raised.setdefault(code.value, set()).add(status.value)
    return raised


def _contract_table() -> dict[str, set[int]]:
    """The contract's §6 table: every code it lists and the status beside it."""
    lines = CONTRACT.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "| HTTP | Codes |"),
        None,
    )
    assert start is not None, "the contract no longer has the §6 error table"
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## ")),
        len(lines),
    )
    rows: dict[str, set[int]] = {}
    for line in lines[start + 1 : end]:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        try:
            status = int(cells[0])
        except ValueError:
            continue
        for code in re.findall(r"`([a-z_]+)`", cells[1]):
            rows.setdefault(code, set()).add(status)
    return rows


def test_the_codes_the_routes_raise_are_the_codes_the_table_knows() -> None:
    """Every code a route raises is registered, with the status it travels with."""
    registered = _registered_codes()
    raised = _raised_codes()

    assert raised, "no ApiError call was found in the routers; the guard is reading nothing"
    assert registered, "errors.py declares no code table"

    unknown = sorted(set(raised) - set(registered))
    assert not unknown, (
        f"the routers raise codes the table does not know: {unknown}. An envelope "
        "with a code the contract does not list is an envelope a client cannot "
        "branch on."
    )

    wrong = {
        code: (sorted(statuses), registered[code])
        for code, statuses in sorted(raised.items())
        if not statuses <= {registered[code]}
    }
    assert not wrong, (
        "these codes are raised with a status the table does not give them:\n  "
        + "\n  ".join(
            f"{code}: raised with {statuses}, the table says {expected}"
            for code, (statuses, expected) in wrong.items()
        )
    )


def test_the_codes_the_routes_raise_are_the_ones_the_contract_lists() -> None:
    """Every code a route raises is in the contract's §6 table."""
    contract = _contract_table()
    raised = _raised_codes()

    assert contract, "the contract's error table could not be read"

    missing = sorted(set(raised) - set(contract))
    assert not missing, (
        f"the routers raise codes the contract's §6 table does not list: {missing}. "
        "The contract is what a client is written against."
    )

    wrong = {
        code: (sorted(raised[code]), sorted(contract[code]))
        for code in sorted(set(raised) & set(contract))
        if not raised[code] <= contract[code]
    }
    assert not wrong, (
        "these codes travel with a status the contract does not give them:\n  "
        + "\n  ".join(
            f"{code}: the routers use {raised_status}, the contract says {contract_status}"
            for code, (raised_status, contract_status) in wrong.items()
        )
    )


def test_the_contract_and_the_code_agree_where_they_both_know_a_code() -> None:
    """The two tables are the same table, for every code they share."""
    registered = _registered_codes()
    contract = _contract_table()

    shared = sorted(set(registered) & set(contract))
    assert shared, "the contract and the code have no code in common"

    mismatched = {
        code: (sorted(contract[code]), registered[code])
        for code in shared
        if registered[code] not in contract[code]
    }
    assert not mismatched, (
        "the contract and the code give the same code different statuses:\n  "
        + "\n  ".join(
            f"{code}: the contract says {contract_status}, the code says {code_status}"
            for code, (contract_status, code_status) in mismatched.items()
        )
    )
