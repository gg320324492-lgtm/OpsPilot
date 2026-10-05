"""``AnthropicModelProvider``: the Anthropic SDK behind the ``ModelProvider`` port.

Responsibility: call Claude and return schema-validated results. Selected by
``MODEL_PROVIDER=anthropic`` with ``ANTHROPIC_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``anthropic`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed -- the
package, the fake-provider demo and the whole test suite must run with neither
provider SDK present. ``pyproject.toml`` keeps the SDK in an optional extra for
the same reason.

API shape verified against the installed SDK (``anthropic`` 1.x): structured
output is requested with
``output_config={"format": {"type": "json_schema", "schema": <json-schema>}}``,
and the reply arrives as a text block whose text is the JSON. Prompt assembly,
schema-to-JSON conversion and usage accounting are shared with the OpenAI
adapter in ``_shared``; this module differs only in request shape and response
parsing.
"""

from __future__ import annotations

import time
from typing import Any

from opspilot.adapters.models import _shared
from opspilot.adapters.models.openai_provider import MissingAPIKeyError
from opspilot.ports.model_provider import ModelResponse, TModel


class AnthropicModelProvider:
    """Claude-backed ``ModelProvider``. Imports the SDK lazily."""

    def __init__(self, *, api_key: str, model_name: str, timeout_seconds: float = 60.0) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    def _client(self) -> Any:
        """Build the async client, importing the SDK here and checking the key."""
        if not self._api_key:
            raise MissingAPIKeyError("anthropic")
        import anthropic

        return anthropic.AsyncAnthropic(api_key=self._api_key, timeout=self._timeout_seconds)

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Call Claude and parse the result into ``schema``.

        Requests a JSON schema via ``output_config`` and validates the returned
        text with ``_shared.parse_structured``; a schema-invalid response raises
        rather than returning a partial.
        """
        client = self._client()
        started = time.perf_counter()
        output_config = {
            "format": {"type": "json_schema", "schema": _shared.json_schema_for(schema)}
        }
        message = await client.messages.create(
            model=self._model_name,
            max_tokens=4096,
            system=_shared.default_system(system),
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
            timeout=timeout_seconds or self._timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = _first_text(message)
        value = _shared.parse_structured(text, schema)
        return ModelResponse[TModel](
            value=value,
            usage=_shared.build_usage(
                provider="anthropic",
                model=self._model_name,
                latency_ms=latency_ms,
                input_tokens=message.usage.input_tokens if message.usage else 0,
                output_tokens=message.usage.output_tokens if message.usage else 0,
            ),
        )

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Call Claude and return a tool proposal as an unvalidated dict.

        Uses the same structured-output path with ``ProposedAction`` as the
        schema, so the proposal shape matches the fake and the OpenAI adapter.
        The result stays unvalidated past the schema -- gate 1 and gate 2 decide
        whether the named tool and its arguments are acceptable.
        """
        from opspilot.agents.schemas import ProposedAction

        client = self._client()
        message = await client.messages.create(
            model=self._model_name,
            max_tokens=2048,
            system=_shared.default_system(system),
            messages=[{"role": "user", "content": _shared.with_tool_menu(prompt, available_tools)}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _shared.json_schema_for(ProposedAction),
                }
            },
            timeout=timeout_seconds or self._timeout_seconds,
        )
        proposal = _shared.parse_structured(_first_text(message), ProposedAction)
        dumped: dict[str, Any] = proposal.model_dump()
        return dumped

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Call Claude and return free-form text, used only for the customer reply."""
        client = self._client()
        message = await client.messages.create(
            model=self._model_name,
            max_tokens=2048,
            system=_shared.default_system(system),
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_seconds or self._timeout_seconds,
        )
        return _first_text(message)


def _first_text(message: Any) -> str:
    """Concatenate the text blocks of an Anthropic message.

    A structured-output reply is a single text block containing JSON; joining
    every text block tolerates the SDK splitting it, and returns "" when there is
    no text (which ``parse_structured`` then rejects as invalid JSON).
    """
    blocks = getattr(message, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)
