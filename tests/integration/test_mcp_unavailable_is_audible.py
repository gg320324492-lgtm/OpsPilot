"""An unreachable MCP server must be audible: the run dies, but the log must say so.

M12 made a failed tool call impossible to miss in four places (see
``tests/integration/test_failed_tool_call_visibility.py``): the reply prompt
states it, the run escalates, a WARNING line names run/tool/error/permission,
and a ``tool_failed`` audit event records it. ``mcp_unavailable`` was the one
failure that escaped all of that machinery -- not by design, but by position: it
raised out of ``_gate_and_execute`` at a point above both the log line and the
audit block, so the path the docs call "the system could not complete the work it
was asked to do" (``docs/agent-state-machine.md`` §3) left the worker's log
saying only that it booted.

That is a real gap rather than a cosmetic one, and it is worth stating why the
run's own FAILED status does not close it. An MCP outage is not one run's
problem; it is every run in flight at once, and the operator's first move is to
read the log, not the database. A log with one boot line over a stack of failed
runs is the same silence M12 was written to end.

Two decisions are encoded here, and the second is the one a reviewer should
argue with:

1. **A WARNING line is added**, carrying the same four fields in the same order
   as the refusal line, so one grep pattern reaches every failed dispatch. It is
   logged *before* ``_fail_run``: the line names the cause, failing the run is
   the consequence, and a line emitted after a database write is a line that
   write can destroy in the same incident that took the server down.

2. **No ``tool_failed`` audit event is written.** ``tool_failed`` asserts a
   dispatch happened -- the call went out and the tool answered not-ok. Here the
   tool never saw the call at all: the gateway could not reach the transport.
   Recording that under the event name operators query for "which operation
   failed" would count refunds that never left the building, which is the exact
   failure of naming the split from ``tool_executed`` existed to fix. The
   path's records are already exact and unmissable: the ``tool_calls`` row at
   ``failed``, the ``run_failed`` audit event carrying the reason, and the line
   above.

The last test is the negative an operator actually cares about: a successful
call logs nothing at all, so the line cannot become the noise it was raised
above to avoid.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
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
from opspilot.agents.runtime import AUDIT_TOOL_EXECUTED, run_loop
from opspilot.agents.schemas import (
    AgentResponse,
    TicketCategory,
    TicketClassification,
)
from opspilot.agents.state import RunContext
from opspilot.domain.errors import MCPUnavailable
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.ports.model_provider import ModelProvider, ModelResponse, ModelUsage
from opspilot.ports.orchestrator import Orchestrator
from opspilot.ports.stores import RunStore
from opspilot.ports.tool_gateway import ToolGateway, ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder

# The run id the log line must carry, and the permission level the read tool
# carries, so the four-field tuple is asserted rather than assumed.
_RUNTIME_LOGGER = "opspilot.agents.runtime"

_SUBJECT = "Charged twice"
_BODY = "INV-2026-384 was charged twice."
_EMAIL = "billing@acme.example"

_READ_PROPOSAL: dict[str, Any] = {
    "tool_name": "billing.get_invoice",
    "arguments": {"invoice_id": "INV-2026-384"},
    "reason": "confirm the invoice first",
}


class UnreachableGateway:
    """A gateway that reports the server it cannot reach.

    The stand-in for the real thing rather than an invented failure:
    ``mcp_unavailable`` is what ``MCPServerGateway`` itself returns when the
    transport cannot be built, which is the code path this file is about. Copied
    (and trimmed to the one error it needs) from
    ``tests/integration/test_failed_tool_call_visibility.py:RefusingGateway``,
    where the same three error codes are driven; attribution kept so the two
    files can be compared without one having to know about the other.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=False, error="mcp_unavailable")


