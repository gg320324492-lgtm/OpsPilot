"""Boundary tests: what request does the OpenAI provider actually put on the wire?

``choose_tool`` and ``generate_structured`` both request structured output, but
they used to spell the request two different ways -- one passing a Pydantic
class, the other a bare JSON Schema dict. The SDK wraps the first and forwards
the second unchanged, so ``choose_tool`` sent ``response_format.type="object"``
and every tool-selection call came back 400 from any OpenAI-compatible endpoint.

These tests drive the real ``OpenAIModelProvider`` methods against an SDK client
whose transport records the serialized HTTP request. Asserting on the recorded
body means the check sits at the boundary the defect lived at: it sees what the
provider caused to be sent, not what the provider meant.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from opspilot.adapters.models.openai_provider import OpenAIModelProvider
from opspilot.agents.schemas import ProposedAction, TicketClassification

# The only three values the API accepts for response_format.type.
LEGAL_RESPONSE_FORMAT_TYPES = frozenset({"text", "json_object", "json_schema"})

_PROPOSAL_JSON = json.dumps(
    {
        "tool_name": "search_knowledge",
        "arguments": {"query": "refund"},
        "reason": "r",
        "done": False,
    }
)

# A minimal well-formed chat completion. The SDK requires these fields to parse
# the envelope; the content must satisfy the schema the request asked for.
_COMPLETION_BODY: dict[str, Any] = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": _PROPOSAL_JSON},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

_CLASSIFICATION_JSON = json.dumps(
    {"category": "billing_other", "confidence": 0.9, "rationale": "mentions an invoice"}
)

# The SDK parses the reply against whatever schema the request named, so a
# canned proposal cannot answer a classification request. Serving the schema the
# request actually asked for keeps these tests about the request, not the reply.
_REPLY_BY_SCHEMA_NAME: dict[str, str] = {
    "ProposedAction": _PROPOSAL_JSON,
    "TicketClassification": _CLASSIFICATION_JSON,
}


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Records the request body the SDK serialized, then answers it."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content.decode())
        self.bodies.append(sent)
        # A bare schema (the old buggy shape) still names no envelope, so fall
        # back to the proposal -- the point of these tests is the request, and
        # the reply only has to be parseable.
        name = (sent.get("response_format", {}) or {}).get("json_schema", {}).get("name", "")
        body = json.loads(json.dumps(_COMPLETION_BODY))
        body["choices"][0]["message"]["content"] = _REPLY_BY_SCHEMA_NAME.get(name, _PROPOSAL_JSON)
        return httpx.Response(200, json=body)


def _provider_recording_into(
    monkeypatch: pytest.MonkeyPatch, transport: _RecordingTransport
) -> OpenAIModelProvider:
    """A real provider whose SDK client records instead of sending.

    Only the SDK *constructor* is replaced; the returned client is a genuine
    ``AsyncOpenAI``, so serialization, response parsing and error handling all
    run exactly as they do in production. CI stays offline.
    """
    import openai

    real_async_openai = openai.AsyncOpenAI

    def _build(**kwargs: object) -> object:
        return real_async_openai(
            api_key=str(kwargs.get("api_key", "")),
            base_url=str(kwargs.get("base_url") or "https://example.invalid/v1"),
            # The SDK's stubs are generated against `httpx2`, while the installed
            # httpx is `httpx1`; they are the same runtime object, and the stub
            # alias is an artifact of the SDK's codegen.
            http_client=httpx.AsyncClient(transport=transport),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", _build)
    return OpenAIModelProvider(api_key="test-key", model_name="test-model")


def _assert_structured_output_request(sent: dict[str, Any]) -> None:
    """Assert a request carries a structured-output ``response_format`` the API accepts.

    A bare JSON Schema passed as ``response_format`` fails the first assertion
    with the offending type in the message, which is exactly the 400 the live
    provider used to return.
    """
    response_format = sent.get("response_format")

    assert response_format is not None, "request sent no response_format at all"
    assert isinstance(response_format, dict), (
        f"response_format must be an object, got {type(response_format).__name__}: "
        f"{response_format!r}"
    )

    actual_type = response_format.get("type")
    assert actual_type in LEGAL_RESPONSE_FORMAT_TYPES, (
        f"response_format.type={actual_type!r} is rejected by the API; expected one of "
        f"{sorted(LEGAL_RESPONSE_FORMAT_TYPES)}. A bare JSON Schema reaches the wire "
        f"unwrapped, with type='object' -- wrap it as "
        f"{{'type': 'json_schema', 'json_schema': {{'name': ..., 'schema': ...}}}} "
        f"or hand the SDK the Pydantic class and let it wrap."
    )

    if actual_type == "json_schema":
        inner = response_format["json_schema"]
        for key in ("name", "schema", "strict"):
            assert key in inner, f"json_schema is missing {key!r}; got keys {sorted(inner)}"
        assert isinstance(inner["schema"], dict), "json_schema.schema must be the schema object"


async def test_choose_tool_sends_a_response_format_the_api_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choose_tool`` -- the planning step, run every iteration of every run.

    Regression test for the 400: this used to serialize
    ``response_format.type="object"``, which every OpenAI-compatible endpoint
    rejects. It failed only against real providers; the fake sends no
    ``response_format`` at all, so no test ever saw it.
    """
    transport = _RecordingTransport()
    provider = _provider_recording_into(monkeypatch, transport)

    proposal = await provider.choose_tool(
        system="you are an operator",
        prompt="A customer asks about a refund.",
        available_tools=["search_knowledge", "lookup_order"],
    )

    assert len(transport.bodies) == 1, "expected exactly one request"
    _assert_structured_output_request(transport.bodies[0])

    # The tool menu must still reach the model; wrapping the schema is not a
    # licence to drop the rest of the request.
    sent_prompt = transport.bodies[0]["messages"][-1]["content"]
    assert "search_knowledge" in sent_prompt

    # And the proposal still round-trips into the unvalidated dict the port
    # promises, unchanged by the fix.
    assert proposal["tool_name"] == "search_knowledge"
    assert proposal["arguments"] == {"query": "refund"}
    assert proposal["done"] is False


