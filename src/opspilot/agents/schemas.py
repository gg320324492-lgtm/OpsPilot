"""Structured-output schemas for model calls.

Responsibility: the Pydantic value objects the model is allowed to produce. This
is gate 1's type surface -- the model emits free-form text, which is parsed into
one of these; unknown fields, wrong types and bad enums are rejected before
anything else happens.

Layer: ``agents``. Imports Pydantic and ``opspilot.domain`` only.

``ProposedAction`` is the load-bearing one: it is a *proposal*, never an effect.
Nothing in this module can execute anything. See ``docs/architecture.md`` §1.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TicketCategory(StrEnum):
    """The classification assigned to an inbound ticket."""

    DUPLICATE_CHARGE = "duplicate_charge"
    BILLING_OTHER = "billing_other"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    OTHER = "other"


class TicketClassification(BaseModel):
    """The result of the CLASSIFYING step."""

    model_config = ConfigDict(extra="forbid")

    category: TicketCategory
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class ProposedAction(BaseModel):
    """What the model proposes to do next.

    Either a tool call (``tool_name`` set) or a decision to respond without a
    tool. This is *untrusted* output: the tool name is checked against the
    registry (gate 2), the arguments against the tool's schema (gate 1), and the
    permission against static code (gate 3) before anything executes.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    done: bool = False


class AgentResponse(BaseModel):
    """The final customer-facing reply, assembled from the run's trace."""

    model_config = ConfigDict(extra="forbid")

    body: str
    cited_document_slugs: list[str] = Field(default_factory=list)
    escalated: bool = False
