"""Tool permissions, tool specifications, call status, and the static registry.

Responsibility: define what a tool *is* to OpsPilot -- its name, which MCP
server it lives on, whether it needs an idempotency key, and, most importantly,
its permission level -- and hold the registry that is the single source of truth
for those facts.

Layer: ``domain``. Imports only the standard library and Pydantic.

The security argument lives here. Permissions are assigned in **static code at
the registration site** -- never in config, a database row, a prompt, or model
output. The threat model is the agent's own influence surface (prompt text,
retrieved documents, model output, tool arguments); none of those can reach a
Python ``Final`` dict. ``tests/security/test_permission_immutability.py``
asserts the registry equals a hard-coded expected mapping, so any change to a
permission is a red test a human must justify. See ``docs/tool-permissions.md``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict


class Permission(StrEnum):
    """How a tool may execute. Three levels, not five -- see §1 of the spec."""

    READ = "read"
    SAFE_WRITE = "safe_write"
    HIGH_RISK_WRITE = "high_risk_write"


class ToolCallStatus(StrEnum):
    """The lifecycle of a single tool call (the ``tool_calls.status`` column)."""

    PROPOSED = "proposed"
    REJECTED = "rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTED = "executed"
    FAILED = "failed"


class ToolSpec(BaseModel):
    """The static description of a registered tool.

    ``permission`` is the load-bearing field and is set by hand at the
    registration site. ``requires_idempotency_key`` is schema-enforced at gate 1
    for tools such as ``billing.issue_refund``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    permission: Permission
    server: str
    description: str = ""
    requires_idempotency_key: bool = False
    requires_approval: bool = False


# The Phase 1 registry. Transcribed from docs/tool-permissions.md §2 so the
# permissions are visible in one place; these are the tools the MCP servers in
# docs/architecture.md §8 expose. Reviewing a diff to this dict is how a
# permission change is caught.
TOOL_REGISTRY: Final[dict[str, ToolSpec]] = {
    "knowledge.search": ToolSpec(
        name="knowledge.search",
        permission=Permission.READ,
        server="internal",
        description="Semantic search over indexed policy documents.",
    ),
    "crm.get_customer": ToolSpec(name="crm.get_customer", permission=Permission.READ, server="crm"),
    "crm.get_account": ToolSpec(name="crm.get_account", permission=Permission.READ, server="crm"),
    "crm.get_subscription": ToolSpec(
        name="crm.get_subscription", permission=Permission.READ, server="crm"
    ),
    "billing.get_invoice": ToolSpec(
        name="billing.get_invoice", permission=Permission.READ, server="billing"
    ),
    "billing.list_transactions": ToolSpec(
        name="billing.list_transactions", permission=Permission.READ, server="billing"
    ),
    "billing.issue_refund": ToolSpec(
        name="billing.issue_refund",
        permission=Permission.HIGH_RISK_WRITE,
        server="billing",
        description="Refund a transaction. Never automatic; requires human approval.",
        requires_idempotency_key=True,
        requires_approval=True,
    ),
    "issues.search": ToolSpec(name="issues.search", permission=Permission.READ, server="issues"),
    "issues.create": ToolSpec(
        name="issues.create",
        permission=Permission.SAFE_WRITE,
        server="issues",
        description="Open an internal issue. Automatic but always audited.",
    ),
}


def get_tool_spec(name: str) -> ToolSpec | None:
    """Return the registered spec for ``name``, or ``None`` if unregistered.

    Gate 2: a model proposing a tool that is not here is rejected and never
    dispatched. M0 stub -- the lookup lands in M1.
    """
    raise NotImplementedError


def validate_tool_call(name: str, arguments: dict[str, Any]) -> ToolSpec:
    """Gate 1: validate arguments against the tool's schema and return its spec.

    Invalid arguments must never reach the gateway. Raises on a programming
    error (arguments that should already have been parsed into a Pydantic
    model). M0 stub.
    """
    raise NotImplementedError
