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

**A model that cannot be reached is the upstream's failure, not the worker's.**
Everything a provider SDK raises is caught here and turned into *one* failed
run; nothing propagates to the poll loop, so the process survives and the next
run is drained. The defect this closes was observed live on the Compose stack
with ``MODEL_PROVIDER=anthropic`` and an unreachable gateway: the
``APIConnectionError`` from ``_respond`` escaped ``drain_once`` ->
``poll_forever`` -> ``main()``'s ``poll.result()`` and killed the worker
process; the container restarted, and the boot sweep marked **every** run the
worker had been holding ``FAILED('interrupted')``. One network blip cost the
whole queue, and the reason the operator read -- "interrupted" -- was not the
reason the run died. The full argument -- which layer, which classes, and
why the run is failed rather than retried -- is in the comment on
``_model_failure_reasons`` below.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from functools import cache
from types import ModuleType
from typing import Final
from uuid import UUID

from opspilot.agents.runtime import (
    AUDIT_RUN_FAILED,
    RetrievalCallable,
    executed_lookup_for,
    run_loop,
)
from opspilot.agents.state import RunContext
from opspilot.domain.approvals import ApprovalStatus
from opspilot.domain.errors import MCPUnavailable, RunParked
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.model_provider import ModelProvider
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import (
    ApprovalStore,
    CitationStore,
    PendingToolCall,
    RunStore,
    TicketStore,
    ToolCallStore,
)
from opspilot.ports.tool_gateway import ToolGateway
from opspilot.tracing.recorder import TraceRecorder

type RecorderFactory = Callable[[UUID], TraceRecorder]


def _settings_retrieval_min_score() -> float:
    """``Settings.retrieval_min_score``, so the threshold has one definition."""
    from opspilot.settings import get_settings

    return get_settings().retrieval_min_score


def _settings_max_steps() -> int:
    """``Settings.max_steps``, so the step budget has one definition.

    Same shape and same reason as :func:`_settings_retrieval_min_score` above:
    ``MAX_STEPS`` was declared in ``settings.py`` and threaded through compose,
    and then :func:`_build_context` built a ``RunContext`` without passing it, so
    the dataclass literal in ``agents/state.py`` was the budget a deployment
    actually got. An operator who wrote ``MAX_STEPS=40`` watched a run exhaust 24
    steps and end ``max_steps_exceeded`` -- the value was configured, reachable,
    and consulted by nothing.
    """
    from opspilot.settings import get_settings

    return get_settings().max_steps


# The abstention threshold used when a caller does not supply one. It used to be
# a literal ``0.35`` here, duplicating ``settings.py``; that was a defect, because
# the number then lived in three places -- here, ``agents/runtime.py`` and
# ``settings.py`` -- so lowering it in one left the others stale and a caller
# relying on the default abstained on a threshold the deployment had already
# moved. It is now read from settings, the same reason ``domain/policies.py``
# reads ``refund_ceiling`` there. One knob, one place.
_DEFAULT_MIN_SCORE: float = _settings_retrieval_min_score()


# ---------------------------------------------------------------------------
# The model-failure policy: what survives, and what the run becomes
# ---------------------------------------------------------------------------

#: The ``failure_reason`` for a model that could not be reached, or reached us
#: and was not able to answer: a refused connection, a timeout, a 5xx, a 429.
#: The provider produced no answer the run could reason about.
MODEL_UNAVAILABLE: Final[str] = "model_unavailable"

#: The ``failure_reason`` for a 401/403. The request reached the provider and the
#: provider refused *our identity* -- a wrong, revoked or under-privileged key.
#: Distinct from ``MODEL_UNAVAILABLE`` because the two need different people to
#: act: one is a network, the other is a credential.
MODEL_REJECTED: Final[str] = "model_rejected"

#: The ``failure_reason`` for a reply that did not fit the SDK's own model of its
#: API. In practice this is ``ANTHROPIC_BASE_URL``/``OPENAI_BASE_URL`` pointing at
#: something that is not the provider -- a proxy, a login page, an SSO gateway.
#: Not ``schema_invalid``: that is the *content* of a well-formed answer, and the
#: runtime already owns it.
MODEL_INVALID_RESPONSE: Final[str] = "model_invalid_response"

#: The catch-all for the rest of what an SDK raises. Honest rather than precise:
#: it says "the provider raised an error we do not classify further" and the
#: message carries the detail, where guessing a category would send an operator
#: to look at the wrong thing.
MODEL_ERROR: Final[str] = "model_error"

#: The provider SDKs whose exception classes this module resolves -- both of
#: them, because ``MODEL_PROVIDER`` is a runtime setting and a worker that
#: named only ``anthropic`` would be exactly as broken on an ``openai``
#: deployment. That is the whole point of the setting.
_PROVIDER_SDKS: Final[tuple[str, ...]] = ("anthropic", "openai")

