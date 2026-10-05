"""Run detail served from the *real* run store, not from a test double.

Every run-detail test in this suite builds the app with ``FakeRunStore``, and
``FakeRunStore`` implements ``list_steps``, ``list_tool_calls`` and
``list_citations``. ``SqlRunStore`` -- what a deployment actually binds -- did
not. The router reaches those three reads through an optional-method probe
(``runs.py``'s ``_optional_method``), so against the real store the probe found
nothing, every list came back empty, and ``GET /api/runs/{id}`` served a run with
no timeline, no tool calls and no citations.

The suite could not see it, and that is the point of this file. A test double
that offers more than production is the one kind of double that cannot fail: it
answers every probe, so the code under test never takes the branch it takes in
production. This file drives ``SqlRunStore`` over a real schema so the probe's
answer is the one a deployment gets.

``docs/api-contract.md`` §3 names the fields; ``docs/data-model.md`` §2 names the
tables. Both are the authority for what follows.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, repositories
from opspilot.adapters.persistence.models import (
    Base,
    Citation,
    KnowledgeChunk,
    KnowledgeDocument,
)
from opspilot.domain.runs import AgentRun, RunStatus
from opspilot.domain.tools import Permission, ToolCallStatus
from opspilot.settings import Settings

pytestmark = pytest.mark.asyncio


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield maker
    finally:
        engine.dispose()


@pytest.fixture
async def seeded(
    factory: sessionmaker[Session],
) -> tuple[repositories.SqlRunStore, AgentRun]:
    """A run with a step, a tool call and a citation, written through the stores.

    Written through the real stores rather than inserted raw, so the rows are the
    rows production would create: a ``set_status`` call writes its own
    ``state_change`` step, and the citation is written by the tool-call/citation
    stores the worker holds.
    """
    tickets = repositories.SqlTicketStore(factory)
    runs = repositories.SqlRunStore(factory)
    calls = repositories.SqlToolCallStore(factory)

    ticket_id = await tickets.create(
        subject="Charged twice for invoice INV-2026-384",
        body="We were charged twice.",
        customer_email="billing@acme.example",
    )
    run = await runs.create(ticket_id=ticket_id, model_provider="fake", model_name="fake-1")
    await runs.set_status(run.id, RunStatus.CLASSIFYING)

    call_id = await calls.record_proposed(
        run_id=run.id,
        tool_name="billing.get_invoice",
        arguments={"invoice_id": "INV-2026-384"},
        permission=Permission.READ.value,
    )
    await calls.set_status(call_id, ToolCallStatus.EXECUTED, result={"total": 129.0})

    # A document and a chunk, so the citation's foreign keys resolve. Written
    # directly because the SQLite path's vector store is in-memory and keeps no
    # chunk rows -- a separate finding, not this file's subject.
    document_id = uuid4()
    chunk_id = uuid4()
    # One session for all three rows: the citation's foreign keys point at the
    # document and the chunk, and they must be visible in the same transaction.
    with db.session_scope(factory) as session:
        session.add(
            KnowledgeDocument(
                id=document_id,
                title="Refund Policy",
                source="refund-policy.md",
                content="Refunds above $100 require human approval.",
                doc_metadata={"owner": "Finance Operations"},
                content_hash="hash-1",
                indexed_at=datetime.now(UTC),
            )
        )
        # SQLite enforces foreign keys immediately, and SQLAlchemy's unit of work
        # does not order these inserts for us, so each parent is flushed before
        # the row that references it.
        session.flush()
        session.add(
            KnowledgeChunk(
                id=chunk_id,
                document_id=document_id,
                ordinal=0,
                anchor="refund-limits",
                heading_path="Refund Policy > Refund Limits",
                content="Refunds above $100 require human approval.",
                token_count=8,
                embedding=None,
            )
        )
        # The citation's foreign keys point at the document and the chunk, so the
        # parents must be flushed into the transaction before the child insert.
        session.flush()
        session.add(
            Citation(run_id=run.id, document_id=document_id, chunk_id=chunk_id, score=0.87, rank=1)
        )

    return runs, run


async def test_the_real_run_store_offers_the_three_reads_the_router_probes(
    seeded: tuple[repositories.SqlRunStore, AgentRun],
) -> None:
    """``SqlRunStore`` answers ``list_steps``/``list_tool_calls``/``list_citations``.

    This is the assertion the whole file exists for. The router reaches these
    through ``_optional_method`` and falls back to an empty list when the method
    is absent -- a fallback that is correct for a store that genuinely lacks the
    capability and a silent failure for the one store that must have it.
    """
    runs, _run = seeded
    for name in ("list_steps", "list_tool_calls", "list_citations"):
        assert hasattr(runs, name), (
            f"SqlRunStore has no {name}; the router's optional-method probe will "
            f"find nothing and the run-detail panel will render empty in a real "
            f"deployment while the FakeRunStore-backed tests stay green"
        )


async def test_run_detail_carries_the_timeline_not_an_empty_list(
    seeded: tuple[repositories.SqlRunStore, AgentRun],
) -> None:
    """The steps are read back, in sequence order.

    ``set_status`` writes a ``state_change`` step per transition
    (``repositories.py``), so a run that moved to CLASSIFYING has at least one.
    An empty list here is the defect: the trace endpoint would show nothing.
    """
    runs, run = seeded
    steps = await runs.list_steps(run.id)

    assert steps, "the run has no steps, so the trace panel would be empty"
    assert [step.sequence for step in steps] == sorted(step.sequence for step in steps)
    assert any(step.step_type == "state_change" for step in steps)


async def test_run_detail_carries_the_tool_calls(
    seeded: tuple[repositories.SqlRunStore, AgentRun],
) -> None:
    """The tool calls are read back, with their permission and status as enums.

    ``docs/api-contract.md`` §3 types ``permission`` and ``status`` with the
    domain enums; the row carries the enums, not the stored strings, so the
    router renders them without converting.
    """
    runs, run = seeded
    calls = await runs.list_tool_calls(run.id)

    assert len(calls) == 1
    call = calls[0]
    assert call.tool_name == "billing.get_invoice"
    assert call.permission is Permission.READ
    assert call.status is ToolCallStatus.EXECUTED
    assert call.result == {"total": 129.0}


async def test_run_detail_carries_the_citations_with_the_greppable_chunk_string(
    seeded: tuple[repositories.SqlRunStore, AgentRun],
) -> None:
    """The citations come back with ``chunk`` composed as ``"{slug}#{anchor}"``.

    ``docs/api-contract.md`` §3: this is "a stable string a reader can grep for in
    ``knowledge/``". Composing it in the store is what keeps the router free of
    the join, and asserting the exact spelling is what keeps the contract.
    """
    runs, run = seeded
    citations = await runs.list_citations(run.id)

    assert len(citations) == 1
    citation = citations[0]
    assert citation.document == "refund-policy.md"
    assert citation.chunk == "refund-policy.md#refund-limits"
    assert citation.rank == 1
    assert citation.score == pytest.approx(0.87)


async def test_the_router_renders_a_seeded_run_with_its_panels_populated(
    seeded: tuple[repositories.SqlRunStore, AgentRun],
) -> None:
    """End to end through the router: the loaded detail is not three empty lists.

    The three tests above assert the store; this one asserts the thing the user
    sees, by calling the router's own loaders against the real store. It is the
    regression test for the defect -- it fails if the store stops offering a
    read, and it fails if the router stops calling it.
    """
    from opspilot.api.routers.runs import _load_citations, _load_steps, _load_tool_calls

    runs, run = seeded

    steps = await _load_steps(runs, run.id)
    tool_calls = await _load_tool_calls(runs, run.id)
    citations = await _load_citations(runs, run.id)

    assert steps, "no steps rendered"
    assert tool_calls, "no tool calls rendered"
    assert citations, "no citations rendered"
    assert [c.chunk for c in citations] == ["refund-policy.md#refund-limits"]
