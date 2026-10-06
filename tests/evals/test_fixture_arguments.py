"""Every fixture's proposed tool arguments must be accepted by the real servers.

``evals/datasets/fixtures/*.json`` is the script ``FakeModelProvider`` replays.
A ``choose_tool`` call in one of those files is a *claim about the servers*: it
says "this argument set is a thing the agent may send, and this is what comes
back". Nothing checked that claim. Two calls in the committed fixtures were
simply wrong -- ``crm.get_customer(customer_id="ACME")`` asked for a customer the
CRM has never heard of, and ``billing.list_transactions(account_id="AC-4471")``
sent a shape the tool does not accept at all -- and the suite stayed green,
because a fixture is data and nothing executed it.

This file executes it. For every fixture under ``evals/datasets/fixtures/`` it
loads the proposed calls, pushes each one's arguments through the real
:class:`MCPToolGateway` against freshly-built in-process servers, and asserts the
outcome is the one the fixture means. The fixtures are *read*, never transcribed
here, so a fixture added tomorrow is covered by the same loop.

**The assertion is not "ok is True".** ``crm.get_customer`` returns
``ok=True`` with ``{"customer": null, "error": {"code": "not_found"}}`` for an
unknown id: the CRM encodes a miss inside its success payload rather than as a
refusal, so the ``ok`` flag alone cannot tell a lookup that found ACME from one
that found nothing. A guard that stopped at ``ok`` would have passed the exact
defect it was written to catch. :func:`_assert_found` therefore requires the
payload to actually contain the record.

**Expected failures are declared, never inferred.** A scenario that wants a
refusal -- one where the correct behaviour is to attempt something the server
rejects -- must say so in the call itself, via an ``"expect_error"`` key holding
the exact code. Nothing here guesses: a call with no ``expect_error`` is asserted
to succeed, so the default is the strict one and a fixture author has to opt in
to leniency.

**The MCP server is the authority, and the divergence that used to exist is
gone.** Gate 1 (``domain.tools``) and the servers disagreed until M6c:
``TransactionListArgs`` declared ``billing.list_transactions(account_id=...)``
while the billing server implements ``list_transactions(invoice_id=...)`` and
``docs/mcp-contracts.md`` §S2 documents the ``invoice_id`` form. No argument set
satisfied both, so the fixtures were written against the *server* and the
disagreement was pinned in :data:`KNOWN_GATE1_DIVERGENCES` -- which is now empty,
because ``TransactionListArgs`` matches the contract.
:func:`test_the_gate1_divergence_list_is_still_accurate` guards that emptiness in
both directions: an entry that stops being a divergence fails, so a stale
justification cannot linger, and
:func:`test_the_gate1_divergence_list_is_still_accurate` fails if the list stops
describing reality, so the defect is pinned in the open instead of absorbed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.domain.tools import (
    TOOL_ARGUMENT_SCHEMAS,
    TOOL_REGISTRY,
    InvalidArgumentsError,
    validate_tool_call,
)

pytestmark = pytest.mark.asyncio

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "fixtures"

# A fixed run id standing in for the real one when deriving an idempotency key.
# See ``_dispatch_arguments``.
_STAND_IN_RUN_ID = "fixture-guard"

# The key a fixture puts on a ``choose_tool`` call to declare that the call is
# meant to fail, and the exact ``ToolResult.error`` code it is meant to fail
# with. Named here because the fixtures depend on it.
EXPECT_ERROR_KEY = "expect_error"

# The key(s) whose presence proves a lookup found its record, per tool. Two
# shapes exist across the servers, and a blanket "any populated key" check gets
# both wrong: the CRM nests under ``customer``/``account``/``subscription`` and
# reports a miss as ``ok=True`` with that key ``None`` plus an ``error`` object,
# while ``billing.get_invoice`` *flattens* its record and reports a miss as
# ``code="not_found"``. ``billing.list_transactions`` legitimately returns an
# empty ``transactions`` list for an invoice with no charges, so its evidence is
# the absence of an error code rather than a non-empty list.
_RECORD_EVIDENCE: dict[str, tuple[str, ...]] = {
    "crm.get_customer": ("customer",),
    "crm.get_account": ("account",),
    "crm.get_subscription": ("subscription",),
    "billing.get_invoice": ("invoice_id",),
    "billing.list_transactions": ("transactions",),
    "billing.issue_refund": ("refund_id",),
    "issues.search": (),
    "issues.create": ("key",),
    "knowledge.search": (),
}

# Tools where gate 1 and the MCP server declare different arguments, mapped to
# the server's fields. ``docs/mcp-contracts.md`` §S2 and the server agree on
# ``invoice_id``; ``domain.tools.TransactionListArgs`` says ``account_id``. No
# argument set satisfies both, so the fixtures target the server and this entry
# keeps the disagreement visible instead of leaving the layer that is wrong
# silently wrong. See the module docstring; this is the one finding this work
# could not fix inside its own scope.
# Gate 1 (``domain.tools.TOOL_ARGUMENT_SCHEMAS``) and the servers must accept
# the same arguments. This map is the escape hatch for a divergence that is known
# and pinned rather than silently absorbed, and it is **empty**: the one entry it
# held -- ``billing.list_transactions`` declaring ``account_id`` while the server
# takes ``invoice_id`` -- was fixed in M6c, and
# ``test_the_gate1_divergence_list_is_still_accurate`` failed until the entry was
# removed, which is that mechanism working. An empty list is the honest state;
# adding an entry back requires saying what cannot be fixed and why.
KNOWN_GATE1_DIVERGENCES: dict[str, tuple[str, ...]] = {}


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[MCPToolGateway]:
    """A gateway over in-process servers backed by this test's ``tmp_path``.

    A private store per test is not tidiness: ``billing.issue_refund`` mutates,
    so a gateway over a shared store would let one fixture's refund decide
    whether another's succeeds.
    """
    yield MCPToolGateway(servers=build_in_process_servers(tmp_path))


def _fixture_paths() -> list[Path]:
    """Every fixture file, sorted for a stable test order."""
    return sorted(FIXTURES_DIR.glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _tool_calls(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``choose_tool`` calls in a fixture, in recorded order.

    Keyed on ``method`` rather than on position, matching how
    ``FakeModelProvider._call_for`` selects by method, and skipping the terminal
    ``tool_name: null`` proposal -- that is the agent declining to call anything,
    which has no arguments to check and would otherwise need a fake tool name.
    """
    calls = fixture.get("calls", [])
    assert isinstance(calls, list), "fixture 'calls' must be a list"
    selected = [c for c in calls if isinstance(c, dict) and c.get("method") == "choose_tool"]
    return [
        c for c in selected if c.get("response", {}).get("value", {}).get("tool_name") is not None
    ]


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    value = call.get("response", {}).get("value", {})
    arguments = value.get("arguments", {})
    assert isinstance(arguments, dict), "choose_tool 'arguments' must be an object"
    return arguments


