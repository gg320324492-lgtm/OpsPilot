"""``knowledge`` is not an MCP server -- the guard that stops one growing back.

``docs/mcp-contracts.md`` §4 is titled **"The ``knowledge`` tool is not an MCP
server"** and states the contract directly::

    `knowledge.search` is registered in the tool registry with permission `READ`
    and `server="internal"`. It is implemented in-process against pgvector rather
    than as a fourth MCP server.

This file is the guard that keeps that true. It follows the technique of
``tests/integration/test_mcp_contract_names.py``: the *claim* is read out of the
contract document, never out of the code, because a test that asks the code what
it did agrees with the code and both can disagree with the spec.

The defect this exists to catch is real and specific. An earlier slice built a
fourth MCP server (``mcp_servers/knowledge/server.py``), registered it in the
gateway's server set, and added a ``_SERVER_ALIASES = {"internal": "knowledge"}``
translation so the registry's ``server="internal"`` resolved to it. **Its nine
tests all passed** -- they verified the server was built correctly (name,
permission, refusal shape) and not one asked whether it should be built at all.
The server has since been deleted and ``knowledge.search`` is dispatched
in-process. These assertions are what fail if anyone reintroduces the fourth
server.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from opspilot.adapters.tools import mcp_gateway
from opspilot.domain.tools import TOOL_REGISTRY, Permission

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "mcp-contracts.md"


def _knowledge_section() -> str:
    """The text of ``docs/mcp-contracts.md`` §4, read from the document."""
    text = CONTRACT.read_text(encoding="utf-8")
    match = re.search(
        r"^##\s+4\.\s+The\s+`knowledge`\s+tool\s+is\s+not\s+an\s+MCP\s+server\s*$(.*?)(?=^##\s)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, (
        "docs/mcp-contracts.md no longer has a section titled 'The `knowledge` "
        "tool is not an MCP server'. Either the contract changed or this pattern "
        "no longer matches it -- and a guard against a section that cannot be "
        "found passes vacuously."
    )
    return match.group(1)


@pytest.fixture(scope="module")
def contract_claim() -> str:
    """§4's text, with a guard against reading an empty string."""
    section = _knowledge_section()
    assert section.strip(), "the contract's §4 is empty; the claim cannot be read"
    return section


# ---------------------------------------------------------------------------
# The contract's claim
# ---------------------------------------------------------------------------


def test_the_contract_says_knowledge_is_not_an_mcp_server(contract_claim: str) -> None:
    """The premise: §4 states the tool is in-process, not a fourth server.

    If the document is ever edited to say otherwise, this test should be deleted
    deliberately rather than left asserting a claim the spec no longer makes.
    """
    normalised = " ".join(contract_claim.split())
    assert "in-process" in normalised, contract_claim
    assert "fourth MCP server" in normalised, contract_claim
    assert 'server="internal"' in normalised, contract_claim


def test_no_knowledge_server_module_exists() -> None:
    """There is no ``mcp_servers/knowledge`` package.

    The contract says the tool is not a server; a server module that exists is a
    server that can be wired. Its absence is the strongest form of the claim.
    """
    assert not (REPO_ROOT / "mcp_servers" / "knowledge").exists(), (
        "mcp_servers/knowledge/ exists, so a fourth MCP server is present even "
        "though docs/mcp-contracts.md §4 says knowledge is not one"
    )


# ---------------------------------------------------------------------------
# The gateway's server set
# ---------------------------------------------------------------------------


def test_knowledge_is_not_in_the_gateway_server_set() -> None:
    """``knowledge`` is not a server the gateway can dispatch to.

    ``_KNOWN_SERVERS`` is the gateway's server set. §4's claim holds only if
    ``knowledge`` is absent from it -- a name here is a name the gateway will try
    to build and dispatch through.
    """
    known = getattr(mcp_gateway, "_KNOWN_SERVERS", None)
    assert known is not None, "the gateway no longer exposes _KNOWN_SERVERS; update this guard"
    assert "knowledge" not in known, (
        f"the gateway's server set is {sorted(known)}; it must not contain "
        f"'knowledge' (docs/mcp-contracts.md §4)"
    )
    assert known == frozenset({"crm", "billing", "issues"}), (
        f"the gateway dispatches to exactly the three external servers; got {sorted(known)}"
    )


def test_the_gateway_builds_no_knowledge_server() -> None:
    """``build_in_process_servers`` returns only the three external servers."""
    servers = mcp_gateway.build_in_process_servers()
    assert set(servers) == {"crm", "billing", "issues"}, (
        f"build_in_process_servers built {sorted(servers)}; docs/mcp-contracts.md "
        f"§4 says knowledge is not one of the MCP servers"
    )


def test_no_server_alias_translates_internal_to_a_server() -> None:
    """The ``_SERVER_ALIASES`` translation is gone, not merely unused.

    That map existed only to make ``knowledge.search``'s ``server="internal"``
    resolve to a ``knowledge`` MCP server. Leaving it in place would be a
    dormant way for the fourth server to return: re-adding the server entry would
    make the alias work again without anyone editing the alias.
    """
    assert not hasattr(mcp_gateway, "_SERVER_ALIASES"), (
        "mcp_gateway still defines _SERVER_ALIASES; it existed only to route "
        "'internal' to a knowledge MCP server (docs/mcp-contracts.md §4)"
    )


# ---------------------------------------------------------------------------
# Dispatch: knowledge.search does not go through the MCP gateway
# ---------------------------------------------------------------------------


async def test_knowledge_search_does_not_route_through_mcp_dispatch() -> None:
    """The MCP gateway refuses ``knowledge.search`` rather than serving it.

    Its registry spec says ``server="internal"``, and the gateway's server set is
    the three external servers only, so a dispatch attempt is refused with
    ``server_unavailable`` -- the same result any unserved ``server`` value would
    get. It is *not* routed to an MCP server.
    """
    gateway = mcp_gateway.MCPToolGateway()
    result = await gateway.call_tool("knowledge.search", {"query": "refund policy"})

    assert result.ok is False
    assert result.error == "server_unavailable", (
        f"the MCP gateway answered knowledge.search with {result.error!r}; §4 says "
        f"it is dispatched in-process, so reaching the MCP gateway at all is the bug"
    )


def test_knowledge_search_stays_a_registered_read_tool() -> None:
    """``knowledge.search`` remains registered, ``READ``, ``server="internal"``.

    §4 says it "is registered in the tool registry with permission ``READ`` and
    ``server="internal"``". Deleting the fourth server must not delete the tool:
    the permission machinery still covers it and the eval's tool-selection
    dataset still includes it uniformly.
    """
    spec = TOOL_REGISTRY["knowledge.search"]
    assert spec.permission is Permission.READ
    assert spec.server == "internal", (
        f"knowledge.search's server is {spec.server!r}; the contract says 'internal'"
    )
