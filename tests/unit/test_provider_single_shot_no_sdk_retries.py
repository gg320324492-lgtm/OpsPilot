"""M1-02: one logical model call must put exactly one request on the wire.

The two real provider adapters used to rely on the SDKs' built-in retry
behaviour. Both SDKs default to ``max_retries=2``, so a single
``generate_structured`` call that met a 429, a 5xx or a timeout would issue up
to three HTTP requests -- and, when a retry succeeded, charge for requests
whose replies the caller never sees. The Phase 2 review
(``docs/phase2-review-2026-11-14.md`` §R1) reproduced this offline with a
mocked transport and counted three requests for one logical call.

The fix is policy, not machinery: both client factories now pass
``max_retries=0`` explicitly, on the custom-endpoint *and* the default-endpoint
path. These tests pin that from the outside, the same way the review observed
it -- a real SDK client whose transport counts requests -- so the assertion
survives an SDK upgrade that renames private internals. Retrying, when an
operator wants it, belongs to the caller's policy (the planning loop), not to
a transport the caller cannot see.

The installed ``anthropic`` SDK is built against ``httpx2`` and rejects an
``httpx.AsyncClient`` outright, so the Anthropic side injects ``httpx2``; the
OpenAI side keeps ``httpx``, as its boundary tests already do. CI stays
offline: every failure mode is simulated at the transport boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider
from opspilot.adapters.models.openai_provider import OpenAIModelProvider
from opspilot.agents.schemas import TicketClassification

pytest.importorskip("openai", reason="the openai SDK is an optional extra")
pytest.importorskip("anthropic", reason="the anthropic SDK is an optional extra")
httpx2: Any = pytest.importorskip("httpx2", reason="the anthropic SDK's transport module")

_CUSTOM_BASE_URL = "https://gateway.example.invalid/v1"

_CLASSIFICATION_JSON = json.dumps(
    {"category": "billing_other", "confidence": 0.9, "rationale": "mentions an invoice"}
)

# A minimal well-formed OpenAI chat completion whose content satisfies the
# classification schema the request asked for.
_OPENAI_COMPLETION: dict[str, Any] = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": _CLASSIFICATION_JSON},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

# A minimal well-formed Anthropic message whose text satisfies the same schema.
_ANTHROPIC_MESSAGE: dict[str, Any] = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": _CLASSIFICATION_JSON}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


# ---------------------------------------------------------------------------
# Counting transports: count requests, then answer every one with the failure.
# The two classes exist because the anthropic SDK type-checks its
# ``http_client`` against ``httpx2``, not ``httpx``.
# ---------------------------------------------------------------------------


class _Counting:
    """Shared counter state; the SDK-facing classes below supply the wire."""

    def __init__(self, failure: Callable[[], Any]) -> None:
        self.requests = 0
        self._failure = failure

    def _next(self) -> Any:
        self.requests += 1
        return self._failure()


class _HttpxTransport(_Counting, httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = self._next()
        assert isinstance(response, httpx.Response)
        return response


class _Httpx2Transport(_Counting, httpx2.AsyncBaseTransport):  # type: ignore[misc]  # httpx2 is importorskip'd, so Any to mypy
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = self._next()
        assert isinstance(response, httpx2.Response)
        return response


def _httpx_failures() -> list[tuple[str, Callable[[], Any]]]:
    def too_many_requests() -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
            headers={"retry-after": "0"},
        )

    def server_error() -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    def timeout() -> httpx.Response:
        raise httpx.ReadTimeout("simulated read timeout")

    return [("429", too_many_requests), ("5xx", server_error), ("timeout", timeout)]


def _httpx2_failures() -> list[tuple[str, Callable[[], Any]]]:
    def too_many_requests() -> httpx2.Response:
        return httpx2.Response(
            429,
            json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
            headers={"retry-after": "0"},
        )

    def server_error() -> httpx2.Response:
        return httpx2.Response(500, json={"error": {"message": "boom"}})

    def timeout() -> httpx2.Response:
        raise httpx2.ReadTimeout("simulated read timeout")

    return [("429", too_many_requests), ("5xx", server_error), ("timeout", timeout)]


_ENDPOINTS = [("", "default"), (_CUSTOM_BASE_URL, "custom")]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def _openai_provider_on(
    monkeypatch: pytest.MonkeyPatch, transport: _HttpxTransport, base_url: str
) -> OpenAIModelProvider:
    """A real provider whose factory runs unmodified, transport swapped only.

    The SDK constructor is wrapped but every keyword the factory passes is
    forwarded -- ``max_retries`` included -- so what the factory builds is
    exactly what gets asserted on. Only the transport is replaced, keeping the
    SDK's own serialization, parsing and retry machinery under test.
    """
    import openai

    real_async_openai = openai.AsyncOpenAI

    def _build(**kwargs: Any) -> Any:
        kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        return real_async_openai(**kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", _build)
    return OpenAIModelProvider(
        api_key="test-key",
        model_name="test-model",
        base_url=base_url,
    )


@pytest.mark.parametrize(("endpoint", "endpoint_id"), _ENDPOINTS)
@pytest.mark.parametrize(("label", "failure"), _httpx_failures(), ids=lambda v: v)
@pytest.mark.asyncio
async def test_openai_single_shot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    endpoint_id: str,
    label: str,
    failure: Callable[[], Any],
) -> None:
    transport = _HttpxTransport(failure)
    provider = _openai_provider_on(monkeypatch, transport, endpoint)
    client = provider._client()
    assert client.max_retries == 0, "the factory must disable SDK retries"

    with pytest.raises(Exception) as exc_info:
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)

    assert transport.requests == 1, (
        f"{label} at {endpoint_id} endpoint: one logical call must issue "
        f"exactly one HTTP request, saw {transport.requests}"
    )
    # The failure the caller sees is the first attempt's, not a retry artefact:
    # the SDK may wrap the transport error (httpx.ReadTimeout becomes the
    # SDK's own APITimeoutError), but it must not have retried underneath it.
    if label == "429":
        assert "rate limited" in str(exc_info.value)


@pytest.mark.parametrize(("endpoint", "endpoint_id"), _ENDPOINTS)
def test_openai_factory_sets_max_retries_zero(endpoint: str, endpoint_id: str) -> None:
    provider = OpenAIModelProvider(api_key="test-key", model_name="test-model", base_url=endpoint)
    client = provider._client()
    assert client.max_retries == 0, endpoint_id


@pytest.mark.asyncio
async def test_openai_success_is_single_request_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path stays one request -- the fix must not add round trips."""

    def success() -> httpx.Response:
        return httpx.Response(200, json=_OPENAI_COMPLETION)

    transport = _HttpxTransport(success)
    provider = _openai_provider_on(monkeypatch, transport, _CUSTOM_BASE_URL)
    await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)
    assert transport.requests == 1


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _anthropic_provider_on(
    monkeypatch: pytest.MonkeyPatch, transport: _Httpx2Transport, base_url: str
) -> AnthropicModelProvider:
    """As the OpenAI helper: factory unmodified, only the transport swapped."""
    import anthropic

    real_async_anthropic = anthropic.AsyncAnthropic

    def _build(**kwargs: Any) -> Any:
        kwargs["http_client"] = httpx2.AsyncClient(transport=transport)
        return real_async_anthropic(**kwargs)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _build)
    return AnthropicModelProvider(
        api_key="test-key",
        model_name="claude-haiku-4-5",
        base_url=base_url,
    )


