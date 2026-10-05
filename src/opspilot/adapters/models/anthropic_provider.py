"""``AnthropicModelProvider``: the Anthropic SDK behind the ``ModelProvider`` port.

Responsibility: call Claude and return schema-validated results. Selected by
``MODEL_PROVIDER=anthropic`` with ``ANTHROPIC_API_KEY`` set.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

The ``anthropic`` SDK is imported **inside each method**, never at module top
level, so this module imports cleanly when the SDK is not installed -- the
package, the fake-provider demo and the whole test suite must run with neither
provider SDK present. ``pyproject.toml`` keeps the SDK in an optional extra for
the same reason.
"""

from __future__ import annotations

from typing import Any

from opspilot.ports.model_provider import ModelResponse, TModel


class AnthropicModelProvider:
    """Claude-backed ``ModelProvider``. Imports the SDK lazily."""

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
        """Call Claude and parse the result into ``schema``. M0 stub.

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
        """Call Claude and return a tool proposal. M0 stub."""
        raise NotImplementedError

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Call Claude and return free-form text. M0 stub."""
        raise NotImplementedError
