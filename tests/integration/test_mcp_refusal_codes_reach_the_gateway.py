"""A server's refusal code must survive the trip to the runtime.

## Why this file exists

``docs/mcp-contracts.md`` §5.2 says it plainly: each server returns the
refusal code as a **top-level** ``code`` on the result, and the gateway reads
it there and maps it to ``ToolCall.error``. The agent can then reason about
*why* a tool failed, which is what makes the "already refunded → do not refund
again" scenario work.

The gateway maps the code by reading a **top-level** ``code`` in the server's
payload (``src/opspilot/adapters/tools/mcp_gateway.py``). That is the
convention the ``billing`` and ``issues`` servers follow, and the ``crm``
server did not: it nested the code one level down, inside an ``error`` object.

The consequence was not a cosmetic difference in a JSON shape. A CRM refusal --
``validation_error`` or ``not_found`` alike -- arrived at the runtime as a
**successful** call: ``ToolCall.status='executed'``, ``error=NULL``, and a
``tool_executed`` audit event. The agent could still read the code out of the
observation payload, so it was not blind to the failure; but the ledger, the
``tool_calls.error`` column and the failure event all said the lookup
*succeeded*. A failed lookup recorded as a success is exactly what an audit
trail exists to prevent, and it happened because one server put a field one
level deeper than another.

Nothing caught it. The CRM server's own tests asserted the *nested* shape
(``structured_content["error"]["code"]``), the gateway's own tests asserted the
top-level shape for ``billing.issue_refund``, and no test drove a CRM refusal
through the gateway at all. Two suites, two conventions, and the seam between
them untested.

## What this file asserts

1. **Every tool of every server declares a top-level ``code``** in its output
   schema, and so does every tool whose schema declares an ``error`` object.
   The second half is the direction that matters: a server that returns a
   structured error has to put the machine-readable code where the gateway
   reads it.
2. **A refusal from each of the three servers arrives as a failure** --
   ``ok=False`` with the code in ``ToolResult.error`` -- exercised through the
   real gateway and the real servers.

The first is a shape invariant that generalises to a server written later; the
second is the end-to-end property the contract promises.

What it deliberately does **not** assert: that every tool *can* fail, or that
the failure modes are the same across servers. It asserts the mechanism -- a
refusal that happens is a refusal that is recorded -- not the catalogue.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers

from tests._mcp_codes import EXPECTED_CODES


def _enum_of(schema: object) -> list[str]:
    """The enum values a JSON-schema property allows.

    Pydantic renders a ``Literal[...] | None`` property as an ``anyOf`` of the
    enum and a null type, so both shapes are read.
    """
    if not isinstance(schema, dict):
        return []
    candidates: list[object] = [schema]
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        candidates.extend(any_of)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        values = candidate.get("enum")
        if isinstance(values, list):
            return [str(value) for value in values]
        # Pydantic renders a *single*-value Literal as ``const`` and a
        # multi-value one as ``enum``.
        const = candidate.get("const")
        if const is not None:
            return [str(const)]
    return []


#: The servers the runtime dispatches to, and the tool that demonstrates a
#: refusal for each. One per server on purpose: the defect this file is about
#: was one server's convention diverging from the other two's, so the coverage
#: that matters is *every* server, not every tool of every server.
_REFUSALS: tuple[tuple[str, str, dict[str, object], str], ...] = (
    ("billing", "billing.get_invoice", {"invoice_id": "INV-NOT-THERE"}, "not_found"),
    ("crm", "crm.get_customer", {"customer_id": "CUS-NOT-THERE"}, "not_found"),
    ("issues", "issues.create", {"title": "", "description": "d"}, "validation_error"),
)


@pytest.fixture
def servers(tmp_path: Path) -> dict[str, MCPServer]:
    """The three real MCP servers, each with its own store directory."""
    return build_in_process_servers(tmp_path)


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[MCPToolGateway]:
    """A gateway over the three real servers, in-process."""
    yield MCPToolGateway(servers=build_in_process_servers(tmp_path))


async def test_every_tool_declares_exactly_the_codes_it_can_return(
    servers: dict[str, MCPServer],
) -> None:
    """A tool's schema says exactly which failures it can have.

    Two directions, and both matter:

    * **A tool that can fail declares a top-level ``code``.** The gateway reads
      the code from the top level of the payload
      (``src/opspilot/adapters/tools/mcp_gateway.py``), so a tool whose schema
      does not declare one there cannot report a refusal the gateway will see.
    * **A tool declares no code it cannot produce.** A code in a tool's
      generated schema is a promise to the agent about a failure it will never
      see, and the shared per-server literal made exactly that promise: the
      ``billing`` union of four codes was on all three tools, two of which can
      produce one of them between them.

    A tool with **no** failure path -- ``billing.list_transactions`` and
    ``issues.search``, where a query that matches nothing is an empty result --
    declares no ``code`` at all, and this says so rather than leaving it to be
    inferred.
    """
    wrong: list[str] = []
    for server_name, server in servers.items():
        for tool in await server.list_tools():
            expected = EXPECTED_CODES.get(tool.name)
            if expected is None:
                wrong.append(f"{server_name}/{tool.name}: not in the expected table")
                continue
            properties = (tool.output_schema or {}).get("properties", {})
            if "code" not in properties:
                if expected:
                    wrong.append(
                        f"{server_name}/{tool.name}: can return {sorted(expected)} but "
                        "declares no top-level `code`"
                    )
                continue
            if not expected:
                wrong.append(
                    f"{server_name}/{tool.name}: declares a `code` but cannot fail"
                )
                continue
            declared = set(_enum_of(properties["code"]))
            if declared != set(expected):
                wrong.append(
                    f"{server_name}/{tool.name}: declares {sorted(declared)}, "
                    f"can return {sorted(expected)}"
                )
    assert not wrong, (
        "these tools' schemas do not match the failures they can actually "
        f"produce:\n  " + "\n  ".join(wrong) + "\n\nA code in a tool's schema is "
        "a promise to the agent about a failure it will never see."
    )


async def test_a_tool_with_an_error_object_also_declares_a_top_level_code(
    servers: dict[str, MCPServer],
) -> None:
    """A structured error has to be reachable by the gateway, not only by a reader.

    This is the direction that caught the ``crm`` server: it returned an
    ``error`` object carrying the code, and no top-level ``code`` -- so the
    refusal was a success to everything downstream of the gateway.
    """
    offenders: list[str] = []
    for server_name, server in servers.items():
        for tool in await server.list_tools():
            schema = tool.output_schema or {}
            properties = schema.get("properties", {})
            if "error" in properties and "code" not in properties:
                offenders.append(f"{server_name}/{tool.name}")
    assert not offenders, (
        f"these tools return a structured `error` object but no top-level `code`: "
        f"{offenders}. The code has to be where the gateway reads it, or the "
        "refusal is recorded as a successful call."
    )


@pytest.mark.parametrize(("server_name", "tool", "arguments", "expected_code"), _REFUSALS)
async def test_a_refusal_from_each_server_arrives_as_a_failure(
    gateway: MCPToolGateway,
    server_name: str,
    tool: str,
    arguments: dict[str, object],
    expected_code: str,
) -> None:
    """One refusal per server, driven through the real gateway.

    ``ok=False`` with the code in ``ToolResult.error`` is what the runtime
    branches on to record a failed tool call and a ``tool_failed`` audit event.
    A refusal that arrives as ``ok=True`` is a successful lookup in the ledger.
    """
    result = await gateway.call_tool(tool, dict(arguments))

    assert result.ok is False, (
        f"{server_name}'s refusal arrived as a successful call. The runtime "
        "records this as an executed tool call with no error, and the audit "
        "trail says the lookup worked."
    )
    assert result.error == expected_code, (
        f"{server_name}'s refusal carried {result.error!r} instead of "
        f"{expected_code!r}; the agent branches on the code to tell 'no such "
        "record' from 'you asked badly'."
    )
