"""The registered tool names must equal the names the contract specifies.

This test exists because of a real defect, and the shape of the defect is the
reason the test is written this way.

The M2 servers registered their tools under bare function names -- `get_invoice`,
`get_customer`, `create` -- because `@server.tool()` defaults the name to the
function name (`docs/mcp-sdk-notes.md` §8). The specification calls them
`billing.get_invoice`, `crm.get_customer`, `issues.create`. Both servers' test
suites passed, because they called the servers using the same bare names the
servers had registered: the tests agreed with the implementation and both
disagreed with the contract.

The consequence was not cosmetic. `TOOL_REGISTRY` in `domain/tools.py` is keyed on
the prefixed names, and gate 2 of the permission model is a registry lookup. A
model proposing `billing.issue_refund` would not have resolved.

So this test does not ask the code what its tool names are and compare that to
itself. It reads the **contract document** and compares the registered names to
what the document says, which is the only comparison that can catch the two
drifting apart.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from opspilot.domain.tools import TOOL_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "mcp-contracts.md"

# Module path -> the server attribute the module exposes.
SERVERS = {
    "crm": "mcp_servers.crm.server",
    "billing": "mcp_servers.billing.server",
    "issues": "mcp_servers.issues.server",
}


def _names_in_contract() -> set[str]:
    """Every ``<server>.<tool>`` name the contract document mentions in a heading.

    Read from the document rather than hard-coded here, so that adding a tool to
    the contract without adding it to a server (or the reverse) is a failure
    rather than a test that quietly keeps testing the old set.
    """
    text = CONTRACT.read_text(encoding="utf-8")
    return set(re.findall(r"^###\s+`((?:crm|billing|issues)\.[a-z_]+)`", text, re.MULTILINE))


def _registered_names(module_path: str) -> set[str]:
    """The tool names a server actually registers, via the MCP SDK's own listing."""
    import importlib

    server = importlib.import_module(module_path).server
    tools = asyncio.run(server.list_tools())
    return {tool.name for tool in tools}


@pytest.fixture(scope="module")
def contract_names() -> set[str]:
    """The names the contract specifies, with a guard against parsing nothing."""
    names = _names_in_contract()
    assert names, (
        f"no `<server>.<tool>` headings were found in {CONTRACT}. Either the "
        f"document changed shape or the pattern no longer matches it -- and a "
        f"comparison against an empty set passes for every server."
    )
    return names


@pytest.mark.parametrize("server_key", sorted(SERVERS))
def test_registered_tool_names_match_the_contract(
    server_key: str, contract_names: set[str]
) -> None:
    """Each server registers exactly the names the contract gives it."""
    module_path = SERVERS[server_key]
    expected = {n for n in contract_names if n.startswith(f"{server_key}.")}
    registered = _registered_names(module_path)

    assert registered == expected, (
        f"{module_path} registers {sorted(registered)} but the contract "
        f"(docs/mcp-contracts.md) specifies {sorted(expected)}.\n"
        f"  registered but not in the contract: {sorted(registered - expected)}\n"
        f"  in the contract but not registered: {sorted(expected - registered)}\n"
        f"A name mismatch is not cosmetic: gate 2 of the permission model is a "
        f"registry lookup keyed on the contracted names, so a tool that is named "
        f"differently cannot be permission-checked."
    )


def test_every_registered_tool_is_in_the_permission_registry(contract_names: set[str]) -> None:
    """Every tool a server exposes has an entry in ``TOOL_REGISTRY``.

    The contract and the permission registry must agree, because a tool that
    exists but has no permission entry has no defined risk level -- and the
    permission lookup is what decides whether it may execute automatically.
    """
    registered: set[str] = set()
    for module_path in SERVERS.values():
        registered |= _registered_names(module_path)

    unregistered = sorted(n for n in registered if n not in TOOL_REGISTRY)
    assert not unregistered, (
        f"these tools are exposed by a server but have no TOOL_REGISTRY entry, so "
        f"their permission is undefined: {unregistered}"
    )


def test_no_tool_name_is_registered_by_two_servers() -> None:
    """Tool names are globally unique across the servers.

    A duplicate would make a registry lookup ambiguous, and the permission model
    has no way to express "which server did you mean".
    """
    seen: dict[str, str] = {}
    collisions: list[str] = []

    for server_key, module_path in SERVERS.items():
        for name in _registered_names(module_path):
            if name in seen:
                collisions.append(f"{name} registered by both {seen[name]} and {server_key}")
            seen[name] = server_key

    assert not collisions, "duplicate tool names across servers:\n  " + "\n  ".join(collisions)
