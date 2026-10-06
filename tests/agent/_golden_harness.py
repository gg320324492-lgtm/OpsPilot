"""Shared harness for the M6 end-to-end tests.

Why a harness rather than a fixture
-----------------------------------
The M6 tests must make one assertion that cannot be satisfied by a
self-consistent implementation: **ask the MCP server whether the money moved.**
``.scratch/verify_m4.py`` states why, and it is the single most important habit
in this repository: three times a green suite agreed with the implementation and
disagreed with reality. A run's own ``COMPLETED`` status proves nothing about
whether a refund row exists; the billing server's transaction row is what
happened.

So every scenario in this package is built the same way:

* the **real** ``Sql*`` stores over a fresh SQLite schema;
* the **real** ``MCPToolGateway`` over in-process MCP servers on a private
  ``tmp_path`` store -- so a refund actually moves money in a document only this
  test can see, and a second test cannot pass on a leftover row;
* the **real** worker loop, driven through ``drain_once`` with every dependency
  injected, exactly as production assembles it;
* the **real** ``FakeModelProvider`` replaying a committed fixture, so the
  model's behaviour is scripted rather than invented;
* the **real** retrieval stack over the committed ``knowledge/`` corpus, so the
  citations asserted are the ones the README names.

``Lambda Gateway`` is deliberately absent. A stub that accepts any arguments is
what let a gate-1/server argument divergence survive four milestones: the stub
agreed with whatever the gate sent. Every call here goes to a real server.

The harness is a plain module (not a ``conftest.py``) because
``test_golden_path.py`` and ``test_readme_scenarios.py`` are two files that must
share it, and pytest fixtures live in conftest by convention. The helpers are
importable by both without any collection-order dependency.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.models.fake import FakeModelProvider
from opspilot.adapters.orchestration.linear import LinearOrchestrator
from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlCitationStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.adapters.wiring import RetrievalStack, build_retrieval_stack
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import ToolSpec
from opspilot.ports.tool_gateway import ToolGateway, ToolResult
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder
from opspilot.worker import loop as worker_loop

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"

# The README's ticket, verbatim. ``README.md`` "The golden path" quotes it as a
# two-line string; the body below is one sentence pair and the subject is its
# short form. ``subject + "\\n" + body`` is what the runtime sends to retrieval
# (``agents/runtime.py`` ``_retrieve``), so the retrieval query these tests
# exercise is the README's question, not a paraphrase of it.
GOLDEN_PATH_SUBJECT = "We were charged twice for invoice INV-2026-384."
GOLDEN_PATH_BODY = "Please investigate and fix it."

# The seeded ACME duplicate charge. Both rows are ``charged`` in
# ``mcp_servers/billing/seed.json``; the golden path refunds the second one.
INVOICE_ID = "INV-2026-384"
DUPLICATE_TRANSACTION = "TX-88219"
KEPT_TRANSACTION = "TX-88218"
REFUND_AMOUNT = 129.00
CUSTOMER_ID = "CUS-1001"

# ``docs/milestones.md`` §M6: "the citations are the two expected documents".
# ``knowledge/README.md`` says why both are needed: "a question about a $129
# enterprise duplicate needs both" -- the SOP governs the amount, the refund
# policy's $100 rule is why a human must approve it.
GOLDEN_PATH_CITATIONS = frozenset({"refund-policy.md", "duplicate-charge-sop.md"})


class Harness:
    """One end-to-end scenario: real stores, real gateway, real worker.

    Assembled by :func:`build_harness`. The attributes are the collaborators a
    test needs to assert on; ``drain`` and ``server_state`` are the two verbs
    that matter.
    """

    def __init__(
        self,
        *,
        factory: sessionmaker[Session],
        gateway: MCPToolGateway,
        stack: RetrievalStack,
        provider: FakeModelProvider,
        store_dir: pathlib.Path,
        retrieval_min_score: float,
    ) -> None:
        self.factory = factory
        self.gateway = gateway
        self.stack = stack
        self.provider = provider
        self.store_dir = store_dir
        self.retrieval_min_score = retrieval_min_score

        self.tickets = SqlTicketStore(factory)
        self.runs = SqlRunStore(factory)
        self.calls = SqlToolCallStore(factory)
        self.approvals = SqlApprovalStore(factory)
        self.citations = SqlCitationStore(factory)

        # A fresh provider per run is deliberate: the fake advances a per-method
        # cursor, so a replayed scenario from the same instance would answer the
        # second run with the first run's remaining script.
        self._orchestrator = LinearOrchestrator()

    # -- worker -----------------------------------------------------------

    async def drain_with(
        self,
        gateway: ToolGateway,
        *,
        provider: FakeModelProvider | None = None,
        worker_id: str = "w-1",
    ) -> bool:
        """Drain once with a *different* gateway or provider.

        Used by the two failure scenarios, where the point is to vary exactly
        one collaborator: the "MCP server down" test swaps in a gateway that
        cannot reach the billing server, and the "re-run after interruption"
        test swaps in a fresh model provider because the fake advances a
        per-method cursor and a replay from a spent instance would answer with
        the wrong part of the script.
        """
        if provider is not None:
            self.provider = provider
        processed: bool = await worker_loop.drain_once(
            worker_id=worker_id,
            run_store=self.runs,
            ticket_store=self.tickets,
            tool_call_store=self.calls,
            approval_store=self.approvals,
            provider=self.provider,
            gateway=gateway,
            orchestrator=self._orchestrator,
            recorder_factory=lambda run_id: TraceRecorder(
                run_id=run_id, session_factory=self.factory
            ),
            retrieval=self.stack.retrieval,
            citation_store=self.citations,
            retrieval_min_score=self.retrieval_min_score,
        )
        return processed

    async def drain(self, *, worker_id: str = "w-1") -> bool:
        """Drive the **real** worker loop once. ``True`` if it claimed a run.

        Every dependency is injected, mirroring production assembly: a stub
        gateway or an in-memory run store would make the golden path a story
        rather than a test.

        ``retrieval_min_score`` is passed explicitly from **settings**, and that
        is deliberate. ``drain_once`` falls back to a module constant
        ``_DEFAULT_MIN_SCORE = 0.35``, but the shipped configuration is
        ``RETRIEVAL_MIN_SCORE = 0.22`` (``settings.py``), and the golden path's
        top hit scores 0.3355. At 0.35 the run **abstains**: it reaches
        ``COMPLETED`` with zero tool calls, zero citations and no refund -- the
        customer's duplicate charge is never investigated, and the run's own
        status looks like success. The production entry point (``worker/
        __main__.py``) is the piece that should pass the setting through; it is
        still a ``NotImplementedError`` stub, so the harness passes it explicitly
        rather than inheriting a threshold no shipped query can clear.
        """
        processed: bool = await worker_loop.drain_once(
            worker_id=worker_id,
            run_store=self.runs,
            ticket_store=self.tickets,
            tool_call_store=self.calls,
            approval_store=self.approvals,
            provider=self.provider,
            gateway=self.gateway,
            orchestrator=self._orchestrator,
            recorder_factory=lambda run_id: TraceRecorder(
                run_id=run_id, session_factory=self.factory
            ),
            retrieval=self.stack.retrieval,
            citation_store=self.citations,
            retrieval_min_score=self.retrieval_min_score,
        )
        return processed

    async def start_run(
        self,
        *,
        subject: str = GOLDEN_PATH_SUBJECT,
        body: str = GOLDEN_PATH_BODY,
        customer_email: str = "billing@acme.example",
        provider: FakeModelProvider | None = None,
    ) -> AgentRun:
        """Insert a ticket and its ``RECEIVED`` run, the way the API does."""
        if provider is not None:
            self.provider = provider
        ticket_id = await self.tickets.create(
            subject=subject, body=body, customer_email=customer_email
        )
        return await self.runs.create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )

    # -- the MCP server's own answer -------------------------------------

    async def server_transactions(self, invoice_id: str = INVOICE_ID) -> list[dict[str, Any]]:
        """Ask the billing server what it holds for ``invoice_id``.

        The assertion the whole milestone turns on. It goes through the real
        gateway to the real server, so it reports what the server actually
        wrote -- not what the run believes it did.
        """
        result = await self.gateway.call_tool(
            "billing.list_transactions", {"invoice_id": invoice_id}
        )
        assert result.ok is True, f"could not read the server's state: {result}"
        payload = result.result or {}
        rows = payload.get("transactions", [])
        assert isinstance(rows, list)
        return [dict(row) for row in rows]

    async def refunded_transaction_ids(self, invoice_id: str = INVOICE_ID) -> list[str]:
        """The transaction ids the **server** reports as refunded."""
        rows = await self.server_transactions(invoice_id)
        return [str(r["transaction_id"]) for r in rows if r.get("status") == "refunded"]

    def refund_rows_in_store(self) -> list[dict[str, Any]]:
        """The refund rows the server wrote to its own document.

        A second, independent witness: the transaction's status could in
        principle be updated without a refund row existing, so a claim of
        "exactly one refund" is checked against both.

        **No file means no refunds.** The billing store is written only on a
        mutation, so a document that does not exist yet is the server's honest
        "I have changed nothing" and reads as zero rows. Returning ``[]`` rather
        than asserting the file exists is what lets the *pre-approval* control
        use this method: before a human approves, the correct answer genuinely is
        that the server's store has never been written.
        """
        path = self.store_dir / "billing.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        refunds = data["refunds"]
        assert isinstance(refunds, list)
        return [dict(row) for row in refunds]

    # -- persistence ------------------------------------------------------

    def tool_calls(self, run_id: UUID) -> list[models.ToolCall]:
        """Every ``ToolCall`` row for a run, oldest first."""
        with db.session_scope(self.factory) as session:
            rows = session.execute(
                select(models.ToolCall)
                .where(models.ToolCall.run_id == run_id)
                .order_by(models.ToolCall.created_at)
            ).scalars()
            return list(rows)

    def approval_rows(self, run_id: UUID) -> list[models.ApprovalRequest]:
        """Every ``ApprovalRequest`` row for a run."""
        with db.session_scope(self.factory) as session:
            rows = session.execute(
                select(models.ApprovalRequest).where(models.ApprovalRequest.run_id == run_id)
            ).scalars()
            return list(rows)

    async def citation_slugs(self, run_id: UUID) -> set[str]:
        """The document slugs cited by a run, as persisted rows."""
        return {row.document for row in await self.citations.list_citations(run_id)}

    async def decide(self, approval_id: UUID, *, approved: bool) -> None:
        """Record a human decision the way the approvals API does.

        ``SqlApprovalStore.decide`` is also responsible for flipping the run back
        to a claimable state -- the run must be re-claimable for the worker to
        pick it up again. That is the API's real decision path, not a test
        shortcut.
        """
        await self.approvals.decide(
            approval_id,
            approved=approved,
            decided_by="admin",
            decided_at=dt.datetime.now(dt.UTC),
        )

    async def pending_approval(self, run_id: UUID) -> models.ApprovalRequest:
        """The run's pending approval. Fails loudly if there is not exactly one."""
        rows = [r for r in self.approval_rows(run_id) if r.status == "pending"]
        assert len(rows) == 1, f"expected exactly one pending approval, got {len(rows)}"
        return rows[0]

    # -- the trace --------------------------------------------------------

    def steps(self, run_id: UUID) -> list[models.AgentStep]:
        """Every ``AgentStep`` for a run, in server order (``sequence``)."""
        with db.session_scope(self.factory) as session:
            rows = session.execute(
                select(models.AgentStep)
                .where(models.AgentStep.run_id == run_id)
                .order_by(models.AgentStep.sequence)
            ).scalars()
            return list(rows)

    def audit_events(self, run_id: UUID) -> list[models.AuditEvent]:
        """Every ``AuditEvent`` for a run, oldest first."""
        with db.session_scope(self.factory) as session:
            rows = session.execute(
                select(models.AuditEvent)
                .where(models.AuditEvent.run_id == run_id)
                .order_by(models.AuditEvent.created_at, models.AuditEvent.id)
            ).scalars()
            return list(rows)

    def state_changes(self, run_id: UUID) -> list[tuple[str, str]]:
        """The ``(from, to)`` status pairs the trace recorded.

        This is the run's actual transition sequence, read back from the rows
        rather than reconstructed from what the test expected -- so an
        implementation that reached the right end state by an illegal route is
        still caught.
        """
        changes: list[tuple[str, str]] = []
        for step in self.steps(run_id):
            if step.step_type != "state_change":
                continue
            output = step.output or {}
            changes.append((str(output.get("from_status")), str(output.get("to_status"))))
        return changes

    async def status_of(self, run: AgentRun) -> RunStatus:
        """The run's current status, failing loudly if the row is missing.

        ``SqlRunStore.get`` returns ``AgentRun | None``. Every caller wants the
        row -- a vanished run is a bug, not a state to assert on -- so this
        raises through an assert rather than making each call site branch.
        """
        found = await self.runs.get(run.id)
        assert found is not None, f"run {run.id} is missing from the store"
        return found.status

    async def failure_reason_of(self, run: AgentRun) -> str | None:
        """The run's ``failure_reason``, or ``None``."""
        found = await self.runs.get(run.id)
        assert found is not None, f"run {run.id} is missing from the store"
        reason: str | None = found.failure_reason
        return reason

    async def reload(self, run: AgentRun) -> AgentRun:
        """The run row, asserted present."""
        found = await self.runs.get(run.id)
        assert found is not None, f"run {run.id} is missing from the store"
        return found

    def step_types(self, run_id: UUID) -> list[str]:
        """The step types in order, for completeness assertions."""
        return [s.step_type for s in self.steps(run_id)]


