"""Shared prompt-assembly and structured-output handling for the provider adapters.

Responsibility: the parts of a ``ModelProvider`` implementation that are the
*same* for every SDK -- rendering the message list, turning a Pydantic schema
into a JSON schema for a structured-output request, parsing and validating the
model's reply, and building the ``ModelResponse``. The anthropic and openai
adapters differ only in request shape and response parsing; everything here is
shared so the two do not drift.

Layer: ``adapters``. Imports Pydantic and ``opspilot.agents``/``opspilot.ports``
only. It never imports either SDK -- the SDK import lives inside the adapter
method that uses it, so this module imports with neither installed.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from opspilot.agents.prompts import build_system_prompt
from opspilot.ports.model_provider import ModelUsage


class StructuredOutputError(RuntimeError):
    """The provider returned a value that does not fit the requested schema.

    ``generate_structured`` validates the model's reply against the Pydantic
    model and raises this rather than returning a partial: a schema-invalid
    result twice fails the run (``docs/agent-state-machine.md`` §3).
    """

    def __init__(self, schema_name: str, detail: str) -> None:
        self.schema_name = schema_name
        self.detail = detail
        super().__init__(f"model returned a value invalid for {schema_name}: {detail}")


def render_messages(system: str, prompt: str) -> list[dict[str, Any]]:
    """Build an OpenAI-shaped ``messages`` list from a system and user prompt.

    The Anthropic adapter uses ``system`` and ``messages`` as separate request
    fields; this helper is for the OpenAI-shaped request only. It lives here so
    the *content* of the system prompt is produced by one call
    (``build_system_prompt``) regardless of provider.
    """
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


def default_system(system: str) -> str:
    """Return ``system``, falling back to the assembled default when empty.

    A caller that passes an empty system prompt gets the standard one (with the
    untrusted-material rules and the tool catalogue) rather than an empty string:
    the safety framing must not be omittable by accident.
    """
    return system or build_system_prompt()


def with_tool_menu(prompt: str, available_tools: list[str]) -> str:
    """Append the caller's tool menu to ``prompt``.

    The port's ``choose_tool`` receives ``available_tools`` "for ergonomics
    only" -- gate 2 still re-checks the name against the registry -- but showing
    the model the exact list is what makes a valid proposal likely. This lives in
    one place so both adapters name the tools identically.
    """
    if not available_tools:
        return prompt
    menu = ", ".join(available_tools)
    return f"{prompt}\n\nTools available for this step: {menu}"


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON schema for ``schema``, suitable for a structured-output request.

    Both SDKs accept a JSON schema; Pydantic's ``model_json_schema`` is the
    source. ``additionalProperties`` is left as Pydantic emits it (``false`` for
    the ``extra='forbid'`` schemas in ``agents/schemas.py``), so the provider
    enforces the same "no unknown fields" rule gate 1 later re-checks.
    """
    return schema.model_json_schema()


def parse_structured[TModel: BaseModel](raw: str | dict[str, Any], schema: type[TModel]) -> TModel:
    """Parse ``raw`` (a JSON string or already-decoded object) into ``schema``.

    Raises ``StructuredOutputError`` -- never returns a partial -- when the value
    does not validate. This is the *validation* half of ``generate_structured``
    and is shared so both adapters fail identically. Generic so the caller gets
    its own Pydantic type back, matching ``ModelProvider.generate_structured``.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(schema.__name__, f"not valid JSON: {exc}") from exc

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        detail = f"{exc.error_count()} validation error(s)"
        raise StructuredOutputError(schema.__name__, detail) from exc


def build_usage(
    *,
    provider: str,
    model: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> ModelUsage:
    """Build a ``ModelUsage`` with a best-effort cost estimate.

    The estimate uses a small published price table per 1K tokens; an unknown
    model reports ``0.0`` rather than guessing. A trace that says "0 USD" for an
    unpriced model is honest; a made-up number is not.
    """
    price = _PRICE_PER_1K_TOKENS.get(model)
    if price is None:
        cost = 0.0
    else:
        input_price, output_price = price
        cost = (input_tokens / 1000.0) * input_price + (output_tokens / 1000.0) * output_price
    return ModelUsage(
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=round(cost, 6),
    )


# USD per 1K tokens, (input, output). Deliberately tiny and best-effort: this is
# for a trace, not for billing. An unpriced model reports 0.0 rather than a
# fabricated number. Extend as models are used; see the provider's pricing page.
_PRICE_PER_1K_TOKENS: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet-latest": (0.003, 0.015),
    "claude-3-5-haiku-latest": (0.0008, 0.004),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
}
