"""``OpenAIModelProvider``: the OpenAI SDK behind the ``ModelProvider`` port.

Responsibility: call OpenAI models and return schema-validated results. Selected
by ``MODEL_PROVIDER=openai`` with ``OPENAI_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``openai`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed. The core
workflow, the full test suite and the golden-path demo run with neither provider
SDK present; the optional extra in ``pyproject.toml`` is the guard.
"""

from __future__ import annotations

from typing import Any

from opspilot.ports.model_provider import ModelResponse, TModel


class OpenAIModelProvider:
    """OpenAI-backed ``ModelProvider``. Imports the SDK lazily."""

    def __init__(self, *, api_key: str, model_name: str, timeout_seconds: float = 60.0) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Call OpenAI and parse the result into ``schema``. M0 stub.

        The SDK import lives here, not at module scope.
        """
        raise NotImplementedError

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Call OpenAI and return a tool proposal. M0 stub."""
        raise NotImplementedError

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Call OpenAI and return free-form text. M0 stub."""
        raise NotImplementedError
