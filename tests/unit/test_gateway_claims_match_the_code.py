"""The tool adapters' own promises, checked against what the code does.

## Why this file exists

The tool adapters make a lot of promises in their docstrings: what an exception
becomes, which code a refusal arrives under, what a queued call does when the
server is shut down, what shape a retriever has. Several of those promises
were **not** what the code did, and the drift was always in the *describing*
direction -- the docstring said something kinder than the truth, so a reader who
trusted it built the wrong thing on top of it.

This file holds the guards for the promises that had drifted, so the
docstring and the code cannot drift apart again. Each test names one property,
and each property is one the surrounding docstring now states.

What is covered:

1. **Every exception a server raises becomes a result, never a raise.** This is
   the layer's "errors are results" contract, and it is also why
   ``mcp_gateway.call_tool``'s ``except Exception`` arm is as wide as it is:
   the catch cannot tell a transport fault from a programming error, so a bug in
   the adapter is reported as ``mcp_unavailable`` rather than propagating. The
   comment in the adapter used to claim the opposite.
2. **A protocol-layer rejection becomes a ``validation_error`` result** -- the
   in-process path, where the SDK raises ``ToolError`` before the tool body
   runs.
3. **An error result with no payload of its own becomes a ``tool_error``** --
   the stdio path, where the same rejection comes back as a plain
   ``CallToolResult`` with ``is_error`` set. These are *two different codes for
   the same mistake*, and ``_error_text``'s docstring says so.
4. **A structured refusal keeps the code the server put at the top level** --
   the shape every server in ``mcp_servers/`` uses, and the one the gateway
   reads.
5. **The shared search takes ``top_k`` by keyword**, so a ``functools.partial``
   of it cannot be called the way the knowledge tool calls its retriever. The
   adapter's docstring used to say that a partial would do.
6. **A call queued before the stop sentinel is answered, not failed.** The
   queue is FIFO, so the sentinel goes in *behind* the call; the adapter's
   ``aclose`` docstring used to say the opposite.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, cast

import pytest
from mcp import ClientSession
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from opspilot.adapters.retrieval.embeddings import Embedder
from opspilot.adapters.retrieval.search import retrieve
from opspilot.adapters.tools.mcp_gateway import MCPToolGateway
from opspilot.adapters.tools.mcp_stdio import StdioServerProcess, _Call
from opspilot.ports.vector_store import VectorStore

_TOOL_NAME = "crm.get_customer"
_ARGUMENTS = {"customer_id": "CUS-1001"}


class _Server:
    """A server that answers (or raises) with whatever it was handed.

    Duck-typed to the gateway's ``DispatchableServer``: all the gateway needs is
    a ``call_tool``. Using a double rather than a real MCP server is what makes
    these tests about the *gateway's* mapping rather than about a server's
    behaviour -- a real server could not produce an ``AttributeError`` on
    demand, and one of the cases below is precisely an ``AttributeError``.
    """

    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _gateway(outcome: Any) -> MCPToolGateway:
    """A gateway whose ``crm`` server answers with ``outcome``."""
    return MCPToolGateway(servers={"crm": _Server(outcome)})


# ---------------------------------------------------------------------------
# 1. Every exception is a result, never a raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(AttributeError("a bug in the adapter"), id="attribute-error"),
        pytest.param(TypeError("a bug in the adapter"), id="type-error"),
        pytest.param(NameError("a bug in the adapter"), id="name-error"),
        pytest.param(KeyError("a bug in the adapter"), id="key-error"),
        pytest.param(RuntimeError("the pipe closed"), id="transport-fault"),
    ],
)
async def test_a_server_that_raises_becomes_a_result_and_never_an_exception(
    raised: BaseException,
) -> None:
    """Any exception a server raises is reported as ``mcp_unavailable``.

    The layer's contract is "errors are results, never exceptions": a raise
    would become an opaque transport error in the runtime, which has no handler
    for one. The catch is deliberately wide, and **the price of that is that a
    programming error in the adapter is reported the same way** -- as a failed
    tool call the caller can only diagnose from the logs.

    An earlier version of the comment in ``mcp_gateway.call_tool`` claimed the
    opposite: that ``AttributeError``, ``TypeError``, ``NameError`` and
    ``KeyError`` "are left to propagate rather than reported as a tool the user
    asked for having failed". They are not: every ``Exception`` subclass lands
    in that arm, and this test is what says so.
    """
    result = await _gateway(raised).call_tool(_TOOL_NAME, _ARGUMENTS)

    assert result.ok is False
    assert result.error == "mcp_unavailable"
    assert result.result is not None
    assert result.result["code"] == "mcp_unavailable"
    assert str(raised) in str(result.result["message"]), (
        "the message has to survive: it is the only thing that tells an operator "
        "what actually went wrong"
    )


# ---------------------------------------------------------------------------
# 2 and 3. The two codes a schema-invalid call arrives under
# ---------------------------------------------------------------------------


async def test_a_protocol_layer_rejection_is_a_validation_error_result() -> None:
    """The in-process path: the SDK raises ``ToolError``, and it is a result.

    ``ToolError`` is the SDK's answer to a call whose arguments failed the
    tool's generated JSON Schema -- a missing ``idempotency_key``, say. Gate 1
    is enforced by the transport here, and the gateway turns the raise back
    into the result shape the rest of the layer speaks.
    """
    result = await _gateway(ToolError("idempotency_key: Field required")).call_tool(
        _TOOL_NAME, _ARGUMENTS
    )

    assert result.ok is False
    assert result.error == "validation_error"
    assert result.result is not None
    assert result.result["code"] == "validation_error"
    assert "idempotency_key" in str(result.result["message"])


async def test_an_error_result_with_no_payload_is_a_tool_error() -> None:
    """The stdio path: the same rejection arrives under a *different* code.

    Over stdio a schema-invalid call comes back as a normal ``CallToolResult``
    with ``is_error`` set and no ``structured_content``, because the rejection
    happened in the child's transport rather than in ours. The gateway has no
    code to read, so it falls back to ``tool_error``.

    **The same mistake therefore lands under two different codes depending on
    the deployment's transport** -- ``validation_error`` in-process,
    ``tool_error`` over stdio. ``_error_text``'s docstring used to say the
    opposite, that reporting the second as a bare ``tool_error`` "would mean
    the same mistake got two different codes depending on a deployment's
    transport, which is precisely the kind of drift this layer exists to
    prevent". The code has always done the thing that paragraph warned
    against; this test is what says so, and the message is the one thing the
    two transports agree on.
    """
    result = await _gateway(
        CallToolResult(
            content=[TextContent(type="text", text="idempotency_key: Field required")],
            is_error=True,
        )
    ).call_tool(_TOOL_NAME, _ARGUMENTS)

    assert result.ok is False
    assert result.error == "tool_error"
    assert result.result is not None
    assert "idempotency_key" in str(result.result["message"]), (
        "the message is what names the offending argument, and it is the only "
        "part of the failure the two transports agree on"
    )


# ---------------------------------------------------------------------------
# 4. A structured refusal keeps the server's own code
# ---------------------------------------------------------------------------


async def test_a_structured_refusal_keeps_the_code_the_server_wrote() -> None:
    """The top-level ``code`` is the field the gateway reads.

    Every server in ``mcp_servers/`` puts its refusal code at the top level of
    the result (``docs/mcp-contracts.md`` §5.2), because a code nested one
    level down is a code the gateway never sees -- and a refusal that arrives
    as a *successful* call is what an audit trail exists to prevent.
    """
    result = await _gateway(
        CallToolResult(content=[], structured_content={"code": "not_found"})
    ).call_tool(_TOOL_NAME, _ARGUMENTS)

    assert result.ok is False
    assert result.error == "not_found"
    assert result.result is not None
    assert result.result["code"] == "not_found"


# ---------------------------------------------------------------------------
# 5. The retriever's shape
# ---------------------------------------------------------------------------


def test_the_shared_search_takes_top_k_by_keyword() -> None:
    """``retrieve``'s arguments are keyword-only, so a partial will not do.

    The knowledge tool calls its retriever as ``retriever(query, top_k)`` --
    two positional arguments. ``opspilot.adapters.retrieval.search.retrieve``
    takes ``embedder``, ``store``, ``top_k`` and ``min_score`` as *keyword-only*
    arguments, so a ``functools.partial`` with the first two bound still
    refuses a second positional one. The adapter's docstring used to say that
    such a partial satisfied the shape; it does not, and a deployment that
    followed it would have answered every search with ``retrieval_failed``.

    What does satisfy the shape is a closure that forwards ``top_k`` by
    keyword, which is what ``adapters/wiring.py`` builds.
    """
    parameter = inspect.signature(retrieve).parameters["top_k"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        "retrieve() now takes top_k positionally, so a functools.partial of it "
        "would satisfy the knowledge tool's retriever shape again -- and the "
        "adapter's docstring has to be revisited when that happens"
    )

    partial = functools.partial(
        retrieve,
        embedder=cast("Embedder", None),
        store=cast("VectorStore", None),
    )
    with pytest.raises(TypeError, match="positional argument"):
        partial("a query", 5)  # type: ignore[call-arg, arg-type, unused-coroutine]


# ---------------------------------------------------------------------------
# 6. The stop sentinel goes in behind the calls already queued
# ---------------------------------------------------------------------------


class _FakeSession:
    """A client session that answers without a pipe behind it.

    The property under test is the *order* the owner task reads its queue in,
    not the transport, so the session is a double.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return "answered"


