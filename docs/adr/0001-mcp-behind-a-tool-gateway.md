# ADR-0001 — The MCP transport sits behind a `ToolGateway` port

**Status:** Accepted (Phase 1)

## Context

OpsPilot calls enterprise tools over MCP. The straightforward implementation is
for the agent to hold an MCP client and call it:

```python
result = await self.mcp_client.call_tool("billing.issue_refund", args)
```

That works, and it is what most MCP examples do. It also puts the transport, the
tool-name strings, the error shapes and the permission question in the same
place as the agent's control flow — which means every future change to any of
those is a change to the agent.

The specific pressure: Phase 2 is expected to add REST-backed tools for systems
without MCP servers, and the permission gate must apply identically to both. If
MCP is the interface, REST tools become a special case in the agent.

## Decision

Define a port and hide MCP behind it.

```python
# src/opspilot/ports/tool_gateway.py
class ToolGateway(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
```

`MCPToolGateway` is the Phase 1 adapter. `agents/` and `domain/` import the
Protocol only; nothing above the adapter imports the MCP SDK.

## Consequences

**Good.** The permission gate is written once, against the port, so a future
`RESTToolGateway` inherits it unchanged. Tests use a `StubGateway` that returns
canned results with no subprocess and no protocol, which makes the agent and
security suites fast and hermetic. A change to MCP SDK versions is confined to
one file.

**Cost.** One more indirection, and the port must be designed before either
implementation exists, so it may be wrong in ways only the second implementation
reveals. Mitigated by keeping the port to two methods with plain-dict arguments —
the smallest surface that can carry a tool call.

**Also decided by this.** The port takes and returns plain dicts rather than
typed models per tool. Typed models would be nicer for the caller but would force
the gateway to know every tool's schema, which is the registry's job. Schema
validation happens at the gate (`domain/tools.py`), not in the transport.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Agent holds the MCP client directly | Couples control flow to transport; makes the gate MCP-specific; Phase 2's REST tools become a special case. |
| LangChain's tool abstraction as the port | Its interface is shaped by its own executor model. Depending on it makes the permission gate a LangChain concept, and ADR-0002 already puts LangGraph at arm's length for the same reason. |
| One gateway class with an `if transport == 'mcp'` branch | A branch is not an abstraction. The second transport would edit the first one's code. |
