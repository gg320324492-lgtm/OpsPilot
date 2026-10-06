"""``OpenAIModelProvider``: the OpenAI SDK behind the ``ModelProvider`` port.

Responsibility: call OpenAI models and return schema-validated results. Selected
by ``MODEL_PROVIDER=openai`` with ``OPENAI_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``openai`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed. The core
workflow, the full test suite and the golden-path demo run with neither provider
SDK present; the optional extra in ``pyproject.toml`` is the guard.

API shape verified against the installed SDK (``openai`` 3.x): structured output
is ``client.beta.chat.completions.parse(response_format=<PydanticModel>)``, which
returns ``choices[0].message.parsed`` already validated. Prompt assembly,
schema-to-JSON conversion and usage accounting are shared with the Anthropic
adapter in ``_shared``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from opspilot.adapters.models import _shared
from opspilot.ports.model_provider import ModelResponse, TModel

if TYPE_CHECKING:
    # Annotation-only: the runtime import stays function-local so this module (and
    # therefore the package) imports without any agent-layer coupling at import.
    from opspilot.agents.schemas import ProposedAction


class MissingAPIKeyError(RuntimeError):
    """The provider was constructed without an API key.

    Raised eagerly from each call so a misconfigured deployment fails with a
    named error at first use, not deep inside the SDK with an opaque one.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"{provider} provider has no API key configured; set the provider's "
            f"API key environment variable or select MODEL_PROVIDER=fake"
        )


class OpenAIModelProvider:
    """OpenAI-backed ``ModelProvider``. Imports the SDK lazily."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        base_url: str = "",
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url

    def _client(self) -> Any:
        """Build the async client, importing the SDK here and checking the key.

        The key check precedes the import so a missing key raises a named error
        even on a machine without the SDK -- the message is the same either way.

        ``base_url`` is passed only when non-empty. An empty string is a
        *different* argument from an omitted one: the SDK treats ``""`` as an
        endpoint rather than "use the default", so passing it unconditionally
        would break every plain-OpenAI deployment. Omitting it leaves the SDK's
        own ``https://api.openai.com/v1`` in place.
        """
        if not self._api_key:
            raise MissingAPIKeyError("openai")
        from openai import AsyncOpenAI

        if self._base_url:
            return AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                base_url=self._base_url,
            )
        return AsyncOpenAI(api_key=self._api_key, timeout=self._timeout_seconds)

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Call OpenAI and parse the result into ``schema``.

        Uses the SDK's structured-output ``parse`` path with the Pydantic model
        as ``response_format``; the SDK validates and returns
        ``message.parsed``. A ``None`` or schema-invalid result raises via
        ``_shared.parse_structured`` -- never a partial.
        """
        client = self._client()
        started = time.perf_counter()
        completion = await client.beta.chat.completions.parse(
            model=self._model_name,
            messages=_shared.render_messages(_shared.default_system(system), prompt),
            response_format=schema,
            timeout=timeout_seconds or self._timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        message = completion.choices[0].message
        # Validate the raw content ourselves rather than trusting the SDK's
        # ``message.parsed``: both produce the same object, but parsing here
        # means a schema-invalid or refused reply raises ``StructuredOutputError``
        # naming the schema on the one shared path instead of returning None.
        value = _shared.parse_structured(message.content or "", schema)

        usage = completion.usage
        return ModelResponse[TModel](
            value=value,
            usage=_shared.build_usage(
                provider="openai",
                model=self._model_name,
                latency_ms=latency_ms,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
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
        """Call OpenAI and return a tool proposal as an unvalidated dict.

        The proposal is returned unvalidated on purpose: gate 1 and gate 2 decide
        whether it is acceptable, and the port's contract says the result is
        untrusted until then. ``tool_choice`` is left to the model; a proposal
        naming an unregistered tool is a legal (and testable) outcome.
        """
        client = self._client()
        completion = await client.beta.chat.completions.parse(
            model=self._model_name,
            messages=_shared.render_messages(
                _shared.default_system(system),
                _shared.with_tool_menu(prompt, available_tools),
            ),
            response_format=_tool_proposal_schema(),
            timeout=timeout_seconds or self._timeout_seconds,
        )
        message = completion.choices[0].message
        proposal = _shared.parse_structured(message.content or "", _tool_proposal_model())
        dumped: dict[str, Any] = proposal.model_dump()
        return dumped

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Call OpenAI and return free-form text, used only for the customer reply."""
        client = self._client()
        completion = await client.chat.completions.create(
            model=self._model_name,
            messages=_shared.render_messages(_shared.default_system(system), prompt),
            timeout=timeout_seconds or self._timeout_seconds,
        )
        return completion.choices[0].message.content or ""


def _tool_proposal_model() -> type[ProposedAction]:
    """The Pydantic model the tool-proposal JSON is parsed into.

    A local import of the agent schema: ``ProposedAction`` is the value object a
    proposal is, and reusing it keeps the fake and both real providers aligned on
    the same shape.
    """
    from opspilot.agents.schemas import ProposedAction

    return ProposedAction


def _tool_proposal_schema() -> dict[str, Any]:
    """The JSON schema for a tool proposal."""
    return _shared.json_schema_for(_tool_proposal_model())