@pytest.mark.parametrize(("endpoint", "endpoint_id"), _ENDPOINTS)
@pytest.mark.parametrize(("label", "failure"), _httpx2_failures(), ids=lambda v: v)
@pytest.mark.asyncio
async def test_anthropic_single_shot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    endpoint_id: str,
    label: str,
    failure: Callable[[], Any],
) -> None:
    transport = _Httpx2Transport(failure)
    provider = _anthropic_provider_on(monkeypatch, transport, endpoint)
    client = provider._client()
    assert client.max_retries == 0, "the factory must disable SDK retries"

    with pytest.raises(Exception) as exc_info:
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)

    assert transport.requests == 1, (
        f"{label} at {endpoint_id} endpoint: one logical call must issue "
        f"exactly one HTTP request, saw {transport.requests}"
    )
    if label == "429":
        assert "rate limited" in str(exc_info.value)


@pytest.mark.parametrize(("endpoint", "endpoint_id"), _ENDPOINTS)
def test_anthropic_factory_sets_max_retries_zero(endpoint: str, endpoint_id: str) -> None:
    provider = AnthropicModelProvider(
        api_key="test-key", model_name="claude-haiku-4-5", base_url=endpoint
    )
    client = provider._client()
    assert client.max_retries == 0, endpoint_id


@pytest.mark.asyncio
async def test_anthropic_success_is_single_request_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path stays one request -- the fix must not add round trips."""

    def success() -> httpx2.Response:
        return httpx2.Response(200, json=_ANTHROPIC_MESSAGE)

    transport = _Httpx2Transport(success)
    provider = _anthropic_provider_on(monkeypatch, transport, _CUSTOM_BASE_URL)
    await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)
    assert transport.requests == 1
