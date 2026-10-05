"""Deterministic policy engine (gate 4) and risk explanation generation.

Responsibility: the business rules that decide whether a *structurally valid*,
*registered* tool call may proceed -- amount ceilings, idempotency pre-check,
and state preconditions (e.g. "cannot refund an already-refunded transaction").
This gate is code, not a prompt, and its verdict is reproducible.

Layer: ``domain``. Imports only the standard library, Pydantic, and sibling
domain modules. It must not touch the database or the gateway: the idempotency
*pre-check* is a fast short-circuit here, but the authoritative guarantee lives
at the MCP server, which is the only place that can prevent a duplicate row
(``docs/tool-permissions.md`` §4).

The principles: deterministic code decides. ``explain_risk`` produces the
``risk_explanation`` string shown to an approver -- generated from the tool's
static permission and its arguments, never from the model's ``reason``, which is
untrusted.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from opspilot.domain.runs import AgentRun
from opspilot.domain.tools import ToolSpec


class PolicyDecision(BaseModel):
    """The verdict of the policy engine for one tool call."""

    model_config = ConfigDict(frozen=True)

    denied: bool
    reason: str = ""
    explanation: str = ""


def evaluate_policy(spec: ToolSpec, arguments: dict[str, Any], run: AgentRun) -> PolicyDecision:
    """Gate 4: apply the deterministic business rules to a tool call.

    Returns a ``PolicyDecision``. ``denied`` short-circuits execution and the
    call is recorded as ``rejected`` with the gate that rejected it. M0 stub --
    the rule set lands in M1/M3.
    """
    raise NotImplementedError


def explain_risk(spec: ToolSpec, arguments: dict[str, Any]) -> str:
    """Produce the deterministic risk explanation shown to a human approver.

    Example: "This will move $129.00 and cannot be undone automatically." Built
    from the static permission and the arguments, so it cannot be influenced by
    a prompt injection in the model's ``reason``. M0 stub.
    """
    raise NotImplementedError


def derive_idempotency_key(run: AgentRun, arguments: dict[str, Any]) -> str:
    """Derive the deterministic idempotency key for a write tool.

    Shape: ``f"refund:{run.id}:{transaction_id}"``. Not a random UUID (which
    would defeat the purpose) and not timestamp-based (a retry minutes later
    would become a new refund). M0 stub.
    """
    raise NotImplementedError
