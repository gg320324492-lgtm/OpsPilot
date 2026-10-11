"""The ``reasoning_content`` fallback, at the boundary where the defect lived.

Some OpenAI-compatible endpoints accept the ``json_schema`` envelope and answer
HTTP 200, but return the model's JSON in a separate ``reasoning_content`` field
and leave ``content`` as prose. Measured against MiniMax: ``content`` was
``'**Category:** Duplicate ... twice within 2 seconds'``, so
``_shared.parse_structured`` raised ``Invalid JSON: expected value at line 1
column 1`` and 0 of 20 classification eval cases could run. The endpoint was not
broken and the request was not wrong -- the payload was simply in the other
field, and nothing read it.

These tests pin four properties, in the order they matter:

- ``content`` still wins. The fallback is a fallback; the primary path must stay
  the one that succeeds for real OpenAI.
- ``reasoning_content`` rescues a reply whose ``content`` will not validate.
- It rescues nothing that fails the *schema*: a reply that ignores the requested
  shape still raises, which is what keeps this from becoming JSON salvage.
- Its absence changes nothing: the error the caller would have seen is the error
  the caller sees.

The provider is driven against a genuine ``AsyncOpenAI`` client whose transport
records the serialized request and serves a canned completion, so the SDK's own
response parsing -- which is where the undeclared field survives -- runs exactly
as it does in production. CI stays offline and no API key is involved.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from opspilot.adapters.models import _shared
from opspilot.adapters.models.openai_provider import OpenAIModelProvider
from opspilot.agents.schemas import ProposedAction, TicketClassification

_CLASSIFICATION_JSON = json.dumps(
    {"category": "billing_other", "confidence": 0.9, "rationale": "mentions an invoice"}
)
_PROPOSAL_JSON = json.dumps(
    {
        "tool_name": "search_knowledge",
        "arguments": {"query": "refund"},
        "reason": "r",
        "done": False,
    }
)

# The shape observed against MiniMax: prose in ``content``, the real JSON in the
# sibling field. ``content`` is Markdown here because that is what the eval run
# actually got, and Markdown is the case that fails loudly rather than quietly
# parsing into the wrong keys.
_MARKDOWN_CONTENT = "**Category:** Duplicate charge reported twice within 2 seconds"
_WRONG_KEYS_CONTENT = '{"classification":"billing issue"}'

# The reply each schema gets back, so the test is about *where* the payload was
# read from and not about one canned reply answering every request.
_REPLY_BY_SCHEMA_NAME: dict[str, str] = {
    "ProposedAction": _PROPOSAL_JSON,
    "TicketClassification": _CLASSIFICATION_JSON,
}


#: Builds the ``message`` dict to serve, from the schema name the request named.
_ReplyFor = Callable[[str], dict[str, Any]]


class _CannedTransport(httpx.AsyncBaseTransport):
    """Records the request body, then answers with a completion built by ``reply_for``.

    ``reply_for`` receives the schema name the request named and returns the
    message dict to serve, so one transport can put prose in ``content`` for a
    classification and valid JSON in ``content`` for a proposal.
    """

    def __init__(self, reply_for: _ReplyFor) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._reply_for = reply_for

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content.decode())
        self.bodies.append(sent)
        name = (sent.get("response_format", {}) or {}).get("json_schema", {}).get("name", "")
        message = self._reply_for(name)
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "test-model",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return httpx.Response(200, json=body)


def _provider_answering(
    monkeypatch: pytest.MonkeyPatch, transport: _CannedTransport
) -> OpenAIModelProvider:
    """A real provider whose SDK client records and answers instead of sending.

    Only the SDK *constructor* is replaced; the client is a genuine
    ``AsyncOpenAI``, so response parsing runs for real -- which matters, because
    ``reasoning_content`` is not a declared field and only survives the SDK as
    an extra attribute.
    """
    import openai

    real_async_openai = openai.AsyncOpenAI

    def _build(**kwargs: object) -> object:
        return real_async_openai(
            api_key=str(kwargs.get("api_key", "")),
            base_url="https://example.invalid/v1",
            # The SDK's stubs are generated against `httpx2`, while the installed
            # httpx is `httpx1`; they are the same runtime object, and the stub
            # alias is an artifact of the SDK's codegen.
            http_client=httpx.AsyncClient(transport=transport),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", _build)
    return OpenAIModelProvider(api_key="test-key", model_name="test-model")


def _reply_with_valid_content(name: str) -> dict[str, Any]:
    """A conforming reply in ``content``, plus a ``reasoning_content`` that must be ignored."""
    return {
        "role": "assistant",
        "content": _REPLY_BY_SCHEMA_NAME.get(name, _PROPOSAL_JSON),
        # Deliberately valid JSON for the *wrong* shape: if this were read, the
        # primary-wins test would fail loudly rather than pass for the wrong reason.
        "reasoning_content": '{"reasoning":"the primary field is authoritative"}',
    }


# -- the primary path is unchanged --------------------------------------------


async def test_content_that_parses_wins_and_reasoning_content_is_never_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real OpenAI is unaffected: ``content`` alone decides the result.

    The fallback must not become a preference. If ``reasoning_content`` were
    consulted first, a stale or divergent value there would silently override
    the field the API contract says carries the answer.
    """
    transport = _CannedTransport(_reply_with_valid_content)
    provider = _provider_answering(monkeypatch, transport)

    response = await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )

    assert response.value.category == "billing_other"
    assert response.value.confidence == 0.9
    assert len(transport.bodies) == 1, "a working primary path must not cost a second request"


