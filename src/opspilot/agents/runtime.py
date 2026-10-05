"""The agent runtime: the five-gate ``_gate_and_execute`` and the run loop.

Responsibility: own the state machine and drive a ``RunContext`` to a terminal
or parked status, and -- the part that matters most -- enforce that *every* tool
call passes all five gates in order before anything executes. There is no
early-exit shortcut for "trusted" callers, and ``ToolGateway`` is reachable from
``agents/`` only through this function.

Layer: ``agents``. Imports ``opspilot.domain``, ``opspilot.ports`` and
``opspilot.tracing`` -- never ``opspilot.adapters`` and never SQLAlchemy. The
gateway, provider, stores, trace recorder and the idempotency lookup are all
injected, so the runtime runs unchanged on the fake provider, the linear
orchestrator, and the real SQLite repositories.

The invariant this module exists to uphold (``docs/architecture.md`` §1):

    For every tool call with permission HIGH_RISK_WRITE and status executed,
    there exists an ApprovalRequest for that same tool call with status
    approved.  Count of violations = 0.

Why ``_gate_and_execute`` is one long linear function rather than a set of
helpers: a numbered sequence of gates can be verified by reading it top to
bottom, and moving a gate into a helper is how a gate silently acquires a caller
that skips it. ``pyproject.toml`` disables the branch/length lints for this file
for exactly this reason. Do not split it up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from opspilot.agents.schemas import ProposedAction
from opspilot.agents.state import RunContext, ToolCallRecord
from opspilot.domain.approvals import ApprovalRequest
from opspilot.domain.errors import IllegalTransition, MaxStepsExceeded, RunParked
from opspilot.domain.policies import (
    derive_idempotency_key,
    evaluate_policy,
    explain_risk,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import (
    TOOL_REGISTRY,
    InvalidArgumentsError,
    Permission,
    ToolCallStatus,
    ToolSpec,
    validate_tool_call,
)
from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import ApprovalStore, RunStore, ToolCallStore
from opspilot.ports.tool_gateway import ToolGateway, ToolResult
from opspilot.tracing.recorder import TraceRecorder

# The step budget defaults to the state-machine document's ``MAX_STEPS = 24``; a
# context may carry its own.
_DEFAULT_MAX_STEPS: Final[int] = 24

# Audit event types written by this module. Named constants so a test asserts on
# a symbol rather than a string literal a typo could quietly change.
AUDIT_TOOL_EXECUTED: Final[str] = "tool_executed"
AUDIT_TOOL_REJECTED: Final[str] = "tool_rejected"
AUDIT_APPROVAL_REQUESTED: Final[str] = "approval_requested"
AUDIT_RUN_FAILED: Final[str] = "run_failed"

# The gate names written to ``ToolCall.rejection_reason``. They are the answer to
# "which gate refused this", so they name the gate, not the rule.
GATE_SCHEMA: Final[str] = "gate_1_schema_validation"
GATE_REGISTRY: Final[str] = "gate_2_registry_lookup"
GATE_PERMISSION: Final[str] = "gate_3_permission_lookup"
GATE_POLICY: Final[str] = "gate_4_policy_engine"
GATE_APPROVAL: Final[str] = "gate_5_approval_gate"

# An injected lookup: given a run id and an idempotency key, return whether an
# *executed* call with that key exists and, if so, the result it recorded. The
# policy engine cannot perform this read itself (``domain`` does no I/O), so the
# runtime performs it and passes the answer to the pure policy function. The
# default lookup finds nothing, which is the safe direction: with no lookup
# configured, a replayed refund is re-executed and stopped by the MCP server and
# the database's partial unique index -- never silently *allowed* to skip a check.
type ExecutedLookup = Callable[[UUID, str], Awaitable[tuple[bool, dict[str, object] | None]]]


async def _no_executed_lookup(
    run_id: UUID,  # noqa: ARG001 -- part of the lookup signature
    idempotency_key: str,  # noqa: ARG001 -- part of the lookup signature
) -> tuple[bool, dict[str, object] | None]:
    """The default idempotency lookup: nothing has executed yet."""
    return False, None


def _utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(UTC)


class ToolCallRef:
    """The runtime's handle on a persisted tool call, passed around inside a gate run.

    Bundles the id -- the thing approval is bound to -- with the static spec and
    the derived idempotency key, so gate 4 and gate 5 agree on all three. It is a
    value the runtime builds *after* it persisted the row, never a decision
    input: gate 5 ignores it and re-reads the database
    (``docs/tool-permissions.md`` §3.1).
    """

    __slots__ = ("arguments", "id", "idempotency_key", "permission", "spec", "tool_name")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002 -- the field is the tool call's id
        tool_name: str,
        spec: ToolSpec,
        arguments: dict[str, object],
        idempotency_key: str | None,
    ) -> None:
        self.id = id
        self.tool_name = tool_name
        self.spec = spec
        self.permission = spec.permission
        self.arguments = arguments
        self.idempotency_key = idempotency_key


def _record_for(
    call_id: UUID,
    tool_name: str,
    status: ToolCallStatus,
    result: dict[str, object] | None = None,
) -> ToolCallRecord:
    """Build the loop's view of a finished tool call."""
    return ToolCallRecord(
        tool_call_id=call_id, tool_name=tool_name, status=status.value, result=result
    )


