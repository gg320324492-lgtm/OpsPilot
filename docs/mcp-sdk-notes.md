# MCP SDK notes (v2.x)

Facts established by running the SDK, not by reading documentation. Recorded
because the installed SDK is **2.x**, where the API differs from the 1.x examples
that dominate every search result and every tutorial — so the version that gets
copied is the version that does not work.

Install line: `mcp>=1.2` in `pyproject.toml` resolves to 2.3.0 today.

---

## 1. `FastMCP` no longer exists

```python
# WRONG under 2.x -- fails at import, with a message naming this migration
from mcp.server.fastmcp import FastMCP

# RIGHT
from mcp.server.mcpserver import MCPServer
```

`mcp/server/fastmcp.py` still exists as a file, but importing it raises
`ModuleNotFoundError` with a redirect message. It is a signpost, not a shim.
That is worse than a clean removal for anyone copying an example, because the
file appears in directory listings and looks importable.

## 2. Attribute names are Python-style, not JSON-style

The 1.x-derived examples use the wire names. Under 2.x the Python attributes are
snake_case:

| Wire / 1.x name | 2.x Python attribute |
|---|---|
| `result.isError` | `result.is_error` |
| `result.structuredContent` | `result.structured_content` |
| `tool.inputSchema` | `tool.input_schema` |

Accessing the wrong one raises `AttributeError` on the Pydantic model, which is
a confusing failure for a name that is correct in every document.

## 3. `structured_output=True` requires a Pydantic return type

A tool returning a bare `dict` with `structured_output=True` fails **at
registration time** (not at call time):

```
InvalidSignature: Function get_thing: return type <class 'dict'> is not
serializable for structured output
```

Declaring a Pydantic model as the return annotation works and produces
`structured_content` as a real object. Without the flag, a `dict` return is
serialised into `TextContent` as a JSON *string* — parseable, but the caller has
to know to parse it.

**This is a benefit, not an obstacle.** Tool results are part of the contract the
agent reasons over, and a schema for them is what makes the contract testable.
Every tool in all three servers declares a Pydantic return model.

## 4. Input schemas are generated from the signature, with required-ness

A parameter with no default is marked `required` in the generated JSON Schema:

```
issue_refund: params=['amount', 'idempotency_key', 'transaction_id']
              required=['transaction_id', 'amount', 'idempotency_key']
```

So `billing.issue_refund`'s mandatory `idempotency_key` is enforced **at the
protocol layer** — a caller omitting it is rejected before any of our code runs.
That is one of the five gates in `docs/tool-permissions.md` §3 satisfied by the
transport rather than by a hand-written check, which is the better place for it.

## 5. A raising tool becomes an opaque transport error

```python
@server.tool()
def failing(x: str) -> dict:
    raise ValueError("boom")

await server.call_tool("failing", {"x": "1"})
# -> UnexpectedToolError: Error executing tool failing
```

The exception type and message are **lost** — the caller sees that *some* tool
failed, not which error. An exception is not a result.

This is why `docs/mcp-contracts.md` §5 says errors are results, never
exceptions: a structured error object carrying a `code` (`not_found`,
`invalid_state`, `amount_exceeds_transaction`) is the only way the agent can
reason about *why* a tool failed, and the `already-refunded` scenario depends on
distinguishing that from a generic failure. Every tool therefore catches its own
errors and returns them in its result model.

## 6. In-process dispatch for tests

`await server.call_tool(name, arguments)` dispatches through the server's own
registry without a subprocess and without the stdio transport. The contract tests
use this, so they run in milliseconds and cannot flake on a pipe.

The one thing they cannot cover is the transport itself. `tests/integration/`
therefore also starts each server as a **real stdio subprocess** and lists its
tools over the wire — once per server, not per test. In-process for behaviour,
out-of-process for the claim that these are MCP servers rather than functions
with a decorator.

## 7. `call_tool` returns a union, not just `CallToolResult`

Under `mypy --strict` the call site needs narrowing:

```python
result = await server.call_tool(name, arguments)
# mypy: CallToolResult | InputRequiredResult
if not isinstance(result, CallToolResult):
    ...  # an elicitation request; these servers never raise one
```

`InputRequiredResult` is the elicitation path (the server asking the client for
more input). None of OpsPilot's tools elicit, but the return type says the union
exists, so the narrow is required rather than optional. Both M2 authors hit this
independently and it produced 33 type errors between them before it was found —
worth knowing before writing the first test.

## 8. The tool name is the function name unless `name=` is given

```python
@server.tool()                       # registers as "get_invoice"
def get_invoice(...) -> ...: ...

@server.tool(name="billing.get_invoice")   # registers as "billing.get_invoice"
def get_invoice(...) -> ...: ...
```

**The default is the bare function name.** This is the one place where the
obvious reading of the API silently produces a non-conforming system, and it bit
this project: the three M2 servers registered `get_invoice`, `get_customer`,
`create` and so on, while `docs/mcp-contracts.md` specifies `billing.get_invoice`
and `docs/tool-permissions.md`'s `TOOL_REGISTRY` is keyed on the prefixed names.

The consequence was not cosmetic. Gate 2 of the permission model is a registry
lookup, so a proposal naming `billing.issue_refund` would have failed to resolve
against a server that only knows `issue_refund` — every high-risk call rejected
for the wrong reason, and the agent unable to see it.

What made it survive two passing test suites: the tests called the servers with
the same bare names the servers registered, so they agreed with each other and
disagreed with the specification. A test suite written from the implementation
cannot catch a divergence between the implementation and the contract. That is
the argument for `tests/integration/test_mcp_contract_names.py`, which compares
the *registered* names against the contract document rather than against the
code's own idea of them.