# -- the fallback rescues the observed MiniMax shape ---------------------------


async def test_reasoning_content_is_read_when_content_is_unusable_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdown in ``content`` plus the real JSON in ``reasoning_content``.

    This is the observed failure verbatim: the endpoint answered 200, the
    request was well-formed, and the classification came back in the sibling
    field. Every one of the twenty eval cases was lost this way.
    """
    transport = _CannedTransport(
        lambda _name: {
            "role": "assistant",
            "content": _MARKDOWN_CONTENT,
            "reasoning_content": _CLASSIFICATION_JSON,
        }
    )
    provider = _provider_answering(monkeypatch, transport)

    response = await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )

    assert response.value.category == "billing_other"
    assert response.value.rationale == "mentions an invoice"


async def test_reasoning_content_is_read_when_content_is_a_json_object_with_wrong_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON in ``content`` is not automatically the right answer.

    The other observed shape: ``content`` parsed as JSON, so no decode error,
    but under keys the schema does not have. The fallback has to run on *any*
    validation failure, not only on a JSON decode failure.
    """
    transport = _CannedTransport(
        lambda _name: {
            "role": "assistant",
            "content": _WRONG_KEYS_CONTENT,
            "reasoning_content": _CLASSIFICATION_JSON,
        }
    )
    provider = _provider_answering(monkeypatch, transport)

    response = await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )

    assert response.value.category == "billing_other"


async def test_the_tool_path_inherits_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choose_tool`` gets the fallback without spelling a second one.

    Tool selection runs every iteration of every run, so an adapter that fixes
    only the classification call would fail the agent at the first planning step
    instead. Both paths parse through ``_parse_reply``.
    """
    transport = _CannedTransport(
        lambda _name: {
            "role": "assistant",
            "content": "I would suggest a search.",
            "reasoning_content": _PROPOSAL_JSON,
        }
    )
    provider = _provider_answering(monkeypatch, transport)

    proposal = await provider.choose_tool(
        system="s", prompt="what next?", available_tools=["search_knowledge"]
    )

    assert proposal["tool_name"] == "search_knowledge"
    assert proposal["arguments"] == {"query": "refund"}


# -- the fallback validates too, and is optional ------------------------------


async def test_a_fallback_payload_that_ignores_the_schema_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving the JSON does not make it acceptable: it is still validated.

    The failure mode this rules out is a general "salvage something that parses"
    heuristic. A MiniMax-style reply that answers in the wrong shape must fail
    the run loudly (``docs/agent-state-machine.md`` §3), not reach gate 1 as a
    half-formed value.
    """
    transport = _CannedTransport(
        lambda _name: {
            "role": "assistant",
            "content": _MARKDOWN_CONTENT,
            "reasoning_content": '{"classification": "billing issue"}',
        }
    )
    provider = _provider_answering(monkeypatch, transport)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)
    assert excinfo.value.schema_name == "TicketClassification"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param({"role": "assistant", "content": _MARKDOWN_CONTENT}, id="absent"),
        pytest.param(
            {"role": "assistant", "content": _MARKDOWN_CONTENT, "reasoning_content": ""},
            id="empty",
        ),
        pytest.param(
            {"role": "assistant", "content": _MARKDOWN_CONTENT, "reasoning_content": None},
            id="null",
        ),
    ],
)
async def test_an_absent_or_empty_fallback_leaves_the_original_error(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any]
) -> None:
    """Without a usable ``reasoning_content`` the failure is exactly what it was.

    The raised error names the schema and the detail comes from parsing
    ``content``. That is the error a real OpenAI deployment has always got, and
    the fallback must not replace it with one about a field the caller never
    asked for.
    """
    transport = _CannedTransport(lambda _name: message)
    provider = _provider_answering(monkeypatch, transport)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)

    assert excinfo.value.schema_name == "TicketClassification"
    assert "not valid JSON" in excinfo.value.detail
    assert "reasoning" not in excinfo.value.detail