async def test_a_call_queued_before_the_stop_sentinel_is_answered_not_failed() -> None:
    """A queued call is answered; the sentinel only stops what comes after it.

    ``StdioServerProcess.aclose`` appends the stop sentinel to the queue, and
    the queue is FIFO: the sentinel therefore lands *behind* a call that was
    already queued, and the owner task dispatches that call first. The caller
    gets the server's real answer.

    The adapter's ``aclose`` docstring used to say the opposite -- that "a call
    that was already queued is failed rather than abandoned", with the sentinel
    as "a stop *order*, not a promise to answer what is behind it". What is
    behind the sentinel is never read; what is *in front of* it is answered.
    """
    handle = StdioServerProcess("crm", "python -m mcp_servers.crm.server")
    session = _FakeSession()
    # The owner task, with the pipe replaced by the double above.
    handle._task = asyncio.create_task(handle._pump(cast("ClientSession", session)))

    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    await handle._queue.put(_Call(name=_TOOL_NAME, arguments=dict(_ARGUMENTS), future=future))

    await handle.aclose()

    assert session.calls == [(_TOOL_NAME, _ARGUMENTS)], (
        "the queued call was not dispatched before the sentinel; the queue is "
        "FIFO, so a call queued first is answered and the sentinel only stops "
        "what is queued after it"
    )
    assert future.done(), "the queued call's future was never completed"
    assert future.exception() is None, (
        "the queued call was failed rather than answered; the call was in front "
        "of the sentinel, so the server's real answer is what it gets"
    )
    assert future.result() == "answered"
