"""Boundary tests: what request does the Anthropic provider put on the wire?

Two things are pinned here, both at the boundary where the defect would live:

- Which *structured-output mechanism* a configured provider sends. The default
  (``output_config``) is the correct request for real Anthropic and must not be
  silently replaced by the gateway workaround; the opt-in (``tool``) must send a
  forced tool declaration and parse the arguments the model returns rather than
  any prose.
- Which *model name* is recorded. A gateway can answer for a model other than
  the one requested, and ``docs/evals.md`` requires the results file to name the
  model that produced the numbers -- so the recorded name must come from the
  response, not from the string we asked for.

The provider is driven against a genuine ``AsyncAnthropic`` client whose
transport records the serialized HTTP request, so serialization and response
parsing run exactly as in production. CI stays offline.

The installed SDK (``anthropic`` 1.11) is built against ``httpx2``; that is the
package this file imports, not ``httpx``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from opspilot.adapters.models import _shared
from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider
from opspilot.agents.schemas import TicketClassification

_CLASSIFICATION_JSON = json.dumps(
    {"category": "billing_other", "confidence": 0.9, "rationale": "mentions an invoice"}
)

#: The model the fake gateway reports, deliberately different from the one the
#: provider is configured with (as ``deepseek-v4.1-flash`` differs from
#: ``claude-haiku-4-5`` on the real gateway). The results must name this.
_REPORTED_MODEL = "deepseek-v4.1-flash"
_CONFIGURED_MODEL = "claude-haiku-4-5"


class _RecordingTransport(httpx2.AsyncBaseTransport):
    """Records the request body, then answers with a canned Anthropic message."""

    def __init__(
        self, *, content: list[dict[str, Any]], model: str | None = _REPORTED_MODEL
    ) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._content = content
        self._model = model

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content.decode()))
        body: dict[str, Any] = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": self._content,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        if self._model is not None:
            body["model"] = self._model
        return httpx2.Response(200, json=body)


def _provider_recording_into(
    monkeypatch: pytest.MonkeyPatch,
    transport: _RecordingTransport,
    *,
    base_url: str = "",
    structured_output: str = "output_config",
) -> AnthropicModelProvider:
    """A real provider whose SDK client records instead of sending."""
    import anthropic

    real_async_anthropic = anthropic.AsyncAnthropic

    def _build(**kwargs: object) -> object:
        return real_async_anthropic(
            api_key=str(kwargs.get("api_key", "")),
            base_url=str(kwargs.get("base_url") or "https://example.invalid"),
            http_client=httpx2.AsyncClient(transport=transport),
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _build)
    return AnthropicModelProvider(
        api_key="test-key",
        model_name=_CONFIGURED_MODEL,
        base_url=base_url,
        structured_output=structured_output,  # type: ignore[arg-type]
    )


def _text_reply() -> list[dict[str, Any]]:
    return [{"type": "text", "text": _CLASSIFICATION_JSON}]


def _tool_reply(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "tool_use", "id": "toolu_1", "name": "emit_structured_output", "input": arguments}
    ]


# -- structured-output mechanism ---------------------------------------------


async def test_default_path_sends_output_config_not_a_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default still sends ``output_config`` and declares no tool.

    This is the regression test that stops the gateway workaround from silently
    becoming the production request: a plain Anthropic deployment must keep the
    native JSON-schema path, which makes a malformed reply impossible.
    """
    transport = _RecordingTransport(content=_text_reply())
    provider = _provider_recording_into(monkeypatch, transport)

    await provider.generate_structured(system="s", prompt="classify", schema=TicketClassification)

    sent = transport.bodies[0]
    assert "output_config" in sent, "the default path must request output_config"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"] == _shared.json_schema_for(
        TicketClassification
    )
    assert "tools" not in sent, "the default path must not declare a tool"
    assert "tool_choice" not in sent, "the default path must not force a tool"