#: The SDK's own root error class, per provider. The two hierarchies are
#: siblings, not relatives -- ``anthropic.AnthropicError`` and
#: ``openai.OpenAIError`` share nothing but a name and ``Exception`` -- so the
#: root has to be named per module.
_SDK_ROOT: Final[dict[str, str]] = {"anthropic": "AnthropicError", "openai": "OpenAIError"}

#: ``(attribute on the SDK module, failure_reason)``. Specific before broad, and
#: resolved through the raised class's own MRO (see ``_model_failure_reason``),
#: so the order here documents intent rather than doing the dispatch.
_SDK_STATUS_REASONS: Final[tuple[tuple[str, str], ...]] = (
    ("APIConnectionError", MODEL_UNAVAILABLE),
    ("APITimeoutError", MODEL_UNAVAILABLE),
    ("APIResponseValidationError", MODEL_INVALID_RESPONSE),
    ("AuthenticationError", MODEL_REJECTED),
    ("PermissionDeniedError", MODEL_REJECTED),
    # Everything else the SDK raises for an answer it did not like: a 400, a 404,
    # a 409, a 429, a 500, a 503, an overload. An HTTP-level answer exists, so
    # this is not a connection failure -- but it is still the provider's, not
    # ours, and it still has to cost exactly one run.
    ("APIError", MODEL_UNAVAILABLE),
)

logger = logging.getLogger(__name__)


def _import_provider_sdk(name: str) -> ModuleType | None:
    """Import a provider SDK, or ``None`` when it is not installed.

    ``importlib.import_module`` rather than a module-level ``import anthropic``:
    the SDKs are optional extras (``pyproject.toml``), this module is imported by
    the whole test suite and by the ``MODEL_PROVIDER=fake`` demo, and a worker
    that selected ``fake`` must not pay to load a client it will never build.
    ``__main__._require_provider_sdk`` reaches for ``find_spec`` for the same
    reason: nothing is loaded unless the deployment actually chose the provider.
    """
    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - depends on the extras installed
        return None


@cache
def _model_failure_reasons() -> dict[type[BaseException], str]:
    """Map each upstream model failure to the reason the run is marked with.

    Built from whichever SDKs are importable, and only when something is about to
    be caught. See ``_import_provider_sdk`` for why it is not built at import
    time.

    What is deliberately **not** in here, and why:

    * ``httpx``. Neither adapter speaks HTTP directly -- both go through their
      SDK, which wraps a transport failure into its own ``APIConnectionError``
      (the incident traceback this policy exists for is exactly that wrapping:
      ``httpx.ConnectError`` -> ``anthropic.APIConnectionError``). Naming a
      library this code never calls would be a claim about behaviour that does
      not exist, and a list that cannot grow is what makes it worth reading.
    * Programming errors -- ``TypeError``, ``AttributeError``, ``KeyError``,
      ``IllegalTransition``. Those must kill the worker, and they do: they fall
      past every class in this mapping and propagate out of ``drain_once``.
      Swallowing them would let the process keep polling in a state that is
      already wrong, which is worse than dying.
    * ``StructuredOutputError`` (``adapters/models/_shared.py``). It is a
      ``RuntimeError`` the adapter raises, not the SDK, and the runtime already
      routes a schema-invalid answer to ``FAILED('schema_invalid')``.
    * ``MissingAPIKeyError``. A configuration fault that no retry or marking can
      resolve; it is not "the model was unreachable", and its correct home is the
      boot check in ``__main__``. Left to kill the process, loudly, as it does
      today.

    **Why the run is failed, not released for a retry.** The obvious alternative
    -- leave the run claimable and let the next poll pick it up -- looks free,
    because ``claim_next`` does not change the status, so a ``PLANNING`` run is
    already re-claimable. It is not free:

    * ``poll_forever`` sleeps only when ``drain_once`` reported nothing to do. A
      run that keeps failing *was* processed, so a gateway that is down for an
      hour becomes a hot loop: claim, fail, claim, fail, at CPU speed, with one
      log line and one failed model call per iteration. Retry-with-backoff inside
      ``drain_once`` instead blocks the *only* consumer from draining every other
      ticket while it waits.
    * A run that is neither failed nor finished is not something an operator can
      act on. ``FAILED`` with a reason that names the upstream is a query and a
      page; a run spinning in ``planning`` with ``failure_reason`` null is
      indistinguishable from a slow worker.
    * Re-driving a run re-plans it from the top. The refund's idempotency key is
      derived from ``(run_id, transaction)`` so a replay cannot double-charge,
      but the *next* proposal is a fresh model call, and a flaky model gives a
      different answer each time. One run, one answer, is the safer default.
    * It is what the system already does on the other side of the port: the
      runtime maps "the tool server could not be reached" to
      ``FAILED('mcp_unavailable')``. "The model server could not be reached" is
      the same class of fault and gets the same terminal answer,
      ``model_unavailable``. Retry-with-backoff for an unreachable upstream is
      Phase 2 work, as ``docs/agent-state-machine.md``'s own table says
      ("MCP server unreachable after retry (Phase 2)"); inventing half of it here
      would be a second, disagreeing policy.

    The cost of this choice, stated plainly: a one-second network blip fails that
    run, where a retry would have completed it. The mitigation is that the reason
    is explicit, the row says which run and why, and the ticket survives -- so a
    re-queue is a decision someone can make rather than a run nobody can find.
    When a retry is built, it belongs around the ``run_loop`` call in
    ``drain_once``, consulting ``_model_failure_reason`` for what is worth
    retrying; nothing here has to move for that.
    """
    reasons: dict[type[BaseException], str] = {}
    for name in _PROVIDER_SDKS:
        module = _import_provider_sdk(name)
        if module is None:  # pragma: no cover - depends on the extras installed
            continue
        for attribute, reason in _SDK_STATUS_REASONS:
            cls = getattr(module, attribute, None)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                reasons[cls] = reason
        # The root is the last entry per module, so an unclassified SDK error
        # (a webhook signature, a credential file, a content filter) is caught
        # and labelled honestly rather than escaping and taking the worker with
        # it -- while every specific class above still wins, because the lookup
        # walks the raised class's own MRO.
        root = getattr(module, _SDK_ROOT[name], None)
        if isinstance(root, type) and issubclass(root, BaseException):
            reasons[root] = MODEL_ERROR
    return reasons