async def _reject(
    ctx: RunContext,
    recorder: TraceRecorder,
    tool_call_store: ToolCallStore,
    *,
    tool_name: str,
    arguments: dict[str, object],
    permission: str,
    gate: str,
    reason: str,
) -> ToolCallRecord:
    """Persist a rejected ``ToolCall`` and its audit event; return the record.

    A gate that refuses produces a *result*: a row with ``status='rejected'``
    naming the gate in ``rejection_reason``, plus an ``AuditEvent``. It does not
    raise, so the run loop can carry on. ``rejection_reason`` holds the gate
    name; ``error`` holds the rule-level reason, because the two answer different
    questions ("which gate" and "why within it").

    Gates 1 and 2 reject calls whose tool may not be registered at all; there is
    then no ``ToolSpec`` and the permission column is written at the *least*
    privileged level, so a row that never executed can never look like a
    high-risk execution to the CI invariant.
    """
    call_id = await tool_call_store.record_proposed(
        run_id=ctx.run.id,
        tool_name=tool_name,
        arguments=arguments,
        permission=permission,
    )
    await tool_call_store.set_status(
        call_id,
        ToolCallStatus.REJECTED,
        rejection_reason=gate,
        error=reason,
    )
    await recorder.record_audit(
        event_type=AUDIT_TOOL_REJECTED,
        actor="runtime",
        payload={
            "tool_call_id": str(call_id),
            "tool_name": tool_name,
            "gate": gate,
            "reason": reason,
        },
    )
    return _record_for(call_id, tool_name, ToolCallStatus.REJECTED)