def _expected_error(call: dict[str, Any]) -> str | None:
    """The failure code a call declares, or ``None`` if it declares none.

    Lives on the ``call`` rather than on the fixture as a whole because the
    decision is per-call: a scenario may have three probes that must succeed and
    a fourth that is meant to be refused. An undeclared call is
    expected-to-succeed -- see the module docstring.
    """
    declared = call.get(EXPECT_ERROR_KEY)
    if declared is None:
        return None
    assert isinstance(declared, str), f"{EXPECT_ERROR_KEY!r} must be a string error code"
    return declared


def _assert_found(tool_name: str, result: dict[str, Any]) -> None:
    """Assert a successful lookup actually returned its record.

    The extra check the ``ok`` flag cannot make. See the module docstring for
    the ``crm.get_customer`` miss that arrives as ``ok=True``, and
    :data:`_RECORD_EVIDENCE` for the per-tool record keys.
    """
    # The CRM's shape: a miss is an ``error`` object beside a ``None`` record.
    error = result.get("error")
    assert error is None, (
        f"{tool_name} returned ok=True but carried an error payload: {error!r} "
        f"-- the server found nothing, so this argument set does not identify a "
        f"real record"
    )
    # The billing shape: a miss is ``code="not_found"`` on an otherwise blank row.
    assert result.get("code") is None, (
        f"{tool_name} returned ok=True but code={result.get('code')!r}: "
        f"the server refused this argument set"
    )

    evidence = _RECORD_EVIDENCE[tool_name]
    for key in evidence:
        if key == "transactions":
            # An empty list is a legitimate answer, so its presence is the
            # evidence rather than its length.
            assert key in result, f"{tool_name} returned no {key!r} key: {sorted(result)}"
            continue
        assert result.get(key), (
            f"{tool_name} returned ok=True with no {key!r}: the lookup found "
            f"nothing, so this argument set does not identify a real record"
        )


