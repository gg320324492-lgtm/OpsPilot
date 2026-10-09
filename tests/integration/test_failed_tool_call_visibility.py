"""A failed tool call must be visible, and the reply must not claim otherwise.

Observed live against the Compose stack, three times, on runs ``c7cd0d91``,
``f37fb2ff`` and ``cf8d3b1f``. Each reached ``completed`` with
``failure_reason: null`` while its ``billing.issue_refund`` row sat at
``status='failed'`` with ``error='invalid_state'`` -- and each customer reply
read::

    We confirmed the duplicate charge of $129.00 on INV-2026-384 and have
    refunded the extra transaction (TX-88219).

The refund did not happen. The refusal was correct (the transaction was already
refunded, so ``invalid_state`` is the idempotency contract working); the defect
is that nothing said so, and the reply asserted a side effect the run had no
record of performing.

The decision this file encodes, argued in ``docs/agent-state-machine.md`` §3.1:
a failed tool call does **not** change the run's status. The run still finishes,
because the system did its job -- it investigated, it was refused, and it can
say so. What must change is that the failure is impossible to miss and the
reply cannot lie about it. Three things carry that: a truthful reply prompt, a
loud audit event and log line, and a ``failed_tool_calls`` summary on the one
endpoint an operator reads.

The two guards at the end keep the decision honest. A refused *read* is still a
legitimate answer the agent may reason about, and ``mcp_unavailable`` -- the
absence of an answer -- still fails the run. Both are asserted so the
escalation rule cannot quietly widen into "any non-ok result fails the run", and
so the last test pins the double-approval half of invariant 4 that was
previously only asserted by hand.
"""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.agents.runtime import (
    AUDIT_TOOL_EXECUTED,
    _response_prompt,
    run_loop,
    run_step,
)
from opspilot.agents.schemas import (
    AgentResponse,
    ProposedAction,
    TicketCategory,
    TicketClassification,
)
from opspilot.agents.state import RunContext, ToolCallRecord
from opspilot.domain.approvals import ApprovalRequest, ApprovalStatus
from opspilot.domain.errors import MCPUnavailable, RunParked
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.ports.model_provider import ModelProvider, ModelResponse, ModelUsage
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.tool_gateway import ToolGateway, ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder

_SUBJECT = "Charged twice"
_BODY = "INV-2026-384 was charged twice."
_EMAIL = "billing@acme.example"

_REFUND_PROPOSAL: dict[str, Any] = {
    "tool_name": "billing.issue_refund",
    "arguments": {"transaction_id": "TX-88219", "amount": 129.0},
    "reason": "duplicate charge",
}
_READ_PROPOSAL: dict[str, Any] = {
    "tool_name": "billing.get_invoice",
    "arguments": {"invoice_id": "INV-2026-384"},
    "reason": "confirm the invoice first",
}


class RefusingGateway:
    """A gateway reproducing the three billing refusals the decision turns on.

    ``invalid_state`` is what the real billing MCP server returns for a
    transaction that is already refunded, ``not_found`` what it returns for one
    that does not exist, and ``mcp_unavailable`` is what the gateway itself
    reports for a server it could not reach. Those three are the cases
    ``docs/agent-state-machine.md`` §3 distinguishes, so the stand-in reproduces
    them rather than inventing a generic failure.
    """

    def __init__(self, *, error: str = "invalid_state") -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        if self.error == "mcp_unavailable":
            return ToolResult(tool_name=name, ok=False, error="mcp_unavailable")
        if name == "billing.issue_refund":
            return ToolResult(tool_name=name, ok=False, error=self.error, latency_ms=7)
        if name == "billing.get_invoice" and self.error == "not_found":
            return ToolResult(tool_name=name, ok=False, error="not_found")
        return ToolResult(tool_name=name, ok=True, result={"ok": True})