async def _gate_and_execute(
    ctx: RunContext,
    proposal: ProposedAction,
    *,
    gateway: ToolGateway,
    run_store: RunStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
    recorder: TraceRecorder,
    executed_lookup: ExecutedLookup | None = None,
    tool_call_id: UUID | None = None,
) -> ToolCallRecord:
    """Run the five gates, then execute -- the only path to a side effect.

    Order is the specification, not an implementation detail (see
    ``docs/tool-permissions.md`` §3). The gates are numbered below, each with a
    comment naming it, because a reviewer verifying the order reads this function
    top to bottom.

    Args:
        tool_call_id: The id of an *existing* tool call row to reuse. On the
            resume pass after an approval, the worker must re-enter the gates for
            the exact call the human approved -- reusing its id is what makes gate
            5's ``has_approved(tool_call_id)`` read true and is why an approval is
            bound to a call, not to a proposal. ``None`` (the first pass) creates
            a fresh row at gate 3.

    A rejection is a returned record with ``status='rejected'`` -- an error is a
    result, not an exception.

    Raises:
        RunParked: Control flow. Gate 5 found no persisted approval for *this*
            tool call and the run is now legitimately waiting for a human. This
            must never be caught and mapped to ``FAILED``.
    """
    tool_name = proposal.tool_name or ""

    # ------------------------------------------------------------------
    # GATE 1 -- SCHEMA VALIDATION
    # ------------------------------------------------------------------
    # Arguments are parsed into the tool's Pydantic model. A tool that is not
    # registered cannot have its arguments validated, so ``spec`` is ``None``
    # here and gate 2 reports the unknown name; malformed arguments for a
    # registered tool are a *result* (a rejected call), not a crash. A model
    # whose output does not fit the schema never reaches the gateway.
    try:
        spec, parsed_arguments = validate_tool_call(tool_name, proposal.arguments)
    except InvalidArgumentsError as exc:
        return await _reject(
            ctx,
            recorder,
            tool_call_store,
            tool_name=tool_name,
            arguments={},
            permission=Permission.READ.value,
            gate=GATE_SCHEMA,
            reason=str(exc),
        )

    # ------------------------------------------------------------------
    # GATE 2 -- REGISTRY LOOKUP
    # ------------------------------------------------------------------
    # ``spec is None`` means the model proposed a tool that does not exist. It is
    # recorded as a rejected call and never dispatched: the model cannot invent a
    # tool name that passes this gate (``docs/tool-permissions.md`` §5).
    if spec is None:
        return await _reject(
            ctx,
            recorder,
            tool_call_store,
            tool_name=tool_name,
            arguments={},
            permission=Permission.READ.value,
            gate=GATE_REGISTRY,
            reason="unknown_tool",
        )

    # ------------------------------------------------------------------
    # GATE 3 -- PERMISSION LOOKUP
    # ------------------------------------------------------------------
    # Static, from the registry in code. Never from the model, a retrieved
    # document, an HTTP request body or a database row (ADR-0003). The permission
    # is snapshotted onto the row on the next line so a future reclassification
    # cannot rewrite what past calls actually did (``docs/data-model.md`` §2).
    permission = spec.permission

    if tool_call_id is None:
        call_id = await tool_call_store.record_proposed(
            run_id=ctx.run.id,
            tool_name=spec.name,
            arguments=parsed_arguments,
            permission=permission.value,
        )
    else:
        # Resume: reuse the row the human approved. Reusing the id -- rather than
        # inserting a fresh call and hoping the approval follows -- is the whole
        # reason gate 5 binds to ``tool_call_id`` (``docs/tool-permissions.md``
        # §3.1). The arguments are re-parsed from the proposal and must match
        # what was snapshotted at the approval, or ``SqlToolCallStore``'s
        # argument-immutability guard raises ``ApprovalArgumentsChanged``.
        call_id = tool_call_id

    idempotency_key: str | None = None
    if spec.requires_idempotency_key:
        idempotency_key = derive_idempotency_key(ctx.run, parsed_arguments)
    call = ToolCallRef(
        id=call_id,
        tool_name=spec.name,
        spec=spec,
        arguments=parsed_arguments,
        idempotency_key=idempotency_key,
    )

    # ------------------------------------------------------------------
    # GATE 4 -- POLICY ENGINE
    # ------------------------------------------------------------------
    # Deterministic business rules: denylist, amount ceiling, refund-exceeds-
    # transaction fast fail, and the idempotency short-circuit. The idempotency
    # lookup is a database read the runtime performs and hands to the *pure*
    # policy function, so the decision stays a pure function of its inputs
    # (``docs/tool-permissions.md`` §4).
    executed: tuple[bool, dict[str, object] | None] | None = None
    if idempotency_key is not None:
        lookup = executed_lookup or _no_executed_lookup
        executed = await lookup(ctx.run.id, idempotency_key)

    decision = evaluate_policy(spec, parsed_arguments, ctx.run, executed=executed)

    if decision.idempotent_replay:
        # Short-circuit: the side effect already happened for this key. Record
        # the remembered result rather than denying or re-executing.
        await tool_call_store.set_status(
            call.id,
            ToolCallStatus.EXECUTED,
            result=decision.executed_result,
            idempotency_key=idempotency_key,
        )
        await recorder.record_audit(
            event_type=AUDIT_TOOL_EXECUTED,
            actor="runtime",
            payload={
                "tool_call_id": str(call.id),
                "tool_name": call.tool_name,
                "permission": permission.value,
                "idempotent_replay": True,
            },
        )
        return _record_for(
            call.id, call.tool_name, ToolCallStatus.EXECUTED, decision.executed_result
        )

    if decision.denied:
        await tool_call_store.set_status(
            call.id,
            ToolCallStatus.REJECTED,
            rejection_reason=GATE_POLICY,
            error=decision.reason,
        )
        await recorder.record_audit(
            event_type=AUDIT_TOOL_REJECTED,
            actor="runtime",
            payload={
                "tool_call_id": str(call.id),
                "tool_name": call.tool_name,
                "gate": GATE_POLICY,
                "reason": decision.reason,
            },
        )
        return _record_for(call.id, call.tool_name, ToolCallStatus.REJECTED)

    # ------------------------------------------------------------------
    # GATE 5 -- APPROVAL GATE
    # ------------------------------------------------------------------
    # HIGH_RISK_WRITE always stops here without a persisted, human-decided
    # approval bound to THIS tool call. The check is a database read on purpose
    # -- the approval may have been granted by the API process while the worker
    # was down -- and it is bound to ``tool_call.id``, never ``run.id``, so
    # approving a refund for TX-88219 cannot authorise a proposal for TX-11111
    # (``docs/tool-permissions.md`` §3.1).
    if permission is Permission.HIGH_RISK_WRITE:
        approved = await approval_store.has_approved(call.id)
        if not approved:
            # Persist the awaiting state, create the pending approval and move
            # the run to WAITING_APPROVAL, then raise RunParked. The raise is
            # the last thing that happens.
            await tool_call_store.set_status(
                call.id,
                ToolCallStatus.AWAITING_APPROVAL,
                idempotency_key=idempotency_key,
            )
            await _park_run(
                ctx,
                call,
                recorder=recorder,
                reason=proposal.reason,
                risk_explanation=explain_risk(spec, call.arguments),
                approval_store=approval_store,
                run_store=run_store,
            )

    # ------------------------------------------------------------------
    # EXECUTE
    # ------------------------------------------------------------------
    # Control reaches here only after all five gates passed. This is the single
    # call site of ``gateway.call_tool`` in ``agents/``.
    #
    # The dispatch payload is built explicitly rather than forwarding
    # ``call.arguments`` straight through. The asymmetry is the point: the
    # *model's* proposal cannot carry an ``idempotency_key`` (gate 1's schema --
    # ``RefundArgs`` -- forbids the field, and the MCP server requires it), so
    # the key can only be added *here*, from
    # ``ToolCallRef.idempotency_key``, which gate 4 derived from the run and the
    # intended effect (``docs/tool-permissions.md`` §4). Forwarding only
    # ``call.arguments`` would send the schema fields the model supplied and
    # omit the derived key, so ``billing.issue_refund`` would fail the server's
    # own required-argument check on every dispatch -- and a replay from a
    # replan of the same refund could never land on the same key. Adding the
    # derived key here is what makes the idempotent refund actually engage on
    # the real path.
    dispatch_arguments: dict[str, object] = dict(call.arguments)
    if call.idempotency_key is not None:
        dispatch_arguments["idempotency_key"] = call.idempotency_key
    result: ToolResult = await gateway.call_tool(spec.name, dispatch_arguments)

    if result.ok:
        await tool_call_store.set_status(
            call.id,
            ToolCallStatus.EXECUTED,
            result=result.result,
            idempotency_key=idempotency_key,
        )
    else:
        # A failed execution is still a *recorded* execution attempt with its
        # error; the tool call is FAILED, and the run step reports it. Phase 1
        # does not retry (``docs/agent-state-machine.md`` §6).
        await tool_call_store.set_status(
            call.id,
            ToolCallStatus.FAILED,
            error=result.error or "tool_error",
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # AUDIT -- always, for every permission level including READ
    # ------------------------------------------------------------------
    await recorder.record_audit(
        event_type=AUDIT_TOOL_EXECUTED,
        actor="runtime",
        payload={
            "tool_call_id": str(call.id),
            "tool_name": call.tool_name,
            "permission": permission.value,
            "ok": result.ok,
            "latency_ms": result.latency_ms,
        },
    )

    return _record_for(
        call.id,
        call.tool_name,
        ToolCallStatus.EXECUTED if result.ok else ToolCallStatus.FAILED,
        result.result,
    )


async def _park_run(
    ctx: RunContext,
    call: ToolCallRef,
    *,
    recorder: TraceRecorder,
    reason: str,
    risk_explanation: str,
    approval_store: ApprovalStore,
    run_store: RunStore,
) -> None:
    """Create the pending ``ApprovalRequest``, move the run, then raise ``RunParked``.

    The writes are ordered so the *last* thing that happens is the raise: the
    approval row and the ``EXECUTING -> WAITING_APPROVAL`` transition are
    persisted first, and then control unwinds. ``RunParked`` is control flow, not
    an error -- the worker catches it, releases the row and moves on
    (``docs/agent-state-machine.md`` §1, §3). The approval's
    ``arguments_snapshot`` is the exact arguments the gate will execute, so the
    human approves what actually runs (§3.2).
    """
    # One approval per tool call (the column is UNIQUE, and one approval
    # authorises exactly one call). A resume pass for a call that is already
    # parked must reuse the existing pending row rather than insert a second:
    # otherwise the unique constraint would turn a legitimate re-park into a
    # crash, and a re-created approval would reset a human's in-flight decision.
    existing = await approval_store.get_for_tool_call(call.id)
    if existing is None:
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=ctx.run.id,
            tool_call_id=call.id,
            reason=reason,
            risk_explanation=risk_explanation,
            arguments_snapshot=dict(call.arguments),
            created_at=_utcnow(),
        )
        await approval_store.create(approval)
    await run_store.set_status(ctx.run.id, RunStatus.WAITING_APPROVAL)
    ctx.run.status = RunStatus.WAITING_APPROVAL
    await recorder.record_audit(
        event_type=AUDIT_APPROVAL_REQUESTED,
        actor="runtime",
        payload={
            "tool_call_id": str(call.id),
            "tool_name": call.tool_name,
            "risk_explanation": risk_explanation,
            "arguments": dict(call.arguments),
        },
    )
    raise RunParked(str(ctx.run.id), str(call.id))


