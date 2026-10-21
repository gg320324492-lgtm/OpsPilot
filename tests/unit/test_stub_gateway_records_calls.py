"""The stub gateway must record the calls it was asked to make.

## Why this file exists

``StubGateway`` is the ``ToolGateway`` a unit test uses to assert *which*
tools the agent proposed, without standing up an MCP server. Its docstring
said its ``call_tool`` "records every call so a test can assert that a
rejected proposal was never dispatched".

It did not. ``call_tool`` raised ``NotImplementedError`` and never touched
``self.calls`` -- so a test that asserted on ``calls`` being empty would have
passed for the wrong reason: nothing had been dispatched because nothing
could be. The assertion was vacuous, and vacuous assertions are what a
security test is not allowed to be.

## What this file asserts

1. **A dispatched call is recorded**, with its name and its arguments.
2. **The recorded arguments are a copy**, so a caller that mutates the dict
   afterwards cannot change what the record says.
3. **A registered answer comes back**, and an unregistered tool gets a
   successful empty result rather than an exception.
"""

from __future__ import annotations

from opspilot.adapters.tools.stub_gateway import StubGateway
from opspilot.domain.tools import Permission, ToolSpec
from opspilot.ports.tool_gateway import ToolResult


async def test_a_dispatched_call_is_recorded() -> None:
    """The record is the whole point of the stub."""
    gateway = StubGateway(
        tools=[ToolSpec(name="crm.get_customer", permission=Permission.READ, server="crm")]
    )

    await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert gateway.calls == [("crm.get_customer", {"customer_id": "CUS-1001"})]


async def test_the_recorded_arguments_are_a_copy() -> None:
    """A caller that mutates its dict afterwards cannot rewrite the record."""
    gateway = StubGateway()
    arguments = {"transaction_id": "TX-88219"}

    await gateway.call_tool("billing.issue_refund", arguments)
    arguments["transaction_id"] = "TX-00000"

    assert gateway.calls == [("billing.issue_refund", {"transaction_id": "TX-88219"})]


async def test_a_registered_answer_comes_back_and_an_unregistered_one_succeeds() -> None:
    """The canned answer is returned; a tool with none gets an empty success."""
    canned = ToolResult(tool_name="crm.get_customer", ok=False, error="not_found")
    gateway = StubGateway(results={"crm.get_customer": canned})

    refused = await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-NOPE"})
    assert refused is canned

    other = await gateway.call_tool("billing.get_invoice", {"invoice_id": "INV-1"})
    assert other.ok is True
    assert other.result == {}
    assert other.error is None
    assert [name for name, _ in gateway.calls] == [
        "crm.get_customer",
        "billing.get_invoice",
    ]