def provider_positioned_at(*, after_structured: int) -> FakeModelProvider:
    """A ``FakeModelProvider`` whose ``generate_structured`` cursor is advanced.

    The fake answers a *scenario* in recorded order, with a separate cursor per
    method kind. A resumed run (``worker/loop.py::_resume``) re-enters the gates
    for the approved call and then goes straight to ``_respond`` -- it does **not**
    re-classify. So a restarted worker's provider must be at
    ``generate_structured`` index 1 (the recorded ``AgentResponse``), not 0 (the
    ``TicketClassification``), or the fake hands the response schema a
    classification payload and fails loudly with a validation error.

    This is a property of the fake's replay semantics, not of the run: the run is
    doing exactly the right thing by not classifying twice.
    """
    provider = FakeModelProvider(scenario="duplicate_charge")
    if after_structured > 0:
        provider._scenario_cursors["generate_structured"] = after_structured
    return provider


class UnreachableGateway:
    """A gateway whose every call fails the way a down MCP server does.

    Models the *transport* failure the real adapter reports
    (``MCPToolGateway.call_tool`` catches any transport exception and returns
    ``ToolResult(ok=False, error="mcp_unavailable")`` -- errors are results,
    never raises, so ``agent-state-machine.md`` §3 can name the reason). It
    deliberately does **not** raise, because the runtime is written against that
    contract and a gateway that raised would be testing a different adapter.

    Used by the "MCP server down" scenario to take the billing server away
    without touching the retrieval path or the model -- so the run fails for the
    stated reason and nothing else changes.
    """

    def __init__(self, reason: str = "mcp_unavailable") -> None:
        self.reason = reason
        self.attempts: list[str] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Refuse the call as an unreachable server would."""
        _ = arguments  # the call never gets far enough to use them
        self.attempts.append(name)
        return ToolResult(
            tool_name=name,
            ok=False,
            error=self.reason,
            result={"code": self.reason, "message": "MCP server is unreachable"},
            latency_ms=0,
        )

    async def list_tools(self) -> list[ToolSpec]:
        """No tools are reachable."""
        return []


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


@pytest.fixture
async def harness(factory: sessionmaker[Session], tmp_path: pathlib.Path) -> AsyncIterator[Harness]:
    """A fully wired scenario: real stores, real MCP servers, real retrieval.

    The retrieval stack is **reindexed over the committed corpus** rather than
    mocked, because §M6's citation criterion is about the documents the README
    names. A mocked retriever would let the golden path pass with whatever
    citations the mock returned -- the assertion would be testing the mock.
    """
    store_dir = tmp_path / "mcp"
    store_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    stack = build_retrieval_stack(settings, session_factory=factory)
    await stack.reindex_runner(KNOWLEDGE_DIR)

    built = Harness(
        factory=factory,
        gateway=MCPToolGateway(servers=build_in_process_servers(store_dir)),
        stack=stack,
        provider=FakeModelProvider(scenario="duplicate_charge"),
        store_dir=store_dir,
        retrieval_min_score=settings.retrieval_min_score,
    )
    yield built


def state_sequence(run: AgentRun) -> list[str]:
    """The run's status as a string, for readable assertion messages."""
    return [run.status.value]


__all__ = [
    "CUSTOMER_ID",
    "DUPLICATE_TRANSACTION",
    "GOLDEN_PATH_BODY",
    "GOLDEN_PATH_CITATIONS",
    "GOLDEN_PATH_SUBJECT",
    "INVOICE_ID",
    "KEPT_TRANSACTION",
    "KNOWLEDGE_DIR",
    "REFUND_AMOUNT",
    "Harness",
    "RunStatus",
    "UnreachableGateway",
    "factory",
    "harness",
    "provider_positioned_at",
    "state_sequence",
]
