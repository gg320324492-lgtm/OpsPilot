"""``FakeModelProvider``: replays recorded fixtures.

Responsibility: a deterministic ``ModelProvider`` that returns recorded
responses from ``evals/datasets/fixtures/`` instead of calling a real model. It
is the default provider (``MODEL_PROVIDER=fake``) so a fresh clone and CI work
with no API key, and it is what the deterministic demo runs on.

Layer: ``adapters``. Implements ``opspilot.ports.model_provider.ModelProvider``.

It is scriptable, which is how the security tests work: a test points it at a
fixture whose recorded response *complies with a prompt injection*, and asserts
the run still cannot execute the refund. The fake is how "the model is
encouraged to misbehave" is expressed as a test rather than a story.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opspilot.ports.model_provider import ModelResponse, TModel


class FakeModelProvider:
    """A provider that replays recorded fixtures. No network, no SDK."""

    def __init__(self, fixtures_dir: Path, *, provider_name: str = "fake") -> None:
        self._fixtures_dir = fixtures_dir
        self._provider_name = provider_name

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Replay a recorded structured response for ``schema``. M0 stub."""
        raise NotImplementedError

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Replay a recorded tool proposal. M0 stub."""
        raise NotImplementedError

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Replay a recorded free-form reply. M0 stub."""
        raise NotImplementedError