def _dispatch_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The payload the runtime would actually send for a fixture's proposal.

    A fixture records what the *model* proposed, which is not always what
    reaches the server. For ``billing.issue_refund`` the two differ by design:
    ``RefundArgs`` forbids the model from supplying ``idempotency_key`` (letting
    a proposal pick its own key would defeat the duplicate-refund check), and
    ``agents/runtime.py`` gate 4 derives one from ``(run_id, transaction_id)``
    and injects it at dispatch. Sending the raw fixture arguments would make the
    guard report a healthy call as broken, and "fixing" the fixture by adding
    the key would introduce the very defect the design forbids.

    So the derived key is added here exactly where the runtime adds it. The key
    is a fixed stand-in for a ``run_id`` -- this file is about argument *shape*,
    not about idempotency, which ``tests/integration/test_refund_idempotency.py``
    covers against the real store.
    """
    payload = dict(arguments)
    if TOOL_REGISTRY[tool_name].requires_idempotency_key:
        payload["idempotency_key"] = f"refund:{_STAND_IN_RUN_ID}:{payload.get('transaction_id')}"
    return payload


async def test_every_fixture_exists_and_declares_a_scenario() -> None:
    """The fixtures directory is not empty and each file names its scenario.

    A guard that loops over ``glob("*.json")`` is vacuously green when the
    directory is empty or the files are renamed, so the loop's precondition is
    asserted on its own.
    """
    paths = _fixture_paths()
    assert paths, f"no fixtures found in {FIXTURES_DIR}"

    for path in paths:
        fixture = _load(path)
        assert fixture.get("scenario"), f"{path.name} has no 'scenario'"
        assert "recorded" in fixture, f"{path.name} has no 'recorded' flag"
        assert isinstance(fixture["recorded"], bool)


async def test_fixtures_still_declare_themselves_hand_written() -> None:
    """``recorded`` stays ``false`` until a fixture is captured from a real run.

    ``recorded: false`` is the honest label for a hand-written script
    (``docs/risks.md`` R2). Flipping it to ``true`` without a recording would
    claim evidence this repository does not have, so the flag is pinned here
    rather than left to whichever fixture is edited next.
    """
    for path in _fixture_paths():
        assert _load(path)["recorded"] is False, (
            f"{path.name} claims recorded=true. Only record_fixture() against a "
            f"real provider may set that (see adapters/models/fake.py)."
        )


async def test_fixture_tool_names_are_registered(gateway: MCPToolGateway) -> None:
    """Every proposed tool is in the static registry.

    Gate 2 would reject an unregistered name, so a fixture naming one scripts a
    run that dies before reaching a server. Cheap to check and it keeps the
    argument check below honest about what it is testing.
    """
    for path in _fixture_paths():
        for call in _tool_calls(_load(path)):
            tool_name = call["response"]["value"]["tool_name"]
            assert tool_name in TOOL_REGISTRY, (
                f"{path.name} proposes unregistered tool {tool_name!r}"
            )


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
async def test_fixture_tool_arguments_are_accepted_by_the_real_servers(
    fixture_path: Path, gateway: MCPToolGateway
) -> None:
    """Each proposed call reaches its server and comes back as the fixture means.

    The guard's core. The arguments are dispatched through ``MCPToolGateway`` to
    an in-process MCP server, so the generated JSON Schema and the tool body both
    run -- the server is what decides whether an argument set is legal. The
    outcome is then asserted against what the fixture declared: ``expect_error``
    means fail with exactly that code, anything else means succeed *and* return
    a real record.
    """
    fixture = _load(fixture_path)
    calls = _tool_calls(fixture)
    assert calls, f"{fixture_path.name} proposes no tool call to check"

    for index, call in enumerate(calls):
        value = call["response"]["value"]
        tool_name = value["tool_name"]
        arguments = _arguments(call)
        where = f"{fixture_path.name} call[{index}] {tool_name}({json.dumps(arguments)})"
        expected_error = _expected_error(call)

        result = await gateway.call_tool(tool_name, _dispatch_arguments(tool_name, arguments))

        if expected_error is not None:
            assert result.ok is False, (
                f"{where} declares expect_error={expected_error!r} but succeeded "
                f"with {json.dumps(result.result)}"
            )
            assert result.error == expected_error, (
                f"{where} expected error {expected_error!r}, got {result.error!r}"
            )
        else:
            assert result.ok is True, (
                f"{where} returned ok=False with error={result.error!r}: "
                f"{json.dumps(result.result)}. If this call is *meant* to fail, "
                f'declare it with "{EXPECT_ERROR_KEY}": "<code>".'
            )
            assert result.result is not None, f"{where} returned ok=True with no payload"
            _assert_found(tool_name, result.result)


async def test_the_gate1_divergence_list_is_still_accurate() -> None:
    """The pinned gate-1/server disagreements still hold, and only those.

    A divergence list is only honest while it is checked. This asserts both
    directions: every entry named here is *still* a disagreement, and every tool
    the fixtures propose that is not listed must pass gate 1 cleanly. Fixing
    ``TransactionListArgs`` therefore turns this red and prompts the entry's
    removal, instead of leaving a stale justification in a test docstring.
    """
    for tool_name, server_fields in KNOWN_GATE1_DIVERGENCES.items():
        gate1_fields = set(TOOL_ARGUMENT_SCHEMAS[tool_name].model_fields)
        assert gate1_fields != set(server_fields), (
            f"{tool_name} no longer diverges: gate 1 and the server now agree on "
            f"{sorted(gate1_fields)}. Remove it from KNOWN_GATE1_DIVERGENCES."
        )

    for path in _fixture_paths():
        for index, call in enumerate(_tool_calls(_load(path))):
            tool_name = call["response"]["value"]["tool_name"]
            if tool_name in KNOWN_GATE1_DIVERGENCES:
                continue
            try:
                validate_tool_call(tool_name, _arguments(call))
            except InvalidArgumentsError as exc:
                pytest.fail(
                    f"{path.name} call[{index}] {tool_name} fails gate 1 and is not "
                    f"listed in KNOWN_GATE1_DIVERGENCES: {exc}"
                )