async def run_step(
    ctx: RunContext,
    proposal: ProposedAction,
    *,
    gateway: ToolGateway,
    run_store: RunStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
    recorder: TraceRecorder | None = None,
    executed_lookup: ExecutedLookup | None = None,
    tool_call_id: UUID | None = None,
    provider: ModelProvider | None = None,
    orchestrator: Orchestrator | None = None,
) -> RunContext:
    """Execute one proposed tool call through the gates and persist its effects.

    A step that reaches the approval gate raises ``RunParked`` and the run is
    left in ``WAITING_APPROVAL``; that is a supported outcome, not an error.
    ``provider`` and ``orchestrator`` are accepted for symmetry with the run loop
    and are not consulted here -- a step that took a model decision mid-gate
    would be the bug this module exists to prevent.
    """
    _ = (provider, orchestrator)
    trace = recorder if recorder is not None else TraceRecorder(run_id=ctx.run.id)
    record = await _gate_and_execute(
        ctx,
        proposal,
        gateway=gateway,
        run_store=run_store,
        tool_call_store=tool_call_store,
        approval_store=approval_store,
        recorder=trace,
        executed_lookup=executed_lookup,
        tool_call_id=tool_call_id,
    )
    ctx.executed_tool_calls.append(record)
    ctx.proposed_actions.append(proposal)
    ctx.steps_taken += 1
    return ctx


