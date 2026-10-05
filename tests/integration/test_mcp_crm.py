"""Contract tests for the ``crm`` MCP server (``docs/mcp-contracts.md`` S1).

Behaviour is exercised in-process with ``await server.call_tool(name, args)``
(``docs/mcp-sdk-notes.md`` S6): fast, no subprocess, no pipe to flake on. The
one thing that cannot cover -- that this is a real MCP server over the wire --
is covered by ``test_crm_server_lists_tools_over_stdio`` at the bottom.

Every assertion is against the *return shape* documented in the contract, not
against the store, because the shape is what the agent sees.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult

from mcp_servers._store import Store
from mcp_servers.crm import server as crm

_CRM_DIR = Path(__file__).resolve().parents[2] / "mcp_servers" / "crm"


async def _call(name: str, arguments: dict[str, object]) -> CallToolResult:
    """Dispatch a tool in-process and narrow the result to ``CallToolResult``.

    ``MCPServer.call_tool`` is typed as ``CallToolResult | InputRequiredResult``
    because the protocol permits a tool to request more input mid-call. None of
    these tools can -- they take plain values and return a result -- so a test
    that sees ``InputRequiredResult`` is itself the bug, and this helper fails
    loudly rather than letting an assertion trace through the union.
    """
    result = await crm.server.call_tool(name, arguments)
    assert isinstance(result, CallToolResult), f"{name} returned {type(result).__name__}"
    return result


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give each test its own store file, reset to the seed, in ``tmp_path``.

    The server's module-level ``_store`` is rebuilt so no test writes into the
    next test's state and none of them touch the repository.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        crm,
        "_store",
        Store(seed_path=_CRM_DIR / "seed.json", filename="crm.json", data_dir=tmp_path),
    )


async def test_get_customer_by_acme_email() -> None:
    result = await _call("crm.get_customer", {"customer_email": "billing@acme.example"})
    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    customer = payload["customer"]
    assert customer["customer_id"] == "CUS-1001"
    assert customer["company"] == "ACME"
    assert customer["plan"] == "enterprise"
    assert customer["status"] == "active"
    assert payload["error"] is None


async def test_get_customer_by_id() -> None:
    result = await _call("crm.get_customer", {"customer_id": "CUS-1001"})
    assert result.structured_content is not None
    assert result.structured_content["customer"]["email"] == "billing@acme.example"


async def test_get_customer_with_both_agreeing_is_accepted() -> None:
    result = await _call(
        "crm.get_customer", {"customer_email": "billing@acme.example", "customer_id": "CUS-1001"}
    )
    assert result.structured_content is not None
    assert result.structured_content["customer"]["customer_id"] == "CUS-1001"
    assert result.structured_content["error"] is None


async def test_get_customer_with_both_disagreeing_is_validation_error() -> None:
    result = await _call(
        "crm.get_customer", {"customer_email": "billing@acme.example", "customer_id": "CUS-1002"}
    )
    assert result.is_error is False  # errors are results, not transport errors
    assert result.structured_content is not None
    assert result.structured_content["customer"] is None
    assert result.structured_content["error"]["code"] == "validation_error"


async def test_get_customer_with_neither_identifier_returns_validation_error() -> None:
    result = await _call("crm.get_customer", {})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["customer"] is None
    assert result.structured_content["error"]["code"] == "validation_error"


async def test_get_customer_with_unknown_email_returns_not_found() -> None:
    result = await _call("crm.get_customer", {"customer_email": "nobody@nowhere.example"})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["customer"] is None
    assert result.structured_content["error"]["code"] == "not_found"


async def test_get_account_happy_path() -> None:
    result = await _call("crm.get_account", {"customer_id": "CUS-1001"})
    assert result.structured_content is not None
    account = result.structured_content["account"]
    assert account["account_id"] == "ACC-2201"
    assert account["customer_id"] == "CUS-1001"
    assert account["billing_contact_email"] == "billing@acme.example"
    assert account["payment_terms"] == "net30"
    assert account["currency"] == "USD"
    assert account["billing_address"] == {"country": "US", "region": "OR"}


async def test_get_account_unknown_customer_returns_not_found() -> None:
    result = await _call("crm.get_account", {"customer_id": "CUS-9999"})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["account"] is None
    assert result.structured_content["error"]["code"] == "not_found"


async def test_get_subscription_happy_path() -> None:
    result = await _call("crm.get_subscription", {"customer_id": "CUS-1001"})
    assert result.structured_content is not None
    sub = result.structured_content["subscription"]
    assert sub["subscription_id"] == "SUB-4410"
    assert sub["plan"] == "enterprise"
    assert sub["seat_count"] == 120
    assert sub["monthly_amount"] == 129.00
    assert sub["currency"] == "USD"
    assert sub["status"] == "active"


async def test_get_subscription_unknown_customer_returns_not_found() -> None:
    result = await _call("crm.get_subscription", {"customer_id": "CUS-9999"})
    assert result.structured_content is not None
    assert result.structured_content["subscription"] is None
    assert result.structured_content["error"]["code"] == "not_found"


async def test_tool_schemas_expose_required_parameters() -> None:
    tools = {tool.name: tool for tool in await crm.server.list_tools()}
    assert set(tools) == {"crm.get_customer", "crm.get_account", "crm.get_subscription"}

    # get_customer's two identifiers are optional at the schema layer: the
    # exactly-one rule is a runtime validation returning a `validation_error`
    # result, which is richer than "missing field" and lets the agent recover.
    customer_schema = tools["crm.get_customer"].input_schema
    assert set(customer_schema["properties"]) == {"customer_email", "customer_id"}
    assert customer_schema.get("required", []) == []

    # get_account / get_subscription require customer_id at the protocol layer.
    assert tools["crm.get_account"].input_schema["required"] == ["customer_id"]
    assert tools["crm.get_subscription"].input_schema["required"] == ["customer_id"]


async def test_tools_declare_structured_output_schema() -> None:
    tools = {tool.name: tool for tool in await crm.server.list_tools()}
    for tool in tools.values():
        assert tool.output_schema is not None, f"{tool.name} declares no output schema"


def test_crm_server_lists_tools_over_stdio() -> None:
    """The one test that proves this is an MCP server, not a decorated function.

    Starts the server as a real subprocess and speaks the protocol to it over
    stdin/stdout: initialize, then tools/list. Everything else in this file
    dispatches in-process (notes S6) and cannot cover the transport.
    """
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _list() -> list[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_servers.crm.server"],
            cwd=str(_CRM_DIR.parents[1]),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            return [tool.name for tool in listed.tools]

    names = anyio.run(_list)
    assert set(names) == {"crm.get_customer", "crm.get_account", "crm.get_subscription"}


def test_seed_file_matches_documented_shape() -> None:
    """The contract quotes exact ids; guard the seed the demo depends on."""
    seed = json.loads((_CRM_DIR / "seed.json").read_text(encoding="utf-8"))
    assert seed["customers"][0]["customer_id"] == "CUS-1001"
    assert seed["customers"][0]["email"] == "billing@acme.example"
    assert len(seed["customers"]) == 10