async def test_tool_path_sends_a_forced_tool_and_parses_its_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in path declares one tool, forces it, and reads its arguments.

    The point of the mechanism is that the arguments arrive already structured
    in the ``tool_use`` block, so there is no text to parse -- but the value is
    still validated through ``_shared.parse_structured``.
    """
    arguments = {"category": "billing_other", "confidence": 0.9, "rationale": "mentions an invoice"}
    transport = _RecordingTransport(content=_tool_reply(arguments))
    provider = _provider_recording_into(monkeypatch, transport, structured_output="tool")

    response = await provider.generate_structured(
        system="s", prompt="classify", schema=TicketClassification
    )

    sent = transport.bodies[0]
    assert "output_config" not in sent, "the tool path must not also send output_config"
    assert len(sent["tools"]) == 1
    tool = sent["tools"][0]
    assert tool["input_schema"] == _shared.json_schema_for(TicketClassification)
    assert sent["tool_choice"] == {"type": "tool", "name": tool["name"]}

    assert isinstance(response.value, TicketClassification)
    assert response.value.category == "billing_other"
    assert response.value.confidence == 0.9


async def test_tool_path_rejects_arguments_that_do_not_fit_the_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-conforming ``tool_use`` input raises rather than returning a partial."""
    transport = _RecordingTransport(
        content=_tool_reply({"category": "not_a_category", "confidence": 2.0, "rationale": "x"})
    )
    provider = _provider_recording_into(monkeypatch, transport, structured_output="tool")

    with pytest.raises(_shared.StructuredOutputError):
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)


# -- reported model -----------------------------------------------------------


async def test_reported_model_is_the_responses_not_the_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage records the model the response named, not the one asked for.

    A gateway answering for a different model would otherwise produce a results
    file attributing the numbers to a model that did not produce them.
    """
    transport = _RecordingTransport(content=_text_reply())
    provider = _provider_recording_into(monkeypatch, transport)

    response = await provider.generate_structured(
        system="s", prompt="p", schema=TicketClassification
    )

    assert response.usage.model == _REPORTED_MODEL
    assert response.usage.model != _CONFIGURED_MODEL


async def test_reported_model_falls_back_to_the_configured_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that omits ``model`` falls back to the configured name."""
    transport = _RecordingTransport(content=_text_reply(), model=None)
    provider = _provider_recording_into(monkeypatch, transport)

    response = await provider.generate_structured(
        system="s", prompt="p", schema=TicketClassification
    )

    assert response.usage.model == _CONFIGURED_MODEL


async def test_tool_path_also_records_the_reported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both mechanisms record the reported model through the same code path."""
    arguments = {"category": "billing_other", "confidence": 0.9, "rationale": "x"}
    transport = _RecordingTransport(content=_tool_reply(arguments))
    provider = _provider_recording_into(monkeypatch, transport, structured_output="tool")

    response = await provider.generate_structured(
        system="s", prompt="p", schema=TicketClassification
    )

    assert response.usage.model == _REPORTED_MODEL


# -- base URL ----------------------------------------------------------------


def _capture_client_kwargs(monkeypatch: pytest.MonkeyPatch, *, base_url: str) -> dict[str, object]:
    """Construct the provider's client and return the kwargs the SDK received."""
    import anthropic

    captured: dict[str, object] = {}

    def _recorder(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _recorder)
    provider = AnthropicModelProvider(
        api_key="test-key", model_name=_CONFIGURED_MODEL, base_url=base_url
    )
    provider._client()  # the client build is the seam under test
    return captured


def test_empty_base_url_is_not_passed_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ``ANTHROPIC_BASE_URL`` omits ``base_url`` entirely."""
    captured = _capture_client_kwargs(monkeypatch, base_url="")
    assert "base_url" not in captured
    assert captured["api_key"] == "test-key"


def test_non_empty_base_url_is_passed_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured ``ANTHROPIC_BASE_URL`` reaches the SDK."""
    captured = _capture_client_kwargs(monkeypatch, base_url="http://127.0.0.1:15742")
    assert captured["base_url"] == "http://127.0.0.1:15742"