async def run_loop(
    ctx: RunContext,
    proposals: list[ProposedAction] | None = None,
    *,
    provider: ModelProvider,
    gateway: ToolGateway,
    orchestrator: Orchestrator,
    run_store: RunStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
    recorder: TraceRecorder | None = None,
    executed_lookup: ExecutedLookup | None = None,
    resume_tool_call_id: UUID | None = None,
) -> RunContext:
    """Drive a run until it is terminal or parked.

    The worker calls this after ``RunStore.claim_next``. ``WAITING_APPROVAL``
    ends the loop (the worker moves on and the API re-queues the run on
    approval); exceeding ``MAX_STEPS`` fails the run with ``max_steps_exceeded``.

    ``RunParked`` propagates out of here untouched: it is control flow the worker
    handles, and catching it would be the one mistake that turns "the system did
    its job" into "the system failed".

    Args:
        proposals: The proposals to drive through the gates, in order. They are
            passed in rather than read from ``ctx.proposed_actions`` because that
            list is the *log* of what was proposed; ``run_step`` appends to it.
        resume_tool_call_id: On the resume pass after an approval, the id of the
            parked call the human decided. It is threaded to gate 5 so the
            approval is found by the id it was bound to.

    Phase 1's loop is a minimal pump. The state machine
    (``docs/agent-state-machine.md``) remains the specification for ordering; the
    richer classify/retrieve/plan/respond pump lands with the provider milestone.
    """
    _ = (provider, orchestrator)
    max_steps = ctx.max_steps or _DEFAULT_MAX_STEPS

    if ctx.steps_taken >= max_steps:
        await _fail_run(ctx, run_store, recorder=recorder, reason="max_steps_exceeded")
        raise MaxStepsExceeded(str(ctx.run.id), max_steps)

    # The run must be in EXECUTING before a tool touches the world, and the only
    # legal route there is RECEIVED -> CLASSIFYING -> RETRIEVING -> PLANNING ->
    # EXECUTING (``docs/agent-state-machine.md`` §2). Walking the chain rather
    # than jumping is what makes ``PLANNING -> EXECUTING`` the sole edge into a
    # tool: a run that skipped planning is a run nobody asked for.
    for intermediate in (
        RunStatus.CLASSIFYING,
        RunStatus.RETRIEVING,
        RunStatus.PLANNING,
        RunStatus.EXECUTING,
    ):
        if ctx.run.status is intermediate or not ctx.run.can_transition_to(intermediate):
            continue
        ctx.run.status = intermediate
        await run_store.set_status(ctx.run.id, intermediate)

    for index, proposal in enumerate(proposals or []):
        if ctx.steps_taken >= max_steps:
            await _fail_run(ctx, run_store, recorder=recorder, reason="max_steps_exceeded")
            raise MaxStepsExceeded(str(ctx.run.id), max_steps)
        # Only the first proposal of a resume re-enters the parked call's gates;
        # the rest are fresh proposals with their own tool call rows.
        tool_call_id = resume_tool_call_id if index == 0 else None
        await run_step(
            ctx,
            proposal,
            gateway=gateway,
            run_store=run_store,
            tool_call_store=tool_call_store,
            approval_store=approval_store,
            recorder=recorder,
            executed_lookup=executed_lookup,
            tool_call_id=tool_call_id,
        )

    return ctx


