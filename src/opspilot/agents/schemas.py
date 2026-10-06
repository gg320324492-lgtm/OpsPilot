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
    """The classification assigned to an inbound ticket.

    The vocabulary is the one the specification documents use. Three members
    were added in M6e -- ``BILLING_DISPUTE``, ``ACCOUNT_ACCESS`` and
    ``TECHNICAL_ISSUE`` -- because four documents and all twenty cases in
    ``evals/datasets/classification.jsonl`` already named them and the enum
    could not produce them. Every member's docstring says what it separates
    from its neighbour, because a distinction nobody can apply is worse than a
    missing category: it takes the model's one confident answer and splits it
    into two coin flips.
    """

    DUPLICATE_CHARGE = "duplicate_charge"
    """A customer reports being charged twice for one thing. Named and specific:
    the ticket alleges a repeated charge for a single invoice. Distinct from
    ``BILLING_DISPUTE``, which is a billing query that is *not* this."""

    BILLING_DISPUTE = "billing_dispute"
    """A dispute about the amount billed -- overcharge, wrong plan price, a seat
    count that does not match the contract, a request to waive an invoice --
    where the objection is to the money owed rather than to a repeated charge.
    This is the general billing question ``DUPLICATE_CHARGE`` is the one
    documented instance of, and the category the README, ``docs/milestones.md``
    M6, ``docs/evals.md`` and ``docs/api-contract.md`` all show. The pair is
    decidable from the ticket alone: "charged twice" is a duplicate;
    "charged wrongly" is a dispute."""

    BILLING_OTHER = "billing_other"
    """Billing that is neither a duplicate charge nor a dispute over the
    amount -- a card expiry, a payment method change, a fee explanation.
    Distinguished from both neighbours by there being nothing to contest."""

    TECHNICAL = "technical"
    """M0 vocabulary retained but superseded by ``TECHNICAL_ISSUE``, which the
    four documents and every dataset case use. Nothing names this member; it is
    kept so a persisted ``classification`` row written before M6e still
    deserialises, and is a candidate for removal once that window closes."""

    TECHNICAL_ISSUE = "technical_issue"
    """The product misbehaves: an error, an outage, a broken integration, a
    rendering or rate-limit defect. Distinguished from ``TECHNICAL`` (the same
    idea under a name nothing uses) and from ``BILLING_DISPUTE`` by intent: the
    ticket asks for the thing to work, not for money back. ``docs/evals.md``
    section 1 makes the boundary explicit -- "a billing dispute that mentions
    an API outage is still ``billing_dispute``, not ``technical_issue``" --
    which is what makes the two separable given only the ticket text."""

    ACCOUNT = "account"
    """M0 vocabulary retained but superseded by ``ACCOUNT_ACCESS``, which the
    four documents and every dataset case use. Nothing names this member; see
    ``TECHNICAL`` for why it is kept."""

    ACCOUNT_ACCESS = "account_access"
    """Someone cannot get into, or into the right part of, the account: a
    locked-out login, an SSO tenant mismatch, a lost MFA device, a seat added or
    a leaver removed. Distinguished from ``ACCOUNT`` (the same idea under an
    unused name) by describing the authentication and membership surface
    specifically -- every case in ``classification.jsonl`` names a login, a
    user or a seat, never the account as a billing entity."""

    OTHER = "other"
    """A question the taxonomy does not cover: a contract or compliance
    enquiry, a release-note lookup, a product question. The fallback, and the
    only member a ticket may be assigned without a distinguishing feature."""


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