class WorkingGateway:
    """A gateway that answers. The negative case's other half.

    Without it, "logs nothing on success" could pass against a gateway that
    fails in some new way, and the test would be measuring the wrong silence.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        return ToolResult(tool_name=name, ok=True, result={"ok": True})


class RecordingProvider:
    """A provider that records the prompts it was handed and replies plainly.

    Copied from ``tests/integration/test_failed_tool_call_visibility.py``
    (``RecordingProvider``, same name and same reasoning): the subject under
    test is what the *runtime* recorded, not what a model happens to say, so the
    provider stays deliberately benign and scripts exactly one tool proposal.
    """

    def __init__(self, *, proposal: dict[str, Any] = _READ_PROPOSAL) -> None:
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
    run_store: RunStore | None = None,
) -> RunContext:
    """Drive the real loop against the real stores.

    Copied from ``tests/integration/test_failed_tool_call_visibility.py``
    (``_pump``): nothing is stubbed at the seam the behaviour lives on, so the
    rows and log lines asserted below are ones the production path wrote.
    ``run_store`` is the one seam a caller may swap, for the ordering test that
    needs the fail-the-run write to die.
    """
    return await run_loop(
        _context(run),
        provider=provider,
        gateway=gateway,
        orchestrator=_orchestrator(),
        run_store=run_store if run_store is not None else SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
    )


async def _drain(
    factory: sessionmaker[Session],
    run_id: UUID,
    *,
    provider: ModelProvider,
    gateway: ToolGateway,
) -> bool:
    """Drive the run through the *worker*: claim it, then run it.

    The production path, and the one that matters here -- the worker is what
    suppresses ``MCPUnavailable`` (``worker/loop.py``), so it is where the
    silent-mcp-failure lived. Copied in shape from
    ``tests/integration/test_failed_tool_call_visibility.py:_drain``.
    """
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


def _tool_call_rows(factory: sessionmaker[Session], run_id: UUID) -> list[models.ToolCall]:
    with db.session_scope(factory) as session:
        return list(
            session.execute(select(models.ToolCall).where(models.ToolCall.run_id == run_id))
            .scalars()
            .all()
        )


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


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The runtime's WARNING-and-above records, and only the runtime's.

    Filtered by logger name as well as level: caplog captures everything that
    reaches the root handler, and a warning from an unrelated module would make
    the negative test fail for a reason that has nothing to do with this one.
    """
    return [r for r in caplog.records if r.name == _RUNTIME_LOGGER and r.levelno >= logging.WARNING]


class FailWriteStore(SqlRunStore):
    """``SqlRunStore`` whose one write to ``FAILED`` dies.

    A stand-in for the incident the ordering decision is about: the MCP server
    is unreachable *and* the write that records the run's death fails too --
    which is the same infrastructure, minutes apart. Only the ``FAILED``
    transition is intercepted, so every other transition in the run behaves
    exactly as the real store's does and the test cannot pass for the wrong
    reason.
    """

    async def set_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        if status is RunStatus.FAILED:
            raise RuntimeError("the database went down with the MCP server")
        await super().set_status(
            run_id,
            status,
            failure_reason=failure_reason,
            completed_at=completed_at,
        )


# ---------------------------------------------------------------------------
# 1. The one line that was missing
# ---------------------------------------------------------------------------