async def test_both_model_call_paths_send_the_same_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choose_tool`` and ``generate_structured`` must not drift apart again.

    The defect existed because the two call sites were written separately and
    only one was exercised. Both must produce a well-formed
    ``json_schema`` envelope -- and both must name the schema, since the request
    is otherwise indistinguishable from a second, divergent spelling.
    """
    transport = _RecordingTransport()
    provider = _provider_recording_into(monkeypatch, transport)

    await provider.generate_structured(
        system="s",
        prompt="classify this ticket",
        schema=TicketClassification,
    )
    await provider.choose_tool(
        system="s", prompt="what next?", available_tools=["search_knowledge"]
    )

    assert len(transport.bodies) == 2
    structured_body, tool_body = transport.bodies
    _assert_structured_output_request(structured_body)
    _assert_structured_output_request(tool_body)

    assert structured_body["response_format"].keys() == tool_body["response_format"].keys()
    assert set(tool_body["response_format"]["json_schema"]) >= {"name", "schema", "strict"}


async def test_a_bare_json_schema_is_not_a_valid_response_format() -> None:
    """The exact mistake, stated as a fact the rest of this file relies on.

    ``_shared.json_schema_for`` returns a bare JSON Schema whose ``type`` is the
    *shape* of the value (``object``), which is not one of the three values the
    API accepts for ``response_format.type``. Recording this through the real SDK
    keeps the guard honest if ``json_schema_for`` ever changes.
    """
    from opspilot.adapters.models import _shared

    transport = _RecordingTransport()
    import openai

    client = openai.AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),  # type: ignore[arg-type]
    )

    # The SDK does not police this. It forwards the dict as-is, so the invalid
    # variant is observable on the wire -- which is exactly why only a real
    # provider revealed the defect. The `arg-type` ignore is the type checker
    # independently rejecting the same thing: `response_format` is annotated to
    # take a Pydantic class, so a bare schema is a type error as well as a 400.
    await client.beta.chat.completions.parse(
        model="test-model",
        messages=[{"role": "user", "content": "p"}],
        response_format=_shared.json_schema_for(ProposedAction),  # type: ignore[arg-type]
    )

    assert len(transport.bodies) == 1
    bare_on_wire = transport.bodies[0]["response_format"]
    assert bare_on_wire["type"] == "object"
    assert bare_on_wire["type"] not in LEGAL_RESPONSE_FORMAT_TYPES

    # ``json_schema_for`` still returns a useful bare schema; that is only correct
    # for the Anthropic adapter, which wraps it in ``output_config`` itself.
    bare = _shared.json_schema_for(ProposedAction)
    assert bare["type"] == "object"
    assert "properties" in bare
    assert "additionalProperties" in bare
