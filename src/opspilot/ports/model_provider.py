"""The ``ModelProvider`` port.

Responsibility: the boundary between the agent runtime and *any* language model.
The runtime knows only this Protocol -- it never imports the OpenAI or Anthropic
SDK. That is what makes the fake provider a first-class citizen rather than a
test double: the deterministic demo and the whole test suite run on it.

Layer: ``ports``. Imports only ``typing``, Pydantic and ``opspilot.domain``.

The interface is deliberately narrow and structured-output-first. The model may
only produce a schema-validated value object (``generate_structured``) or pick
among already-registered tools (``choose_tool``); there is no method that hands
the model a side effect. See ``docs/architecture.md`` §1 and §3.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)


class ModelUsage(BaseModel):
    """Token and cost accounting for one model call, written as a ``model_called`` event."""

    provider: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class ModelResponse[TModel: BaseModel](BaseModel):
    """A structured response plus its usage record.

    Generic over the validated schema so callers get their own Pydantic type
    back rather than a dict to re-check.
    """

    value: TModel
    usage: ModelUsage


@runtime_checkable
class ModelProvider(Protocol):
    """Produces validated proposals. Never causes a side effect."""

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[TModel],
        timeout_seconds: float | None = None,
    ) -> ModelResponse[TModel]:
        """Produce an instance of ``schema``, or raise if the model cannot comply.

        A schema-invalid result twice fails the run (Phase 1 has no retries).
        """
        ...

    async def choose_tool(
        self,
        *,
        system: str,
        prompt: str,
        available_tools: list[str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Choose a registered tool and arguments, as an unvalidated proposal.

        The result is *untrusted* until it passes gate 1; the tool name is
        constrained to ``available_tools`` here for ergonomics only, and gate 2
        re-checks it against the registry.
        """
        ...

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Produce free-form text, used only for the customer reply."""
        ...