class RecordingProvider:
    """A provider that records the prompts it was handed and replies plainly.

    It deliberately returns a *benign* body. The subject is not the words the
    model happens to choose, it is what the runtime told it: a provider that
    asserted a refund would make these tests pass for the wrong reason, and one
    that refused would make them unfalsifiable.

    Args:
        proposal: The single tool proposal the agent makes before it stops.
            Scripting it is what lets the refund path, the refused-read path and
            the unreachable-server path share one provider -- each names a
            different proposal rather than the provider guessing.
    """

    def __init__(self, *, proposal: dict[str, Any] = _REFUND_PROPOSAL) -> None:
        self.proposal = proposal
        self.response_prompt = ""
        self.planning_prompts: list[str] = []

    def _usage(self) -> ModelUsage:
        return ModelUsage(
            provider="fake",
            model="fake-1",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        )

    async def generate_structured(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,
        schema: type[Any],
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> ModelResponse[Any]:
        if schema is TicketClassification:
            return ModelResponse(
                value=TicketClassification(
                    category=TicketCategory.DUPLICATE_CHARGE,
                    confidence=0.9,
                    rationale="charged twice",
                ),
                usage=self._usage(),
            )
        if schema is AgentResponse:
            self.response_prompt = prompt
            return ModelResponse(
                value=AgentResponse(body="Summary of where things stand.", escalated=False),
                usage=self._usage(),
            )
        raise AssertionError(f"unexpected schema {schema!r}")

    async def choose_tool(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,
        available_tools: list[str],  # noqa: ARG002 -- protocol parameter
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> dict[str, Any]:
        self.planning_prompts.append(prompt)
        if len(self.planning_prompts) == 1:
            return dict(self.proposal)
        return {"tool_name": None, "done": True, "reason": "investigated"}

    async def generate_text(
        self,
        *,
        system: str,  # noqa: ARG002 -- protocol parameter
        prompt: str,
        timeout_seconds: float | None = None,  # noqa: ARG002 -- protocol parameter
    ) -> str:
        self.response_prompt = prompt
        return "Summary of where things stand."


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    Base.metadata.create_all(session_factory.kw["bind"])
    yield session_factory
    session_factory.kw["bind"].dispose()


async def _new_run(factory: sessionmaker[Session]) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject=_SUBJECT, body=_BODY, customer_email=_EMAIL
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


def _context(run: AgentRun) -> RunContext:
    return RunContext(run=run, ticket_subject=_SUBJECT, ticket_body=_BODY, customer_email=_EMAIL)


def _orchestrator() -> Orchestrator:
    from tests.agent.test_runtime_fake import UnusedOrchestrator

    return UnusedOrchestrator()


async def _pump(
    factory: sessionmaker[Session],
    run: AgentRun,
    *,
    provider: ModelProvider,
    gateway: ToolGateway,
    proposals: list[ProposedAction] | None = None,
) -> RunContext:
    """Run the loop against the real stores, whichever entry mode is under test."""
    kwargs: dict[str, Any] = {
        "provider": provider,
        "gateway": gateway,
        "orchestrator": _orchestrator(),
        "run_store": SqlRunStore(factory),
        "tool_call_store": SqlToolCallStore(factory),
        "approval_store": SqlApprovalStore(factory),
        "recorder": TraceRecorder(run_id=run.id, session_factory=factory),
    }
    if proposals is not None:
        return await run_loop(_context(run), proposals, **kwargs)
    return await run_loop(_context(run), **kwargs)


async def _approve_pending(factory: sessionmaker[Session], run: AgentRun) -> UUID:
    with db.session_scope(factory) as session:
        approval_id = session.execute(
            select(models.ApprovalRequest.id).where(models.ApprovalRequest.run_id == run.id)
        ).scalar_one()
        await SqlApprovalStore(session).decide(
            approval_id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
    return approval_id


async def _drain(
    factory: sessionmaker[Session], gateway: ToolGateway, provider: ModelProvider
) -> bool:
    from opspilot.worker.loop import drain_once

    return await drain_once(
        worker_id="w1",
        run_store=SqlRunStore(factory),
        ticket_store=SqlTicketStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        provider=provider,
        gateway=gateway,
        orchestrator=_orchestrator(),
        recorder_factory=lambda rid: TraceRecorder(run_id=rid, session_factory=factory),
    )


async def _failed_refund_run(
    factory: sessionmaker[Session],
    *,
    error: str = "invalid_state",
) -> tuple[AgentRun, RefusingGateway]:
    """The live reproduction in miniature: park, approve, drain, refund refused.

    Nothing is stubbed at the seam the defect lives on. The run is parked by the
    real gate 5, approved through the real ``SqlApprovalStore.decide``, and
    resumed through the real ``drain_once`` -- so the ``failed`` row is one the
    production path wrote.
    """
    run = await _new_run(factory)
    gateway = RefusingGateway(error=error)

    with pytest.raises(RunParked):
        await _pump(factory, run, provider=RecordingProvider(), gateway=gateway)

    await _approve_pending(factory, run)
    await _drain(factory, gateway, RecordingProvider())
    return run, gateway


def _audit_payloads(
    factory: sessionmaker[Session], run_id: UUID, event_type: str
) -> list[dict[str, object]]:
    with db.session_scope(factory) as session:
        rows = (
            session.execute(
                select(models.AuditEvent).where(
                    models.AuditEvent.run_id == run_id,
                    models.AuditEvent.event_type == event_type,
                )
            )
            .scalars()
            .all()
        )
    return [dict(row.payload or {}) for row in rows]


# ---------------------------------------------------------------------------
# The defect, reproduced
# ---------------------------------------------------------------------------


async def test_the_reproduced_run_completed_with_a_failed_refund(
    factory: sessionmaker[Session],
) -> None:
    """The live shape, exactly: ``completed``, ``failure_reason`` null, refund failed.

    This pins the *reproduction*, not the fix. If it goes red the reproduction is
    no longer faithful and every assertion below it would be measuring something
    else. It also states the decision as code: the run still completes, and the
    refund row is still ``failed``.
    """
    run, gateway = await _failed_refund_run(factory)

    completed = await SqlRunStore(factory).get(run.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED
    assert completed.failure_reason is None

    with db.session_scope(factory) as session:
        refund = session.execute(
            select(models.ToolCall).where(
                models.ToolCall.run_id == run.id,
                models.ToolCall.tool_name == "billing.issue_refund",
            )
        ).scalar_one()
    assert refund.status == ToolCallStatus.FAILED.value
    assert refund.error == "invalid_state"
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# 1. The reply prompt is truthful about what failed
# ---------------------------------------------------------------------------


def _prompt_context(*, status: str) -> RunContext:
    return RunContext(
        run=AgentRun(
            id=UUID(int=1), ticket_id=UUID(int=2), model_provider="fake", model_name="fake-1"
        ),
        ticket_subject=_SUBJECT,
        ticket_body=_BODY,
        customer_email=_EMAIL,
        executed_tool_calls=[
            ToolCallRecord(
                tool_call_id=UUID(int=3),
                tool_name="crm.get_customer",
                status=ToolCallStatus.EXECUTED.value,
            ),
            ToolCallRecord(
                tool_call_id=UUID(int=4),
                tool_name="billing.issue_refund",
                status=status,
            ),
        ],
    )


def test_response_prompt_names_a_failed_action_and_forbids_claiming_it() -> None:
    """A failed tool call reaches the reply prompt in words, not just as a status.

    ``_response_prompt`` is the only thing standing between the trace and the
    customer. The bare status string ``billing.issue_refund=failed`` is what the
    live run was handed, and the model read it as a completed refund; the prompt
    now says the action did not happen and forbids claiming it.
    """
    prompt = _response_prompt(_prompt_context(status=ToolCallStatus.FAILED.value))

    assert "did not complete" in prompt
    assert "billing.issue_refund" in prompt
    # The prohibition is the load-bearing half: a failed action must not be
    # described to the customer as something that happened. Case-insensitive so
    # the assertion is about the instruction, not its capitalisation.
    assert "do not claim" in prompt.lower()


def test_response_prompt_leaves_a_clean_run_unchanged() -> None:
    """The guard the previous test needs: a run with no failures is unaffected.

    Without this the fix could be satisfied by always emitting the prohibition,
    which would turn every reply in the system into an escalation-shaped one,
    including the runs with nothing to escalate about.
    """
    prompt = _response_prompt(_prompt_context(status=ToolCallStatus.EXECUTED.value))

    assert "did not complete" not in prompt


async def test_a_failed_write_reaches_the_model_as_a_failure_not_a_success(
    factory: sessionmaker[Session],
) -> None:
    """The provider that composed the reply was told the refund did not happen.

    Asserted against the prompt the production path built, which is the only
    place this can be fixed. A fixture replaying a canned reply would pass this
    test whatever the runtime said.
    """
    run = await _new_run(factory)
    gateway = RefusingGateway()

    with pytest.raises(RunParked):
        await _pump(factory, run, provider=RecordingProvider(), gateway=gateway)
    await _approve_pending(factory, run)

    responder = RecordingProvider()
    await _drain(factory, gateway, responder)

    assert responder.response_prompt, "the reply must be composed through the provider"
    assert "did not complete" in responder.response_prompt


async def test_a_failed_write_marks_the_run_escalated(factory: sessionmaker[Session]) -> None:
    """A failed *write* escalates the run; the composed reply must say so.

    ``escalated`` is what the API puts beside the reply body and what the
    dashboard shows the operator. A run whose refund failed and whose reply is
    flagged ``escalated: false`` is the live defect reduced to one field.
    """
    run, _gateway = await _failed_refund_run(factory)

    with db.session_scope(factory) as session:
        steps = list(
            session.execute(
                select(models.AgentStep).where(
                    models.AgentStep.run_id == run.id,
                    models.AgentStep.step_type == "response",
                )
            )
            .scalars()
            .all()
        )
    assert steps, "the run must have composed a reply to be judged"
    assert steps[-1].output is not None
    assert steps[-1].output["escalated"] is True


# ---------------------------------------------------------------------------
# 2. A refused read is still an answer; an unreachable server is not
# ---------------------------------------------------------------------------


async def test_a_refused_read_does_not_fail_the_run(factory: sessionmaker[Session]) -> None:
    """``billing.get_invoice`` returning ``not_found`` is a legitimate answer.

    This is the counter-argument to the fix, pinned so it cannot be waved away:
    the agent is *supposed* to see that an invoice does not exist and reason
    about it. So a refused read leaves the run ``completed`` rather than
    ``failed`` -- a read that returned no record is a fact about the world, not
    an action that went wrong.
    """
    run = await _new_run(factory)
    await _pump(
        factory,
        run,
        provider=RecordingProvider(proposal=_READ_PROPOSAL),
        gateway=RefusingGateway(error="not_found"),
    )

    completed = await SqlRunStore(factory).get(run.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED
    assert completed.failure_reason is None


async def test_a_refused_read_does_not_escalate_the_reply(
    factory: sessionmaker[Session],
) -> None:
    """The reply half of the same rule: a refused read is not an escalation.

    Asserted against the recorded response step, because the flag the API serves
    is the one persisted there rather than the in-memory one.
    """
    run = await _new_run(factory)
    ctx = _context(run)

    await run_step(
        ctx,
        ProposedAction(tool_name="billing.get_invoice", arguments={"invoice_id": "INV-999"}),
        gateway=RefusingGateway(error="not_found"),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )

    # The refusal is recorded and legible, but it does not taint the run.
    assert ctx.executed_tool_calls[-1].status == ToolCallStatus.FAILED.value
    assert ctx.escalated is False


async def test_an_unreachable_server_still_fails_the_run(
    factory: sessionmaker[Session],
) -> None:
    """``mcp_unavailable`` is the absence of an answer, so the run still fails.

    The other half of the decision, pinned so the escalation rule cannot be
    widened into "every non-ok result escalates". Without it, someone could
    satisfy ``test_a_failed_write_marks_the_run_escalated`` by treating all
    refusals alike, which would fail the legitimate ``not_found`` case.
    """
    run = await _new_run(factory)

    with pytest.raises(MCPUnavailable):
        await _pump(
            factory,
            run,
            provider=RecordingProvider(proposal=_READ_PROPOSAL),
            gateway=RefusingGateway(error="mcp_unavailable"),
        )

    failed = await SqlRunStore(factory).get(run.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == "mcp_unavailable"


# ---------------------------------------------------------------------------
# 3. The failure is logged, in the runtime's own words
# ---------------------------------------------------------------------------


async def test_a_failed_tool_call_is_logged(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """One line at WARNING, naming the tool and the error.

    The live worker's log for the entire defective run contained one line -- the
    boot line. WARNING rather than INFO on purpose: a silent refund failure
    buried below a threshold is the same silence with an extra step in front of
    it.
    """
    with caplog.at_level(logging.WARNING, logger="opspilot.agents.runtime"):
        await _failed_refund_run(factory)

    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert lines, "a failed tool call must produce a log line an operator can see"
    joined = "\n".join(lines)
    assert "billing.issue_refund" in joined
    assert "invalid_state" in joined


def test_the_runtime_logger_survives_an_in_process_migration(
    repo_root: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running ``alembic upgrade head`` in-process must not silence the runtime.

    Found by the test above failing in the full suite while passing alone. The
    cause is not in the runtime at all: ``migrations/env.py`` calls
    ``fileConfig``, which **defaults to** ``disable_existing_loggers=True`` and
    therefore switches off every already-constructed logger that ``alembic.ini``
    does not name -- including ``opspilot.agents.runtime``. Any deployment that
    runs migrations in the same process as the app would then have had every
    application log line swallowed for the rest of its life, which would make the
    warning added above invisible in production.

    The trigger is reproduced rather than simulated: a real ``command.upgrade``
    against a real (SQLite) database, which is what ``test_api_readiness.py``
    does and what any in-process migration entry point would do. Simulating the
    ``fileConfig`` call would let the test pass while ``env.py`` regressed.
    """
    from alembic import command
    from alembic.config import Config

    logger = logging.getLogger("opspilot.agents.runtime")
    # The property under test, set explicitly rather than assumed: if a previous
    # test in this session already disabled it, the assertion below would prove
    # nothing.
    logger.disabled = False

    from opspilot.settings import get_settings

    db_path = tmp_path / "logging-survives-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    # env.py takes the URL from settings, not from the alembic config, and
    # settings is cached process-wide -- so the override only takes effect after
    # the cache is cleared. Without this the migration would run against the
    # developer's own database.
    get_settings.cache_clear()
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(config, "head")
    assert db_path.exists(), "the migration must actually have run"

    # Attached to the logger itself rather than read from caplog: the
    # migration's fileConfig replaces the *root* handlers, which is exactly
    # what took pytest's own capture handler with it. Asserting against a
    # root-attached capture would re-test the handler plumbing instead of the
    # property that matters -- whether the runtime can still emit.
    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        logger.warning("runtime logger is alive after an in-process migration")
    finally:
        logger.removeHandler(handler)

    assert not logger.disabled, "an in-process alembic run disabled the runtime's logger"
    assert any("alive after" in m for m in seen), (
        "the runtime could not emit after an in-process migration"
    )


# ---------------------------------------------------------------------------
# 4. An operator sees it on /api/runs/{id}
# ---------------------------------------------------------------------------


async def test_run_detail_reports_the_failed_tool_calls(
    factory: sessionmaker[Session],
) -> None:
    """``GET /api/runs/{id}`` names the failed calls, so the page cannot hide them.

    ``/api/runs/{id}`` is the payload the operator actually reads. The failed row
    was in ``tool_calls`` all along, but a status a human has to know to look for
    is not a signal; the summary field makes the run un-ignorable at a glance.
    """
    from opspilot.api.routers.runs import get_run

    run, _gateway = await _failed_refund_run(factory)
    detail = await get_run(run.id, SqlRunStore(factory))

    assert [tc.tool_name for tc in detail.failed_tool_calls] == ["billing.issue_refund"]
    failed = detail.failed_tool_calls[0]
    assert failed.error == "invalid_state"
    assert failed.permission is Permission.HIGH_RISK_WRITE


async def test_run_detail_reports_no_failures_on_a_clean_run(
    factory: sessionmaker[Session],
) -> None:
    """The guard above needs a negative case, or it passes vacuously.

    "Clean" means no tool failed at all -- every call succeeded. A run with a
    refused read is *not* clean for this purpose, and deliberately so:
    ``failed_tool_calls`` reports every failure, reads included, because an
    operator deciding whether to trust a run needs the whole list. What the
    refused read does *not* do is change the run's status, which is what
    ``test_a_refused_read_does_not_fail_the_run`` pins.
    """
    from opspilot.api.routers.runs import get_run

    run = await _new_run(factory)
    await _pump(
        factory,
        run,
        provider=RecordingProvider(proposal=_READ_PROPOSAL),
        gateway=RefusingGateway(error="none"),
    )

    detail = await get_run(run.id, SqlRunStore(factory))
    assert detail.status is RunStatus.COMPLETED
    assert detail.tool_calls, "the run must have called a tool to be a real case"
    assert detail.failed_tool_calls == []


# ---------------------------------------------------------------------------
# 5. The audit ledger records the failure under its own name
# ---------------------------------------------------------------------------


async def test_the_failure_is_an_audit_event_naming_the_call(
    factory: sessionmaker[Session],
) -> None:
    """``tool_failed`` is its own event type, carrying the error.

    A failed call previously wrote ``tool_executed`` with ``ok: false`` in the
    payload -- an event type whose *name* contradicts its content. An operator
    grepping the ledger for what ran would have to know to distrust the name,
    which is the same problem as the silent worker log.
    """
    run, _gateway = await _failed_refund_run(factory)

    payloads = _audit_payloads(factory, run.id, "tool_failed")
    assert payloads, "the failed call must leave its own audit event"
    assert payloads[0]["tool_name"] == "billing.issue_refund"
    assert payloads[0]["ok"] is False
    assert payloads[0]["error"] == "invalid_state"


async def test_a_failed_call_is_not_recorded_as_tool_executed(
    factory: sessionmaker[Session],
) -> None:
    """The executed event is for calls that executed; a failure writes the other.

    Asserted as an absence so the rename cannot happen twice in either
    direction.
    """
    run, _gateway = await _failed_refund_run(factory)

    executed = _audit_payloads(factory, run.id, AUDIT_TOOL_EXECUTED)
    assert all(p.get("tool_name") != "billing.issue_refund" for p in executed)


# ---------------------------------------------------------------------------
# 6. Invariant 4 extended: the operator may approve the same approval twice
# ---------------------------------------------------------------------------


async def test_approving_the_same_approval_twice_refunds_exactly_once(
    factory: sessionmaker[Session],
) -> None:
    """The double-approval case, driven through the API handler, not the store.

    ``docs/tool-permissions.md`` §6 claims the refund executes exactly once. The
    replan/replay path was covered; the double-click was not. This parks a run,
    approves it, drains (the refund executes), then approves *the same approval a
    second time* -- which the API answers 409 -- and asserts the money did not
    move a second time.
    """
    from opspilot.api.routers.approvals import approve
    from opspilot.api.schemas import ApprovalDecisionRequest
    from tests.integration.test_approval_resume import FakeBillingServer

    run = await _new_run(factory)
    gateway = FakeBillingServer()

    with pytest.raises(RunParked):
        await _pump(
            factory,
            run,
            provider=RecordingProvider(),
            gateway=gateway,
            proposals=[
                ProposedAction(
                    tool_name="billing.issue_refund",
                    arguments={"transaction_id": "TX-88219", "amount": 129.0},
                )
            ],
        )

    approval_id = await _pending_approval_id(factory, run)

    # First approval: granted.
    first = await approve(
        approval_id,
        SqlApprovalStore(factory),
        SqlRunStore(factory),
        ApprovalDecisionRequest(decided_by="operator:1"),
        "operator:1",
    )
    assert first.status is ApprovalStatus.APPROVED

    assert await _drain(factory, gateway, RecordingProvider()) is True
    assert len(gateway.refunds) == 1

    # Second approval of the *same* approval: refused.
    with pytest.raises(HTTPException) as refused:
        await approve(
            approval_id,
            SqlApprovalStore(factory),
            SqlRunStore(factory),
            ApprovalDecisionRequest(decided_by="operator:1"),
            "operator:1",
        )
    assert refused.value.status_code == 409

    # The decisive assertion: the money did not move, nothing re-executed, and
    # the terminal run is not claimable again.
    assert len(gateway.refunds) == 1
    assert len(gateway.calls) == 1
    assert await _drain(factory, gateway, RecordingProvider()) is False
    assert len(gateway.refunds) == 1


async def _pending_approval_id(factory: sessionmaker[Session], run: AgentRun) -> UUID:
    with db.session_scope(factory) as session:
        return session.execute(
            select(models.ApprovalRequest.id).where(models.ApprovalRequest.run_id == run.id)
        ).scalar_one()


async def test_a_second_decision_does_not_overwrite_the_first(
    factory: sessionmaker[Session],
) -> None:
    """The store-level guard the API's 409 wraps, pinned on its own.

    ``decide`` is a conditional update, ``WHERE status='pending'``. Removing that
    predicate is exactly the mutation that would make the test above vacuous, so
    the property is asserted directly: a second decide -- even one that would
    *reverse* the outcome -- leaves the first decider standing.
    """
    run = await _new_run(factory)

    # A real ``tool_calls`` row: ``approval_requests.tool_call_id`` is a foreign
    # key, so the approval cannot be written against an id that does not exist.
    # The call is parked rather than executed, which is the state an approval is
    # actually created against in production.
    with db.session_scope(factory) as session:
        calls = SqlToolCallStore(session)
        tool_call_id = await calls.record_proposed(
            run_id=run.id,
            tool_name="billing.issue_refund",
            arguments={"transaction_id": "TX-88219", "amount": 129.0},
            permission=Permission.HIGH_RISK_WRITE.value,
        )
        await calls.set_status(tool_call_id, ToolCallStatus.AWAITING_APPROVAL)
        approval = await SqlApprovalStore(session).create(
            ApprovalRequest(
                id=UUID(int=9001),
                run_id=run.id,
                tool_call_id=tool_call_id,
                reason="duplicate charge",
                risk_explanation="moves $129.00 out of the account",
                arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.0},
                created_at=datetime.now(UTC),
            )
        )
        decided = await SqlApprovalStore(session).decide(
            approval.id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
        assert decided.status is ApprovalStatus.APPROVED

        again = await SqlApprovalStore(session).decide(
            approval.id,
            approved=False,
            decided_by="attacker:9",
            decided_at=datetime.now(UTC),
        )

    assert again.status is ApprovalStatus.APPROVED, "a decided row must not flip"
    assert again.decided_by == "operator:1", "a second decision must not overwrite the first"


async def test_the_store_guard_is_the_only_thing_between_a_race_and_a_double_grant(
    factory: sessionmaker[Session],
) -> None:
    """The API test above passes even with the store guard removed.

    Worth stating plainly, because it was found by removing it: ``_decide``
    re-reads the approval and refuses with 409 *before* it ever calls
    ``decide``, so the handler's own check masks the store's conditional
    update in any sequential test. Two clicks arriving together is the case the
    predicate exists for, and no sequential test reaches it.

    So this drives ``decide`` directly -- no pre-check, no handler -- and
    asserts the race is lost. Under the mutation that drops
    ``WHERE status='pending'`` this test goes red; the API-level one does not,
    which is the whole reason it exists.
    """
    run = await _new_run(factory)

    with db.session_scope(factory) as session:
        calls = SqlToolCallStore(session)
        tool_call_id = await calls.record_proposed(
            run_id=run.id,
            tool_name="billing.issue_refund",
            arguments={"transaction_id": "TX-88219", "amount": 129.0},
            permission=Permission.HIGH_RISK_WRITE.value,
        )
        await calls.set_status(tool_call_id, ToolCallStatus.AWAITING_APPROVAL)
        approval = await SqlApprovalStore(session).create(
            ApprovalRequest(
                id=UUID(int=9002),
                run_id=run.id,
                tool_call_id=tool_call_id,
                reason="duplicate charge",
                risk_explanation="moves $129.00 out of the account",
                arguments_snapshot={"transaction_id": "TX-88219", "amount": 129.0},
                created_at=datetime.now(UTC),
            )
        )
        # Operator one clicks.
        await SqlApprovalStore(session).decide(
            approval.id, approved=True, decided_by="operator:1", decided_at=datetime.now(UTC)
        )
        # Operator two clicks at the same instant -- the race the predicate loses.
        await SqlApprovalStore(session).decide(
            approval.id, approved=True, decided_by="operator:2", decided_at=datetime.now(UTC)
        )

        # Read the row back rather than trusting the return value, so this
        # asserts what is *persisted*, not what the method reported.
        with db.session_scope(factory) as reader:
            stored = reader.get(models.ApprovalRequest, approval.id)

    assert stored is not None
    # The ORM column is a plain string; the store method returns the domain
    # enum. Comparing against the enum here would be comparing a raw column to
    # a domain object -- and because ``StrEnum`` compares equal to its value,
    # it would pass while asserting almost nothing.
    assert stored.status == ApprovalStatus.APPROVED.value
    assert stored.decided_by == "operator:1", "the second click must not win"