async def test_an_absent_fallback_does_not_save_the_tool_path_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choose_tool`` with prose-only content still raises, as it always has."""
    transport = _CannedTransport(
        lambda _name: {"role": "assistant", "content": "I would suggest a search."}
    )
    provider = _provider_answering(monkeypatch, transport)

    with pytest.raises(_shared.StructuredOutputError) as excinfo:
        await provider.choose_tool(system="s", prompt="p", available_tools=["search_knowledge"])
    assert excinfo.value.schema_name == ProposedAction.__name__


# -- the request itself did not change ----------------------------------------


async def test_the_request_is_unchanged_by_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wire body is byte-identical to what handing the class produced.

    The adapter pre-expands the Pydantic class so the SDK stops parsing
    ``content`` eagerly (see ``_response_format``). That is only acceptable if
    what goes out is exactly what went out before -- otherwise the fix would
    have bought one compatible endpoint's support by changing the request every
    OpenAI deployment sends. The reference is the SDK's own expansion of the
    class, not a literal, so the check keeps holding if the envelope changes
    shape in a future SDK.
    """
    import openai
    from openai.lib._parsing import type_to_response_format_param
    from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam

    # Typed as the SDK declares it: the reference call must build the same list
    # the adapter does, and only the ``response_format`` is meant to differ.
    messages: list[ChatCompletionSystemMessageParam | ChatCompletionUserMessageParam] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "classify this"},
    ]

    # Built before the patch below, so the reference client is a genuine
    # ``AsyncOpenAI`` rather than the recording stand-in.
    class_transport = _CannedTransport(_reply_with_valid_content)
    reference_client = openai.AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=class_transport),  # type: ignore[arg-type]
    )

    adapter_transport = _CannedTransport(_reply_with_valid_content)
    provider = _provider_answering(monkeypatch, adapter_transport)
    await provider.generate_structured(
        system="s", prompt="classify this", schema=TicketClassification
    )
    sent_by_adapter = adapter_transport.bodies[0]

    await reference_client.beta.chat.completions.parse(
        model="test-model",
        messages=messages,
        response_format=TicketClassification,
    )
    sent_by_class = class_transport.bodies[0]

    assert len(adapter_transport.bodies) == 1
    assert len(class_transport.bodies) == 1
    assert sent_by_adapter == sent_by_class
    assert sent_by_adapter["response_format"] == type_to_response_format_param(TicketClassification)


def test_the_fallback_reads_exactly_one_extra_field() -> None:
    """The recovery is one documented field, not a search over the message.

    Asserted against the source because the failure being guarded is a
    *generalisation*: a regex over ``content``, a retry, or a sweep of whatever
    other attributes the message happens to carry would all pass a behavioural
    test while quietly turning a narrow compatibility shim into JSON salvage.
    The docstring is excluded -- it *names* the things the code must not do.
    """
    import ast
    import inspect
    import textwrap

    from opspilot.adapters.models import openai_provider

    source = inspect.getsource(openai_provider._parse_reply)
    tree = ast.parse(textwrap.dedent(source))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    assert ast.get_docstring(function) is not None
    # The docstring is an Expr statement and is dropped; the rest is unparsed
    # (quotes normalised to ' by ast.unparse, hence the single-quoted literal).
    statements = [node for node in function.body if not isinstance(node, ast.Expr | ast.Pass)]
    body = "\n".join(line for node in statements for line in ast.unparse(node).splitlines())

    assert "getattr(message, 'reasoning_content', None)" in body
    for smuggled in ("re.", "json.", "regex", "while ", "for ", "tool_calls", "refusal"):
        assert smuggled not in body, (
            f"_parse_reply mentions {smuggled!r}. The fallback reads exactly one "
            "field; a search over the message for anything that parses is not "
            "what this is for."
        )
