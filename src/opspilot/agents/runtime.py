"""The agent runtime: the five-gate ``_gate_and_execute`` and the run loop.

Responsibility: own the state machine and drive a ``RunContext`` to a terminal
or parked status, and -- the part that matters most -- enforce that *every* tool
call passes all five gates in order before anything executes. There is no
early-exit shortcut for "trusted" callers, and ``ToolGateway`` is reachable from
``agents/`` only through this function.

Layer: ``agents``. Imports ``opspilot.domain`` and ``opspilot.ports`` -- never
``opspilot.adapters``. The gateway, provider, stores and orchestrator are
injected, so the runtime runs unchanged on the fake provider and the linear
orchestrator.

The invariant this module exists to uphold (``docs/architecture.md`` §1):

    For every tool call with permission HIGH_RISK_WRITE and status executed,
    there exists an ApprovalRequest for that same tool call with status
    approved.  Count of violations = 0.

M0 note: this module is docstrings and stub signatures. The gates are described
in order below so the M1 implementation fills in a specification, not a guess.
"""

from __future__ import annotations

from opspilot.agents.state import RunContext
from opspilot.domain.tools import ToolCallStatus, ToolSpec
from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import ApprovalStore, RunStore, ToolCallStore
from opspilot.ports.tool_gateway import ToolGateway


class _Proposal:
    """Placeholder for a parsed, gate-1-validated proposal.

    Real definition lands in M1; the runtime refers to it by name so the flow of
    ``_gate_and_execute`` reads correctly now.
    """


async def _gate_and_execute(ctx: RunContext, proposal: _Proposal) -> ToolCallStatus:
    """Run the five gates, then execute -- the only path to a side effect.

    Order is the specification, not an implementation detail (see
    ``docs/tool-permissions.md`` §3):

    1. SCHEMA VALIDATION -- arguments already parsed into a Pydantic model when
       the proposal was accepted; invalid arguments never reach here, and if
       they do it is a programming error and raises.
    2. REGISTRY LOOKUP -- the tool name must be in the static ``TOOL_REGISTRY``.
       An unknown tool is recorded as ``rejected`` and never dispatched.
    3. PERMISSION LOOKUP -- read from the registry (static code), never from
       the model, a document, a request body or a database row.
    4. POLICY ENGINE -- deterministic preconditions (amount ceiling,
       idempotency pre-check, state preconditions); a denial records the call as
       ``rejected``.
    5. APPROVAL GATE -- ``HIGH_RISK_WRITE`` always stops here: without an
       approved ``ApprovalRequest`` bound to *this* tool call, the run is parked
       (``RequestParked``/``RunParked``) and the worker releases the row. The
       check is a database read by design (§3.1).

    Then: execute via the gateway, and always write an audit record -- for every
    permission level, including ``READ``. M0 stub -- raises, because there is no
    execution path yet.
    """
    raise NotImplementedError


async def run_step(
    ctx: RunContext,
    *,
    provider: ModelProvider,
    gateway: ToolGateway,
    orchestrator: Orchestrator,
    run_store: RunStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
) -> RunContext:
    """Execute one orchestrated step of the run and persist its effects.

    A step that reaches the approval gate raises ``RunParked`` and the run is
    left in ``WAITING_APPROVAL``; that is a supported outcome, not an error.

    M0 stub.
    """
    raise NotImplementedError


async def run_loop(
    ctx: RunContext,
    *,
    provider: ModelProvider,
    gateway: ToolGateway,
    orchestrator: Orchestrator,
    run_store: RunStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
) -> RunContext:
    """Drive a run until it is terminal or parked.

    The worker calls this after ``RunStore.claim_next``. ``WAITING_APPROVAL``
    ends the loop (the worker moves on and the API re-queues the run on
    approval); exceeding ``MAX_STEPS`` fails the run with ``max_steps_exceeded``.
    M0 stub.
    """
    raise NotImplementedError


def validate_tool_call(name: str, arguments: dict[str, object]) -> ToolSpec:
    """Gate 1 helper: validate a proposed call and return its spec. M0 stub."""
    raise NotImplementedError
