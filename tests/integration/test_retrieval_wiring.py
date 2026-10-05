"""Retrieval is wired into the runtime: citations persist, abstention escalates.

Written from the specification:

- ``docs/milestones.md`` §M5: "Retrieval results carry ``(document, chunk, score,
  rank)`` and are persisted as ``Citation`` rows"; and "Below
  ``RETRIEVAL_MIN_SCORE`` -> the run escalates".
- ``docs/api-contract.md`` §3: a completed run's ``citations[]`` carries
  ``document``, ``chunk``, ``score``, ``rank`` -- the run-detail router loads them
  through ``list_citations(run_id)``.
- ``docs/agent-state-machine.md`` §2/§3: ``RETRIEVING -> RESPONDING`` is **not** a
  legal edge; the legal path for a weak-evidence run is
  ``RETRIEVING -> PLANNING -> RESPONDING`` (the "no tool needed" edge), and §3
  says "Knowledge insufficient to answer -> ``COMPLETED`` (via ``RESPONDING``) --
  Abstention is a supported outcome, not an error".

The pump is driven with the scripted provider and a recording gateway, so nothing
does model or transport I/O; the assertions are on the rows the runtime wrote.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import (
    SqlApprovalStore,
    SqlCitationStore,
    SqlKnowledgeDocumentStore,
    SqlRunStore,
    SqlTicketStore,
    SqlToolCallStore,
)
from opspilot.adapters.retrieval.chunking import split_document
from opspilot.adapters.retrieval.memory_store import derive_chunk_id
from opspilot.agents.runtime import run_loop
from opspilot.agents.state import RunContext
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.ports.vector_store import SearchHit
from opspilot.settings import Settings
from opspilot.tracing.recorder import TraceRecorder
from tests.agent.test_runtime_fake import (
    RecordingGateway,
    ScriptedProvider,
    UnusedOrchestrator,
)

# A document whose chunks the citation store can resolve: the foreign keys on
# ``citations`` require the document and chunk rows to exist first
# (``docs/data-model.md`` §2), so the fixture writes them.
_DOC_SOURCE = "refund-policy.md"
_DOC_BODY = (
    "# Refund Policy\n\n## Limits\n\nRefunds above $100 need approval.\n\n"
    "## Scope\n\nApplies to every plan tier.\n"
)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


async def _seed_indexed_document(factory: sessionmaker[Session]) -> None:
    """Write the document and its chunk rows so citations can resolve."""
    document_id = await SqlKnowledgeDocumentStore(factory).upsert_document(
        source=_DOC_SOURCE,
        title="Refund Policy",
        content=_DOC_BODY,
        metadata={"owner": "Finance"},
        content_hash="deadbeef",
    )
    with db.session_scope(factory) as session:
        for chunk in split_document(_DOC_BODY):
            session.add(
                models.KnowledgeChunk(
                    id=derive_chunk_id(document_id, chunk.ordinal),
                    document_id=document_id,
                    ordinal=chunk.ordinal,
                    anchor=chunk.anchor,
                    heading_path=chunk.heading_path,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    embedding=None,
                )
            )


async def _make_run(factory: sessionmaker[Session]) -> AgentRun:
    with db.session_scope(factory) as session:
        ticket_id = await SqlTicketStore(session).create(
            subject="Duplicate charge on INV-2026-384",
            body="We were charged twice for the same invoice.",
            customer_email="billing@acme.example",
        )
        return await SqlRunStore(session).create(
            ticket_id=ticket_id, model_provider="fake", model_name="fake-1"
        )


def _hit(score: float, rank: int, anchor: str) -> SearchHit:
    """A retrieval hit for the seeded document."""
    return SearchHit(
        chunk_id=UUID(int=rank),
        document_id=UUID(int=99),
        document_slug=_DOC_SOURCE,
        anchor=anchor,
        content="Refunds above $100 need approval.",
        score=score,
        rank=rank,
    )


async def _drive(
    factory: sessionmaker[Session],
    run: AgentRun,
    *,
    hits: list[SearchHit],
    min_score: float,
    gateway: RecordingGateway | None = None,
    provider: ScriptedProvider | None = None,
) -> RunContext:
    """Run the pump with a retrieval callable returning ``hits``."""
    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge on INV-2026-384",
        ticket_body="We were charged twice for the same invoice.",
        customer_email="billing@acme.example",
    )

    async def _retrieval(_query: str) -> list[SearchHit]:
        return hits

    return await run_loop(
        ctx,
        provider=provider or ScriptedProvider(reply="Your refund has been processed."),
        gateway=gateway or RecordingGateway(),
        orchestrator=UnusedOrchestrator(),
        run_store=SqlRunStore(factory),
        tool_call_store=SqlToolCallStore(factory),
        approval_store=SqlApprovalStore(factory),
        recorder=TraceRecorder(run_id=run.id, session_factory=factory),
        citation_store=SqlCitationStore(factory),
        retrieval=_retrieval,
        retrieval_min_score=min_score,
    )


# ---------------------------------------------------------------------------
# Citations persist (docs/milestones.md §M5, docs/api-contract.md §3)
# ---------------------------------------------------------------------------


async def test_retrieved_hits_persist_as_citations_bound_to_the_run(
    factory: sessionmaker[Session],
) -> None:
    """A run's retrieved hits become ``Citation`` rows the router can read.

    ``docs/api-contract.md`` §3: the completed run's ``citations[]`` carries
    ``document``, ``chunk`` (``"{slug}#{anchor}"``), ``score`` and ``rank``,
    loaded via ``list_citations(run_id)``. This asserts the exact router shape --
    a citation is *structural*, built from ids, never from model prose.
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    await _drive(
        factory,
        run,
        hits=[_hit(0.91, 1, "limits"), _hit(0.72, 2, "scope")],
        min_score=0.35,
    )

    rows = await SqlCitationStore(factory).list_citations(run.id)

    assert [r.rank for r in rows] == [1, 2], "citations must come back best rank first"
    assert rows[0].document == _DOC_SOURCE
    assert rows[0].chunk == f"{_DOC_SOURCE}#limits"
    assert rows[0].score == pytest.approx(0.91)


