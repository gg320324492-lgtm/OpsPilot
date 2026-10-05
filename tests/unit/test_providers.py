"""Unit tests for the real provider adapters that need no API key.

What can be verified without a key: the SDK is imported lazily (the modules
import cleanly with the extras present), the provider constructs, and every
method raises a clear ``MissingAPIKeyError`` when no key is configured rather
than failing deep inside the SDK. What cannot be verified here is the live
request/response shape; that is exercised by the manual ``eval-live`` job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider
from opspilot.adapters.models.openai_provider import MissingAPIKeyError, OpenAIModelProvider
from opspilot.agents.schemas import TicketClassification


def _anthropic() -> AnthropicModelProvider:
    return AnthropicModelProvider(api_key="", model_name="claude-3-5-sonnet-latest")


def _openai() -> OpenAIModelProvider:
    return OpenAIModelProvider(api_key="", model_name="gpt-4o-mini")


@pytest.mark.parametrize("provider_factory", [_anthropic, _openai])
async def test_generate_structured_raises_without_a_key(provider_factory: object) -> None:
    """``generate_structured`` names the missing key rather than reaching the SDK."""
    provider = provider_factory()  # type: ignore[operator]
    with pytest.raises(MissingAPIKeyError) as excinfo:
        await provider.generate_structured(system="s", prompt="p", schema=TicketClassification)
    assert "no API key" in str(excinfo.value)


@pytest.mark.parametrize("provider_factory", [_anthropic, _openai])
async def test_choose_tool_raises_without_a_key(provider_factory: object) -> None:
    """``choose_tool`` fails the same way, before any network use."""
    provider = provider_factory()  # type: ignore[operator]
    with pytest.raises(MissingAPIKeyError):
        await provider.choose_tool(system="s", prompt="p", available_tools=[])


@pytest.mark.parametrize("provider_factory", [_anthropic, _openai])
async def test_generate_text_raises_without_a_key(provider_factory: object) -> None:
    """``generate_text`` fails the same way, before any network use."""
    provider = provider_factory()  # type: ignore[operator]
    with pytest.raises(MissingAPIKeyError):
        await provider.generate_text(system="s", prompt="p")


def test_provider_modules_import_without_sdk_at_module_scope() -> None:
    """Neither adapter imports its SDK at module scope (checked statically too).

    This is a smoke check that importing the module works; the static guarantee
    is ``tests/unit/test_layering.py::test_provider_sdk_is_imported_lazily``.
    """
    import opspilot.adapters.models.anthropic_provider as anthropic_mod
    import opspilot.adapters.models.openai_provider as openai_mod

    source_a = Path(anthropic_mod.__file__).read_text(encoding="utf-8")
    source_o = Path(openai_mod.__file__).read_text(encoding="utf-8")
    assert "\nimport anthropic" not in source_a and "\nfrom anthropic" not in source_a
    assert "\nimport openai" not in source_o and "\nfrom openai" not in source_o
