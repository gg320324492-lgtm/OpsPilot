"""``AnthropicModelProvider``: the Anthropic SDK behind the ``ModelProvider`` port.

Responsibility: call Claude and return schema-validated results. Selected by
``MODEL_PROVIDER=anthropic`` with ``ANTHROPIC_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``anthropic`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed -- the
package, the fake-provider demo and the whole test suite must run with neither
provider SDK present. ``pyproject.toml`` keeps the SDK in an optional extra for
the same reason.

API shape verified against the installed SDK (``anthropic`` 1.x): the default
structured output is requested with
``output_config={"format": {"type": "json_schema", "schema": <json-schema>}}``,
and the reply arrives as a text block whose text is the JSON. An alternative
path, selected explicitly by ``structured_output="tool"``, asks for the same
value through forced tool use -- see :meth:`AnthropicModelProvider
.generate_structured`. Prompt assembly, schema-to-JSON conversion and usage
accounting are shared with the OpenAI adapter in ``_shared``; this module differs
only in request shape and response parsing.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from opspilot.adapters.models import _shared
from opspilot.adapters.models.openai_provider import MissingAPIKeyError
from opspilot.ports.model_provider import ModelResponse, TModel

#: The two structured-output mechanisms the adapter can send. ``output_config``
#: is the correct request for real Anthropic and the default; ``tool`` is the
#: forced-tool-use fallback for a gateway that ignores ``output_config``.
StructuredOutputMechanism = Literal["output_config", "tool"]

#: The tool name used on the forced-tool-use path. Not model-visible prose -- it
#: is the name the forced ``tool_choice`` selects, so it only has to be stable.
_TOOL_NAME = "emit_structured_output"


class AnthropicModelProvider:
    """Claude-backed ``ModelProvider``. Imports the SDK lazily."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        base_url: str = "",
        structured_output: StructuredOutputMechanism = "output_config",
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url
        self._structured_output = structured_output

    def _client(self) -> Any:
        """Build the async client, importing the SDK here and checking the key.

        ``base_url`` is passed only when non-empty. The Anthropic SDK already
        reads ``ANTHROPIC_BASE_URL`` from the environment itself, so this is
        frequently redundant with the environment -- but it is passed
        explicitly when configured so the adapter does not lean on the SDK's
        private precedence rule, and *omitted* when empty so a plain Anthropic
        deployment keeps the SDK's ``https://api.anthropic.com`` (passing ``""``
        would be an invalid endpoint, not "use the default").
        """
        if not self._api_key:
            raise MissingAPIKeyError("anthropic")
        import anthropic

        if self._base_url:
            return anthropic.AsyncAnthropic(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                base_url=self._base_url,
            )
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

        Two mechanisms, chosen by ``structured_output`` at construction:

        - ``output_config`` (default): request a JSON schema via ``output_config``
          and validate the returned text with ``_shared.parse_structured``. This
          is the correct request for real Anthropic -- it makes a malformed reply
          impossible at generation time.
        - ``tool``: force a single tool whose ``input_schema`` is the target
          schema and read the arguments off the returned ``tool_use`` block. The
          gateway returns them as an already-structured object, so there is no
          text to parse. The value is *still* run through
          ``_shared.parse_structured``, so a non-conforming reply raises rather
          than returning something partial.

        Either way a schema-invalid response raises ``StructuredOutputError``.
        """
        client = self._client()
        started = time.perf_counter()
        if self._structured_output == "tool":
            message = await self._create_with_forced_tool(
                client, system=system, prompt=prompt, schema=schema,
                timeout_seconds=timeout_seconds,
            )
            raw: str | dict[str, Any] = _tool_use_input(message)
        else:
            message = await client.messages.create(
                model=self._model_name,
                max_tokens=4096,
                system=_shared.default_system(system),
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {"type": "json_schema", "schema": _shared.json_schema_for(schema)}
                },
                timeout=timeout_seconds or self._timeout_seconds,
            )
            raw = _first_text(message)
        latency_ms = int((time.perf_counter() - started) * 1000)

        value = _shared.parse_structured(raw, schema)
        return ModelResponse[TModel](
            value=value,
            usage=_shared.build_usage(
                provider="anthropic",
                # The name the *response* reported, not the one we asked for: a
                # gateway may answer for a different model, and a results file
                # that names the requested model would attribute the numbers to
                # a model that did not produce them. Fall back to the configured
                # name only when the response omits one.
                model=_shared.reported_model(message, self._model_name),
                latency_ms=latency_ms,
                input_tokens=message.usage.input_tokens if message.usage else 0,
                output_tokens=message.usage.output_tokens if message.usage else 0,
            ),
        )

    async def _create_with_forced_tool(
        self,
        client: Any,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None,
    ) -> Any:
        """Send the forced-tool-use structured request and return the message.

        One tool is declared, its ``input_schema`` being the target schema, and
        ``tool_choice`` forces it, so the model must answer by "calling" it. The
        returned ``tool_use`` block carries the arguments as an object. This is
        the same shape ``choose_tool`` uses against both the gateway and
        OpenRouter, which is why it is the mechanism that works where
        ``output_config`` is ignored.
        """
        return await client.messages.create(
            model=self._model_name,
            max_tokens=4096,
            system=_shared.default_system(system),
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Return the requested value as structured output.",
                    "input_schema": _shared.json_schema_for(schema),
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            timeout=timeout_seconds or self._timeout_seconds,
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

        Delegates to :meth:`generate_structured` rather than issuing its own
        request. It previously built one directly, hardcoding ``output_config``
        and ignoring ``structured_output`` entirely -- so on a gateway that
        ignores ``output_config`` this method failed on every call while
        ``generate_structured`` worked, with the same adapter and the same
        settings. One schema, one request builder, one mechanism.

        The result stays unvalidated past the schema -- gate 1 and gate 2 decide
        whether the named tool and its arguments are acceptable, and the port's
        contract says a proposal is untrusted until they do.
        """
        from opspilot.agents.schemas import ProposedAction

        response = await self.generate_structured(
            system=system,
            # The tool menu goes in the prompt: the port says ``available_tools``
            # is "for ergonomics only" and gate 2 re-checks the name against the
            # registry, so this is a hint rather than an authorisation.
            prompt=_shared.with_tool_menu(prompt, available_tools),
            schema=ProposedAction,
            timeout_seconds=timeout_seconds,
        )
        dumped: dict[str, Any] = response.value.model_dump()
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


def _tool_use_input(message: Any) -> dict[str, Any]:
    """Return the ``input`` object of the first ``tool_use`` block.

    On the forced-tool-use path the model answers by calling the one tool it was
    given, and the arguments arrive already structured in the block's ``input``.
    Returning it as a dict lets ``_shared.parse_structured`` validate it through
    the same path as a JSON string -- so a reply that does not fit the schema
    raises ``StructuredOutputError`` rather than yielding a partial.

    An empty dict is returned when no ``tool_use`` block is present, which the
    schema's required fields then reject in ``parse_structured``; the run fails
    loudly instead of proceeding on an empty proposal.
    """
    blocks = getattr(message, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "tool_use":
            value = getattr(block, "input", None)
            if isinstance(value, dict):
                return value
    return {}