async def test_an_unreachable_server_is_logged_at_warning(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """One WARNING, naming the run, the tool, the error and the permission.

    Asserted on the message rather than the record's metadata because the
    message is the artefact an operator greps: "tool call failed: run=...
    tool=billing.get_invoice error=mcp_unavailable permission=read". Exactly one
    line, because a loop that retried or re-planned would turn one outage into
    a storm and teach operators to filter it out.
    """
    run = await _new_run(factory)
    gateway = UnreachableGateway()

    with caplog.at_level(logging.WARNING, logger=_RUNTIME_LOGGER), pytest.raises(MCPUnavailable):
        await _pump(factory, run, provider=RecordingProvider(), gateway=gateway)

    lines = [r.getMessage() for r in _warnings(caplog)]
    assert lines, "an unreachable MCP server must leave a line an operator can see"
    assert len(lines) == 1, f"one failed dispatch is one line; got {lines}"

    line = lines[0]
    assert "billing.get_invoice" in line
    assert "mcp_unavailable" in line
    assert str(run.id) in line, "the line must name the run it belongs to"
    assert f"permission={Permission.READ.value}" in line, (
        "the four-field tuple the refusal line uses is a contract; a reader "
        "should not have to learn a second shape"
    )


async def test_the_worker_path_logs_it_even_though_the_exception_is_swallowed(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """The line survives the worker's ``contextlib.suppress`` -- where it counts.

    This is the production path: ``drain_once`` suppresses ``MCPUnavailable`` so
    the poll loop stays alive, which is also why the missing line was invisible.
    Driving the loop directly (the test above) would pass even if the line were
    only reachable on a path the worker never takes.
    """
    run = await _new_run(factory)
    gateway = UnreachableGateway()

    with caplog.at_level(logging.WARNING, logger=_RUNTIME_LOGGER):
        claimed = await _drain(factory, run.id, provider=RecordingProvider(), gateway=gateway)

    assert claimed is True, "the worker must have claimed and driven the run"
    assert [r.getMessage() for r in _warnings(caplog)], (
        "the worker suppressed the exception AND the log said nothing: the "
        "operator is told neither by the stack nor by the log"
    )


async def test_the_line_survives_the_write_that_failing_the_run_performs(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """The WARNING goes down *before* ``_fail_run``, so it outlives a dead write.

    This is the ordering decision made executable rather than left as prose.
    The fail-the-run write raises -- the run is neither marked failed nor
    explained, the worst state to leave an operator in -- and the line must
    still be there, because it was emitted before the write that died. Move the
    ``logger.warning`` below ``_fail_run`` and this test goes red; keep it above
    and the log still names the run, the tool and the error.

    The store's write is what raises, so the ``MCPUnavailable`` the test above
    sees is replaced here by the database error -- the runtime never gets as far
    as the raise.
    """
    run = await _new_run(factory)

    with caplog.at_level(logging.WARNING, logger=_RUNTIME_LOGGER), pytest.raises(RuntimeError):
        await _pump(
            factory,
            run,
            provider=RecordingProvider(),
            gateway=UnreachableGateway(),
            run_store=FailWriteStore(factory),
        )

    lines = [r.getMessage() for r in _warnings(caplog)]
    assert lines, "the line was lost with the write that followed it"
    assert "billing.get_invoice" in lines[0]
    assert "mcp_unavailable" in lines[0]


# ---------------------------------------------------------------------------
# 2. What must NOT change: the run still fails, the row still records it
# ---------------------------------------------------------------------------


async def test_the_run_still_fails_and_the_row_still_records_the_failure(
    factory: sessionmaker[Session],
) -> None:
    """The log line is additive; the state machine is untouched.

    ``docs/agent-state-machine.md`` §3 and §3.1 are pinned here again from this
    side: ``FAILED`` with ``failure_reason='mcp_unavailable'``, and the
    ``tool_calls`` row at ``failed`` carrying the same error. The row was
    already correct before the fix -- probed, not assumed -- and the fix must
    not disturb it, so it is asserted rather than left implicit.
    """
    run = await _new_run(factory)

    with pytest.raises(MCPUnavailable):
        await _pump(factory, run, provider=RecordingProvider(), gateway=UnreachableGateway())

    failed = await SqlRunStore(factory).get(run.id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.failure_reason == "mcp_unavailable"

    rows = _tool_call_rows(factory, run.id)
    assert len(rows) == 1, "the attempted call must be on the record"
    assert rows[0].status == ToolCallStatus.FAILED.value
    assert rows[0].error == "mcp_unavailable"


async def test_a_successful_call_logs_nothing_at_warning(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """The guard: a clean run stays silent, so the line keeps its meaning.

    A WARNING that fires on every call is a WARNING nobody reads. The run is
    also asserted to have genuinely succeeded, so this cannot pass against a
    run that simply never reached a tool.
    """
    run = await _new_run(factory)
    gateway = WorkingGateway()

    with caplog.at_level(logging.WARNING, logger=_RUNTIME_LOGGER):
        await _pump(factory, run, provider=RecordingProvider(), gateway=gateway)

    assert _warnings(caplog) == [], "a successful call must not warn about anything"

    completed = await SqlRunStore(factory).get(run.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED
    assert completed.failure_reason is None
    rows = _tool_call_rows(factory, run.id)
    assert [r.status for r in rows] == [ToolCallStatus.EXECUTED.value]


# ---------------------------------------------------------------------------
# 3. The audit ledger: run_failed, and deliberately not tool_failed
# ---------------------------------------------------------------------------


async def test_the_ledger_records_the_run_failure_and_not_a_tool_failure(
    factory: sessionmaker[Session],
) -> None:
    """No ``tool_failed`` for a call that was never dispatched.

    This pins the second decision rather than leaving it as a comment someone
    can quietly drop. ``tool_failed`` means "dispatched, and the tool answered
    not-ok"; ``mcp_unavailable`` means the transport was never reached, so the
    tool never saw the call. Writing it as ``tool_failed`` would put a refund
    nobody attempted into the ledger's query for refunds that were attempted
    and refused -- the query ``tool_executed``/``tool_failed`` was split apart
    to make answerable (``docs/agent-state-machine.md`` §3.1).

    What *is* asserted present: the ``run_failed`` event ``_fail_run`` writes,
    carrying the reason, and the absence of ``tool_executed`` for this call. So
    the ledger is not missing the failure -- it is filed under the event whose
    name matches what happened.
    """
    run = await _new_run(factory)

    with pytest.raises(MCPUnavailable):
        await _pump(factory, run, provider=RecordingProvider(), gateway=UnreachableGateway())

    assert _audit_payloads(factory, run.id, "tool_failed") == [], (
        "an undelivered call is not a tool that answered not-ok; recording it "
        "as tool_failed corrupts the one query that event exists to answer"
    )

    failed = _audit_payloads(factory, run.id, "run_failed")
    assert len(failed) == 1, "the run failure must be audited exactly once"
    assert failed[0]["failure_reason"] == "mcp_unavailable"

    executed = _audit_payloads(factory, run.id, AUDIT_TOOL_EXECUTED)
    assert all(p.get("tool_name") != "billing.get_invoice" for p in executed), (
        "nothing executed against a server that was never reached"
    )