async def test_citations_are_written_only_for_a_completed_run(
    factory: sessionmaker[Session],
) -> None:
    """The run reaches ``COMPLETED`` with citations attached."""
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    ctx = await _drive(factory, run, hits=[_hit(0.9, 1, "limits")], min_score=0.35)

    assert ctx.run.status is RunStatus.COMPLETED
    rows = await SqlCitationStore(factory).list_citations(run.id)
    assert len(rows) == 1


async def test_no_citation_store_wired_writes_no_rows(
    factory: sessionmaker[Session],
) -> None:
    """With no ``citation_store`` injected, retrieval still runs and writes nothing.

    The reason no citation exists is that no store was wired -- not that retrieval
    found nothing. This pins that the wiring is what creates citations, so a run
    with hits and no store is distinguishable in the test from the honest
    empty-retrieval case.
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    ctx = RunContext(
        run=run,
        ticket_subject="Duplicate charge",
        ticket_body="charged twice",
        customer_email="a@example.com",
    )

    async def _retrieval(_query: str) -> list[SearchHit]:
        return [_hit(0.9, 1, "limits")]

    with db.session_scope(factory) as session:
        await run_loop(
            ctx,
            provider=ScriptedProvider(),
            gateway=RecordingGateway(),
            orchestrator=UnusedOrchestrator(),
            run_store=SqlRunStore(session),
            tool_call_store=SqlToolCallStore(session),
            approval_store=SqlApprovalStore(session),
            recorder=TraceRecorder(run_id=run.id, session_factory=factory),
            retrieval=_retrieval,
            retrieval_min_score=0.35,
        )

    with factory() as session:
        count = session.execute(select(models.Citation)).scalars().all()
    assert count == []


# ---------------------------------------------------------------------------
# Abstention escalates (docs/milestones.md §M5, docs/agent-state-machine.md §3)
# ---------------------------------------------------------------------------


async def test_below_threshold_retrieval_escalates_via_responding(
    factory: sessionmaker[Session],
) -> None:
    """Weak evidence makes the run escalate and complete, not answer.

    ``docs/milestones.md`` §M5: below ``RETRIEVAL_MIN_SCORE`` "the run escalates
    rather than answering from weak evidence". ``docs/agent-state-machine.md`` §3:
    that outcome is ``COMPLETED`` through ``RESPONDING``. There is no
    ``RETRIEVING -> RESPONDING`` edge, so the run takes the legal
    ``RETRIEVING -> PLANNING -> RESPONDING`` path.
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    ctx = await _drive(factory, run, hits=[_hit(0.10, 1, "limits")], min_score=0.35)

    assert ctx.run.status is RunStatus.COMPLETED, (
        "an abstention is 'COMPLETED via RESPONDING', not FAILED (docs/agent-state-machine.md §3)"
    )
    assert ctx.escalated is True, "the reply must be an escalation, not an answer"
    assert ctx.retrieval_hits == [], "weak hits must not feed the reply"


async def test_below_threshold_retrieval_writes_no_citations(
    factory: sessionmaker[Session],
) -> None:
    """A run that abstains cites nothing.

    Citing below-threshold chunks would make the Sources panel claim support the
    run explicitly refused to rely on (``docs/architecture.md`` §9).
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    await _drive(factory, run, hits=[_hit(0.10, 1, "limits")], min_score=0.35)

    assert await SqlCitationStore(factory).list_citations(run.id) == []


async def test_abstention_records_an_audit_event(
    factory: sessionmaker[Session],
) -> None:
    """The abstention is visible as a ``retrieval_abstained`` audit event.

    An abstention is a decision, not merely the absence of hits; recording it
    lets the trace distinguish "escalated because the corpus was weak" from
    "escalated for another reason".
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    await _drive(factory, run, hits=[_hit(0.10, 1, "limits")], min_score=0.35)

    with factory() as session:
        event_types = set(session.execute(select(models.AuditEvent.event_type)).scalars().all())
    assert "retrieval_abstained" in event_types


async def test_strong_retrieval_does_not_escalate_and_still_completes(
    factory: sessionmaker[Session],
) -> None:
    """The contrast: evidence above the threshold plans, responds, and completes.

    Without this, an implementation that *always* abstained would pass the tests
    above. Here the same pump with a strong hit does not set ``escalated`` from
    retrieval and the run still completes.
    """
    await _seed_indexed_document(factory)
    run = await _make_run(factory)

    ctx = await _drive(factory, run, hits=[_hit(0.9, 1, "limits")], min_score=0.35)

    assert ctx.run.status is RunStatus.COMPLETED
    assert ctx.escalated is False, "strong evidence must not force an escalation"
