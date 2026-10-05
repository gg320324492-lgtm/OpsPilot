"""The claim-and-drive poll loop.

Responsibility: repeatedly claim the oldest claimable run
(``SELECT ... WHERE status IN (claimable) ORDER BY created_at FOR UPDATE SKIP
LOCKED LIMIT 1``), drive it to a terminal or parked state through the runtime,
and sleep ``WORKER_POLL_INTERVAL`` between polls. On ``RunParked`` the worker
releases the row and moves on -- ``WAITING_APPROVAL`` is the one non-terminal
state it does not hold.

Layer: ``worker``. It depends only on the *ports* (``RunStore``,
``ToolCallStore``, ``ApprovalStore``) and the runtime -- never on
``opspilot.adapters.persistence.models``. The dialect handling for the claim
lives in the store adapter, so this module never branches on Postgres vs SQLite.

Restart behaviour is deliberately shallow: on boot, runs left mid-flight
(``CLASSIFYING``, ``RETRIEVING``, ``PLANNING``, ``EXECUTING``, ``RESPONDING``)
are marked ``FAILED`` with ``failure_reason='interrupted'``; runs in
``WAITING_APPROVAL`` are left alone. Automatic mid-step resume is Phase 2 and is
not pretended here (``docs/architecture.md`` §5).

The two resume situations this loop must recognise, both edge-triggered by the
approvals API flipping the run back to a claimable state (Gap B):

* **approved** -- the run is in ``EXECUTING`` with an ``awaiting_approval`` tool
  call whose approval is now ``approved``. The worker finds that call's id and
  passes it as ``resume_tool_call_id`` so the runtime re-enters the gates for the
  exact call a human decided.
* **rejected** -- the run was flipped straight to ``RESPONDING``. The worker
  drives the reply step with ``responded_without_tool=True``; the run reaches
  ``COMPLETED`` with an escalation reply and **no** tool executes. A rejection is
  the workflow working, not a failure (``docs/agent-state-machine.md`` §3).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from uuid import UUID

from opspilot.agents.runtime import RetrievalCallable, executed_lookup_for, run_loop
from opspilot.agents.state import RunContext
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import RunParked
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import (
    ApprovalStore,
    PendingToolCall,
    RunStore,
    TicketStore,
    ToolCallStore,
)
from opspilot.ports.tool_gateway import ToolGateway
from opspilot.tracing.recorder import TraceRecorder

type RecorderFactory = Callable[[UUID], TraceRecorder]


async def claim_next(
    *,
    worker_id: str,
    run_store: RunStore,
) -> AgentRun | None:
    """Atomically claim the oldest claimable run, or ``None``.

    The claim predicate is the store's business: ``SqlRunStore.claim_next`` uses
    ``FOR UPDATE SKIP LOCKED`` on Postgres and a plain single-threaded ``SELECT``
    on SQLite, and it claims only ``CLAIMABLE`` from ``domain/runs.py`` -- so
    ``WAITING_APPROVAL`` is never claimed, and a new state cannot be picked up
    without a deliberate edit to that table.
    """
    return await run_store.claim_next(worker_id=worker_id)


def sleep(seconds: float) -> Awaitable[None]:
    """Awaitable sleep, factored out so tests can patch it."""
    return asyncio.sleep(seconds)


def mark_interrupted_on_boot(session_factory: object) -> int:
    """Fail mid-flight runs from a previous process; return how many were marked.

    The honest, shallow restart policy (``docs/architecture.md`` §5): a run left
    in a claim-and-work state when the worker died is marked ``FAILED`` with
    ``failure_reason='interrupted'``. ``WAITING_APPROVAL`` is deliberately left
    alone -- those runs are legitimately waiting on a human, and clearing them
    would erase a pending decision. The SQL lives in
    ``adapters.persistence.db.mark_interrupted_runs``; this wrapper is the one
    place the worker reaches for it, so the boot step is called on start rather
    than remembered per entry point.
    """
    from opspilot.adapters.persistence import db  # local: adapter only at boot

    return db.mark_interrupted_runs(session_factory)  # type: ignore[arg-type]


async def _build_context(
    run: AgentRun,
    *,
    ticket_store: TicketStore,
) -> RunContext:
    """Build the run's ``RunContext`` from its persisted ticket.

    The ticket is read through the port, not the ORM: a missing ticket is a
    wiring bug, so an empty subject/body is used rather than crashing the whole
    worker on one malformed row.
    """
    ticket = await ticket_store.get(run.ticket_id)
    if ticket is None:
        return RunContext(
            run=run,
            ticket_subject="",
            ticket_body="",
            customer_email="",
            ticket_id=run.ticket_id,
        )
    return RunContext(
        run=run,
        ticket_subject=ticket.subject,
        ticket_body=ticket.body,
        customer_email=ticket.customer_email,
        ticket_id=ticket.id,
    )


async def _resolve_resume(
    run: AgentRun,
    *,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
) -> tuple[UUID | None, bool]:
    """Decide how to drive a run that was re-queued after an approval decision.

    Returns ``(resume_tool_call_id, responded_without_tool)``.

    * A run in ``EXECUTING`` with an ``awaiting_approval`` call whose approval is
      ``approved`` yields that call's id -- Gap B's discovery. The runtime then
      re-enters the gates for *that* call (gate 5 reads ``has_approved(id)``), so
      the refund actually executes exactly once.
    * A run in ``RESPONDING`` was flipped there by a *rejection*; it yields
      ``(None, True)`` so the runtime composes an escalation reply and nothing
      executes.
    * Anything else yields ``(None, False)`` -- the ordinary pump.
    """
    if run.status not in {RunStatus.EXECUTING, RunStatus.RESPONDING}:
        return None, False

    pending: PendingToolCall | None = await tool_call_store.find_awaiting_approval(run.id)
    if pending is None:
        return None, False

    approval = await approval_store.get_for_tool_call(pending.tool_call_id)
    if approval is None:
        return None, False

    if run.status is RunStatus.EXECUTING and approval.status is ApprovalStatus.APPROVED:
        return pending.tool_call_id, False
    if run.status is RunStatus.RESPONDING and approval.status is ApprovalStatus.REJECTED:
        return None, True
    return None, False


async def drain_once(
    *,
    worker_id: str,
    run_store: RunStore,
    ticket_store: TicketStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
    provider: ModelProvider,
    gateway: ToolGateway,
    orchestrator: Orchestrator,
    recorder_factory: RecorderFactory | None = None,
    retrieval: RetrievalCallable | None = None,
) -> bool:
    """Claim and drive a single run; ``True`` if one was processed.

    The loop body, exposed separately so tests drive the worker deterministically
    without sleeping. Side-effect-free beyond the run itself: it claims one run,
    drives it, and returns. If nothing is claimable it returns ``False`` and
    touches nothing.

    ``recorder_factory`` builds the ``TraceRecorder`` for the claimed run. It is
    a factory rather than an instance because the run id is not known until the
    claim succeeds; when omitted, the runtime builds one lazily from settings.

    ``RunParked`` is caught here -- and only here -- because release-the-row is
    exactly what the worker is for. It is *not* mapped to ``FAILED``.
    """
    run = await claim_next(worker_id=worker_id, run_store=run_store)
    if run is None:
        return False

    ctx = await _build_context(run, ticket_store=ticket_store)
    recorder = recorder_factory(run.id) if recorder_factory is not None else None
    resume_tool_call_id, responded_without_tool = await _resolve_resume(
        run, tool_call_store=tool_call_store, approval_store=approval_store
    )

    with contextlib.suppress(RunParked):
        await run_loop(
            ctx,
            provider=provider,
            gateway=gateway,
            orchestrator=orchestrator,
            run_store=run_store,
            tool_call_store=tool_call_store,
            approval_store=approval_store,
            recorder=recorder,
            executed_lookup=executed_lookup_for(tool_call_store),
            resume_tool_call_id=resume_tool_call_id,
            retrieval=retrieval,
            responded_without_tool=responded_without_tool,
        )
    return True


async def poll_forever(
    *,
    worker_id: str,
    run_store: RunStore,
    ticket_store: TicketStore,
    tool_call_store: ToolCallStore,
    approval_store: ApprovalStore,
    provider: ModelProvider,
    gateway: ToolGateway,
    orchestrator: Orchestrator,
    poll_interval: float,
    recorder_factory: RecorderFactory | None = None,
    retrieval: RetrievalCallable | None = None,
) -> None:
    """Loop: claim, drive, sleep. Never returns under normal operation."""
    while True:
        processed = await drain_once(
            worker_id=worker_id,
            run_store=run_store,
            ticket_store=ticket_store,
            tool_call_store=tool_call_store,
            approval_store=approval_store,
            provider=provider,
            gateway=gateway,
            orchestrator=orchestrator,
            recorder_factory=recorder_factory,
            retrieval=retrieval,
        )
        if not processed:
            await sleep(poll_interval)


__all__: list[str] = [
    "claim_next",
    "drain_once",
    "poll_forever",
    "sleep",
]