def transition_or_raise(run: AgentRun, target: RunStatus) -> None:
    """Move ``run`` to ``target`` in memory, raising ``IllegalTransition`` otherwise.

    The in-memory mirror of ``RunStore.set_status``; kept here so a caller that
    needs the transition checked without a store write (a test, the golden-path
    driver) gets the same error the store would produce.
    """
    if not run.can_transition_to(target):
        raise IllegalTransition(run.status, target)
    run.status = target


async def _fail_run(
    ctx: RunContext,
    run_store: RunStore,
    *,
    recorder: TraceRecorder | None,
    reason: str,
) -> None:
    """Move a run to ``FAILED`` with a machine-stable reason and audit it.

    ``FAILED`` means OpsPilot did not finish the job -- distinct from a rejected
    approval or a policy refusal, which ``COMPLETED`` via ``RESPONDING``
    (``docs/agent-state-machine.md`` §3).
    """
    await run_store.set_status(ctx.run.id, RunStatus.FAILED, failure_reason=reason)
    ctx.run.status = RunStatus.FAILED
    trace = recorder if recorder is not None else TraceRecorder(run_id=ctx.run.id)
    await trace.record_audit(
        event_type=AUDIT_RUN_FAILED,
        actor="runtime",
        payload={"failure_reason": reason},
    )


def registered_tool_names() -> list[str]:
    """The names of every statically registered tool, for the provider's arguments."""
    return sorted(TOOL_REGISTRY)


__all__: list[str] = [
    "AUDIT_APPROVAL_REQUESTED",
    "AUDIT_RUN_FAILED",
    "AUDIT_TOOL_EXECUTED",
    "AUDIT_TOOL_REJECTED",
    "GATE_APPROVAL",
    "GATE_PERMISSION",
    "GATE_POLICY",
    "GATE_REGISTRY",
    "GATE_SCHEMA",
    "ExecutedLookup",
    "ToolCallRef",
    "registered_tool_names",
    "run_loop",
    "run_step",
    "transition_or_raise",
]