@cache
def _model_failure_errors() -> tuple[type[BaseException], ...]:
    """The classes that mean "the upstream model failed", for the ``except``."""
    return tuple(_model_failure_reasons())


def _model_failure_reason(error: BaseException) -> str:
    """The reason the run is marked with when ``error`` is a caught failure.

    Resolved by walking ``type(error).__mro__`` rather than by comparing against
    each key in turn, so ``anthropic.AuthenticationError`` is found as itself and
    never as its ``APIStatusError``/``APIError`` ancestors -- the specificity is
    in the class hierarchy, which is the SDK's own, not in the order of a list
    this module has to keep sorted.
    """
    reasons = _model_failure_reasons()
    for cls in type(error).__mro__:
        reason = reasons.get(cls)
        if reason is not None:
            return reason
    return MODEL_ERROR  # pragma: no cover - the caller only passes caught classes


async def _mark_failed(
    run: AgentRun,
    *,
    run_store: RunStore,
    reason: str,
    recorder: TraceRecorder | None,
) -> None:
    """Fail ``run`` with a machine-stable reason and audit it.

    The runtime's ``_fail_run`` does this for every failure inside the pump, but
    it takes a ``RunContext`` this module does not have and it is private to
    ``agents/``. This is the same write from the layer that holds the row, which
    is the layer the state machine says decides the run's fate: ``FAILED`` means
    OpsPilot did not finish the job (``docs/agent-state-machine.md`` §3), and a
    run whose model call could not be made did not finish it.

    The audit event is written even when ``recorder_factory`` was omitted, for the
    runtime's own reason: a failure nobody recorded is a failure nobody can count.
    """
    await run_store.set_status(run.id, RunStatus.FAILED, failure_reason=reason)
    trace = recorder if recorder is not None else TraceRecorder(run_id=run.id)
    await trace.record_audit(
        event_type=AUDIT_RUN_FAILED,
        actor="worker",
        payload={"failure_reason": reason},
    )


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

    ``max_steps`` is read from settings here because this is the one place the
    worker constructs a ``RunContext``. It used to be omitted entirely, and
    ``RunContext``'s dataclass default supplied a literal ``24`` -- so
    ``MAX_STEPS`` reached no ``RunContext`` the worker ever built, and the budget
    a run got was fixed in ``src/`` regardless of the deployment. The recorded
    consequence was a real end-to-end run that spent all 24 steps on read calls
    (``get_invoice`` x12, ``list_transactions`` x9, ``issues.create`` x3), never
    proposed ``billing.issue_refund``, and ended ``max_steps_exceeded`` while
    ``MAX_STEPS=40`` sat unread in the ``.env``.

    Both return paths pass it, deliberately: the empty-ticket fallback returns a
    different ``RunContext``, so wiring only the happy path would leave the
    malformed-row path on the stale literal, which is the same defect one branch
    in.
    """
    max_steps = _settings_max_steps()
    ticket = await ticket_store.get(run.ticket_id)
    if ticket is None:
        return RunContext(
            run=run,
            ticket_subject="",
            ticket_body="",
            customer_email="",
            ticket_id=run.ticket_id,
            max_steps=max_steps,
        )
    return RunContext(
        run=run,
        ticket_subject=ticket.subject,
        ticket_body=ticket.body,
        customer_email=ticket.customer_email,
        ticket_id=ticket.id,
        max_steps=max_steps,
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
    citation_store: CitationStore | None = None,
    retrieval_min_score: float | None = None,
) -> bool:
    """Claim and drive a single run; ``True`` if one was processed.

    The loop body, exposed separately so tests drive the worker deterministically
    without sleeping. Side-effect-free beyond the run itself: it claims one run,
    drives it, and returns. If nothing is claimable it returns ``False`` and
    touches nothing.

    ``recorder_factory`` builds the ``TraceRecorder`` for the claimed run. It is
    a factory rather than an instance because the run id is not known until the
    claim succeeds; when omitted, the runtime builds one lazily from settings.

    ``citation_store`` is threaded to the runtime so a run's retrieved hits are
    persisted as citation rows bound to the run. It is injected like
    ``approval_store`` -- a database collaborator, not something this module
    constructs. ``retrieval_min_score`` overrides the runtime's default
    abstention threshold; ``None`` leaves the runtime's own default in place.

    ``RunParked`` is caught here -- and only here -- because release-the-row is
    exactly what the worker is for. It is *not* mapped to ``FAILED``.

    So is every exception a provider SDK raises, which is the subject of
    ``_model_failure_reasons``: the worker survives a model that cannot be
    reached and marks the one run it was driving, rather than dying and letting
    the boot sweep fail the whole queue as "interrupted". A programming error --
    ``TypeError``, ``AttributeError``, ``KeyError`` -- is *not* in that set and
    escapes, by design: a worker running with a broken in-memory state is worse
    than a worker that stopped, and the pin on that is a test.
    """
    run = await claim_next(worker_id=worker_id, run_store=run_store)
    if run is None:
        return False

    ctx = await _build_context(run, ticket_store=ticket_store)
    recorder = recorder_factory(run.id) if recorder_factory is not None else None
    resume_tool_call_id, responded_without_tool = await _resolve_resume(
        run, tool_call_store=tool_call_store, approval_store=approval_store
    )

    min_score = retrieval_min_score if retrieval_min_score is not None else _DEFAULT_MIN_SCORE

    # ``RunParked`` is suppressed: parking is the workflow working, and the
    # point here is to release the row. ``MCPUnavailable`` is suppressed too,
    # but for the opposite reason -- the runtime has *already* marked the run
    # ``FAILED('mcp_unavailable')`` before raising, so letting it escape would
    # crash the poll loop over a failure that is already recorded.
    #
    # That pair is the *inner* suppression and the model failures are the *outer*
    # catch, because they are two kinds of "not mine to crash over": control flow
    # the runtime raised on purpose, and an upstream that could not answer. Both
    # are decided here, at the one layer that holds the row, so both can record
    # what became of it. ``poll_forever`` catches neither -- it has no run id to
    # fail and no reason to write, so a loop-level catch could only log
    # "something went wrong" and re-claim the same broken run.
    try:
        with contextlib.suppress(RunParked, MCPUnavailable):
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
                citation_store=citation_store,
                retrieval_min_score=min_score,
                responded_without_tool=responded_without_tool,
            )
    except _model_failure_errors() as error:
        # A function call in the ``except`` clause, not a module constant: the
        # SDKs are optional extras, and importing them to build one would charge
        # every deployment -- ``fake`` included -- for a client it never builds
        # (see ``_import_provider_sdk``). It is evaluated only when an exception
        # actually arrives here.
        reason = _model_failure_reason(error)
        # Logged *before* the failing write, for the runtime's own reason
        # (``agents/runtime.py``: a line logged after a database write "is a line
        # that write can destroy"). The stakes are the same here and were worse:
        # the worker's log for the entire incident held exactly one line -- the
        # boot line.
        logger.exception(
            "model call failed: run=%s failure_reason=%s",
            run.id,
            reason,
        )
        await _mark_failed(run, run_store=run_store, reason=reason, recorder=recorder)
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
    citation_store: CitationStore | None = None,
    retrieval_min_score: float | None = None,
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
            citation_store=citation_store,
            retrieval_min_score=retrieval_min_score,
        )
        if not processed:
            await sleep(poll_interval)


__all__: list[str] = [
    "claim_next",
    "drain_once",
    "poll_forever",
    "sleep",
]
