"""The composite gateway: every registered tool reaches *some* half.

The defect this file exists for
-------------------------------

``knowledge.search`` is registered ``server="internal"``
(``docs/mcp-contracts.md`` §4) and dispatched in-process by ``KnowledgeTool``.
The runtime has exactly **one** dispatch site -- ``agents/runtime.py``'s
``await gateway.call_tool(spec.name, dispatch_arguments)`` -- and both places that
build that gateway handed it a bare ``MCPToolGateway``, whose
``_server_for("internal")`` returns ``None`` by design. So every call reached
``ToolResult(ok=False, error="server_unavailable")``: in the eval harness
(``evals/runner.py::run_case``) and in production
(``worker/__main__.py::build_worker``) alike. A real MiniMax run recorded four
such failures in one run. ``docs/architecture.md`` §7 advertises the tool to the
model as ``READ``, so the model was offered a tool that could only ever fail.

``tests/integration/test_knowledge_is_not_mcp.py`` asserts the MCP gateway must
*refuse* the tool, and that assertion is unchanged -- the refusal is the contract.
``CompositeToolGateway`` is what routes around it.

What these tests pin
--------------------

1. Routing: a ``server="internal"`` tool is dispatched by the knowledge half and
   an external one by the MCP half, and each half sees only its own.
2. ``list_tools`` is the *union*, without the duplicates two registry-answering
   halves would otherwise produce.
3. **Both real call sites are wired.** This is the part a unit test of the
   composite alone cannot establish: a composite nobody constructs fixes nothing.
   The eval and the production worker are each driven for real and the gateway
   they actually built is dispatched against.
"""

from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.retrieval.search import RetrievalOutcome
from opspilot.adapters.tools.composite_gateway import CompositeToolGateway
from opspilot.adapters.tools.knowledge_tool import KnowledgeTool, Retriever
from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
from opspilot.adapters.wiring import RetrievalStack, build_retrieval_stack
from opspilot.domain.tools import TOOL_REGISTRY, Permission, ToolSpec
from opspilot.evals import runner as eval_runner
from opspilot.ports.tool_gateway import ToolResult
from opspilot.ports.vector_store import SearchHit
from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
DATASETS = REPO_ROOT / "evals" / "datasets"

# The golden-path question. ``test_retrieval.py`` asserts it retrieves both
# policy documents, so it is a query this corpus genuinely answers -- which is
# what makes "the hits are non-empty" a statement about the wiring.
_GOLDEN_PATH_QUESTION = (
    "We were charged twice for invoice INV-2026-384. Please investigate and fix it."
)
_EXPECTED_DOCUMENTS = {"refund-policy.md", "duplicate-charge-sop.md"}

# Wide enough to reach the expected documents, for the reason
# ``test_retrieval.py``'s ``_RECALL_K`` gives: the local embedder's scores sit
# below ``RETRIEVAL_MIN_SCORE``, so the ranking claim is asserted at K=10 rather
# than at the dataset's K=5.
_TOP_K = 10


def _slugs(result: ToolResult) -> set[str]:
    """The ``document_slug``s of a knowledge-tool result, as ``test_retrieval.py``
    reads them.

    ``ToolResult.result`` is ``dict[str, object]``, so the hits list needs
    narrowing; doing it here rather than in each assertion keeps the tests about
    *which* documents came back instead of about how to walk the payload.
    """
    assert result.result is not None
    hits = result.result["hits"]
    assert isinstance(hits, list), "the knowledge tool's result carries no hits list"
    return {str(hit["document_slug"]) for hit in hits if isinstance(hit, dict)}


class _RecordingGateway:
    """A ``ToolGateway`` that records what it was asked, and answers nothing.

    Used as the *wrong* half in the routing tests: if a call reaches it, the
    routing is broken, and its own result is a value no assertion above depends
    on.
    """

    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tools = tools or []

    async def list_tools(self) -> list[ToolSpec]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(tool_name=name, ok=True, result={"served_by": "recorder"})


def _hit(rank: int) -> SearchHit:
    """A hit with a stable identity, so assertions can name it."""
    return SearchHit(
        chunk_id=UUID(int=rank),
        document_id=UUID(int=99),
        document_slug="refund-policy.md",
        anchor="limits",
        content="Refunds above $100 need approval.",
        score=0.91,
        rank=rank,
    )


def _stub_retriever(hits: list[SearchHit]) -> Retriever:
    """A ``Retriever`` returning ``hits``, standing in for the vector store."""

    async def _retriever(query: str, top_k: int) -> RetrievalOutcome:
        _ = query, top_k
        return RetrievalOutcome(
            hits=hits,
            top_score=hits[0].score if hits else None,
            abstained=False,
        )

    return _retriever


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


async def test_an_internal_tool_is_dispatched_by_the_knowledge_half() -> None:
    """``knowledge.search`` reaches the in-process tool, not the MCP gateway.

    The composite hands the tool to the knowledge half, which searches the
    retriever it was built with. The MCP half records nothing: reaching it at all
    is the bug -- its ``server_unavailable`` refusal is what this routing exists
    to avoid (``docs/mcp-contracts.md`` §4).
    """
    mcp = _RecordingGateway()
    knowledge = KnowledgeTool(retriever=_stub_retriever([_hit(1)]))
    gateway = CompositeToolGateway(internal=knowledge, mcp=mcp)

    result = await gateway.call_tool("knowledge.search", {"query": "refund policy"})

    assert result.ok is True
    assert result.result is not None
    assert result.result["count"] == 1, "the knowledge half's own hits must come back"
    assert mcp.calls == [], (
        "knowledge.search was offered to the MCP gateway, which refuses it by "
        "design; the composite must route around that, not through it"
    )


async def test_an_external_tool_is_dispatched_by_the_mcp_half(tmp_path: pathlib.Path) -> None:
    """``crm.get_customer`` still reaches its MCP server through the composite.

    The routing must not swallow the tools that always worked. A real
    ``MCPToolGateway`` over isolated in-process servers answers here, so this
    asserts the real server's response -- not that some double was called.
    """
    knowledge = _RecordingGateway()
    gateway = CompositeToolGateway(
        internal=knowledge,
        mcp=MCPToolGateway(servers=build_in_process_servers(tmp_path)),
    )

    result = await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert result.ok is True, f"the MCP half did not serve a real external tool: {result}"
    assert knowledge.calls == [], (
        "an external tool was offered to the knowledge half, which answers for "
        "exactly one registered name; routing it there would turn a working "
        "lookup into an unknown_tool refusal"
    )


async def test_an_unregistered_name_still_gets_unknown_tool() -> None:
    """A name the registry does not know reaches the MCP half's refusal.

    The composite routes on ``TOOL_REGISTRY[name].server`` and has no opinion
    about names that are absent, so it must fall through rather than raise -- and
    the refusal must stay the one the MCP gateway has always given. Gate 2
    rejects these names before dispatch in practice; this pins the behaviour for
    anything that reaches the gateway anyway.
    """
    knowledge = _RecordingGateway()
    mcp = _RecordingGateway()
    gateway = CompositeToolGateway(internal=knowledge, mcp=mcp)

    result = await gateway.call_tool("crm.no_such_tool", {})

    assert result.ok is True
    assert [name for name, _ in mcp.calls] == ["crm.no_such_tool"]
    assert knowledge.calls == []


async def test_the_mcp_gateway_still_refuses_the_internal_tool() -> None:
    """The contract the composite routes around is unchanged, not routed away.

    ``test_knowledge_is_not_mcp.py`` already asserts this against the gateway
    directly. It is repeated here because the fix for "the tool always fails" has
    an obvious wrong form -- teaching ``MCPToolGateway`` to serve it -- and this
    is the assertion that would catch that form.
    """
    result = await MCPToolGateway().call_tool("knowledge.search", {"query": "anything"})

    assert result.ok is False
    assert result.error == "server_unavailable"


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


async def test_list_tools_is_the_union_of_both_halves_without_duplicates() -> None:
    """Every registered tool appears exactly once, whatever the halves answer.

    Both halves really do answer with the static registry, so a naive
    concatenation would list all nine tools twice. The union is deduplicated by
    name -- not by a second registry -- so it stays a statement about what the
    two gateways can execute.
    """
    knowledge = KnowledgeTool()
    gateway = CompositeToolGateway(internal=knowledge, mcp=MCPToolGateway())

    tools = await gateway.list_tools()
    names = [spec.name for spec in tools]

    assert len(names) == len(set(names)), f"list_tools repeated a tool: {names}"
    assert set(names) == set(TOOL_REGISTRY), (
        "the union must cover every registered tool; a name present in only one "
        "half's list is missing from the composite's"
    )


async def test_list_tools_carries_the_registry_s_authority_not_a_half_s() -> None:
    """A half that reports a *narrower* tool still gets listed; permissions hold.

    The halves provide behaviour, the registry provides authority. A stub half
    offering a single spec does not make the composite narrow its list, and the
    permission attached to what is listed is the registry's -- never one a
    gateway chose for itself.
    """
    only_knowledge = ToolSpec(
        name="knowledge.search", permission=Permission.READ, server="internal"
    )
    gateway = CompositeToolGateway(
        internal=_RecordingGateway([only_knowledge]),
        mcp=MCPToolGateway(),
    )

    tools = {spec.name: spec for spec in await gateway.list_tools()}

    assert tools["knowledge.search"] == only_knowledge
    assert set(tools) == set(TOOL_REGISTRY), "the MCP half's list must still be unioned in"
    for name, spec in tools.items():
        assert spec.permission is TOOL_REGISTRY[name].permission, (
            f"{name}'s permission came from a gateway rather than the registry"
        )


# ---------------------------------------------------------------------------
# The eval call site
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_the_eval_harness_wires_a_real_retriever_into_the_knowledge_half(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway ``run_case`` actually builds can answer ``knowledge.search``.

    The composite is only worth anything if the harness constructs one, and only
    if it constructs it with the *case's own* retrieval stack: an unconfigured
    ``KnowledgeTool`` is legal, answers ``not_configured``, and would leave every
    retrieval number in ``evals/datasets/`` empty while every case still "passed".

    So this drives a real case and dispatches against the gateway object the case
    was run with -- captured by spying on ``runner._drain``, which is where
    ``run_case`` hands the gateway to the worker loop. Asserting on hits from the
    committed corpus is what separates "wired to this case's stack" from "wired to
    something".

    The dispatch happens *inside* the spy, while the case's resources are still
    alive. ``run_case`` tears down the per-case database and retrieval stack on
    the way out (``_case_resources``), so a search attempted after it returns
    would fail against a disposed engine -- which says nothing about the wiring.
    """
    captured: list[object] = []
    searched: list[ToolResult] = []
    forwarded: dict[str, Any] = {}
    real_drain = eval_runner._drain

    # ``**_kwargs: object`` rather than ``**kwargs: Any``: the spy must accept
    # whatever ``run_case`` passes, so a narrower signature would make this test
    # fail on an unrelated harness change instead of on the wiring it pins. The
    # forwarded dict is typed ``Any`` because ``_drain``'s own parameters are
    # heterogeneous.
    async def _spy(**_kwargs: object) -> None:
        forwarded.update(_kwargs)
        gateway = forwarded["gateway"]
        captured.append(gateway)
        if not searched:  # the same gateway is drained twice when a run parks
            searched.append(
                await gateway.call_tool(
                    "knowledge.search", {"query": _GOLDEN_PATH_QUESTION, "top_k": _TOP_K}
                )
            )
        await real_drain(**forwarded)

    monkeypatch.setattr(eval_runner, "_drain", _spy)

    cases = eval_runner.load_dataset(DATASETS / "safety.jsonl")
    golden = next(c for c in cases if c.id == "safe-001")
    await eval_runner.run_case(golden, provider_name="fake", tmp_dir=tmp_path)

    assert captured, "the case never reached _drain, so no gateway was built to inspect"
    assert isinstance(captured[0], CompositeToolGateway), (
        f"the eval drives {type(captured[0]).__name__}, so knowledge.search reaches a "
        "gateway that refuses it (server_unavailable)"
    )

    assert len(searched) == 1
    result = searched[0]
    assert result.ok is True, (
        f"the harness's knowledge half has no live retriever: {result.error}. "
        "'not_configured' is the failure this test exists to catch -- it means "
        "the eval composed the gateway without its case's retrieval stack"
    )
    slugs = _slugs(result)
    assert _EXPECTED_DOCUMENTS & slugs, (
        f"the harness's knowledge half searched something other than the case's "
        f"index: retrieved {sorted(slugs)}"
    )


# ---------------------------------------------------------------------------
# The production call site
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_worker_db(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A real SQLite database at the migration head, with the fake provider.

    The same fixture ``test_worker_entry_point.py`` uses: the point is to
    exercise the path a deployment takes, so the schema is migrated rather than
    created from the models.
    """
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "worker.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "composite-gateway-test-token")

    from opspilot.settings import get_settings

    get_settings.cache_clear()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(config, "head")
    return db_path


async def test_the_worker_runs_on_a_gateway_that_serves_every_registered_tool(
    migrated_worker_db: pathlib.Path,
) -> None:
    """``build_worker``'s gateway executes the internal tool *and* an MCP one.

    This is the production half of the fix and the one that matters: the eval
    harness is a measurement tool, while this is what a real run dispatches
    through. A bare ``MCPToolGateway`` here refused every ``knowledge.search``
    with ``server_unavailable``.

    ``ok`` is asserted rather than ``count``: a worker boots against an empty
    index, so the honest result is zero hits. What distinguishes a bound retriever
    from an unbound one is the refusal -- ``KnowledgeTool()`` with no retriever
    answers ``not_configured`` -- and an index that is empty because nobody has
    ingested yet is a different thing from a tool with no backend at all.
    """
    from opspilot.worker.__main__ import build_worker

    worker = build_worker()
    assert isinstance(worker.gateway, CompositeToolGateway)

    knowledge = await worker.gateway.call_tool("knowledge.search", {"query": "refund policy"})
    assert knowledge.ok is True, (
        f"the worker wired no retriever for knowledge.search: {knowledge.error}"
    )

    external = await worker.gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})
    assert external.ok is True, f"composing the gateway broke an external tool: {external}"

    # The worker's shutdown path closes whatever gateway it holds; the composite
    # is what has to make that reach the MCP half's stdio children.
    await worker.gateway.aclose()


# ---------------------------------------------------------------------------
# The retrieval stack the composition depends on
# ---------------------------------------------------------------------------


@pytest.fixture
async def indexed_stack() -> AsyncIterator[RetrievalStack]:
    """A real retrieval stack with the committed corpus ingested.

    Built the way ``tests/integration/test_retrieval.py``'s ``stack`` fixture
    builds one -- shared session factory, then ``reindex_runner`` over
    ``knowledge/`` -- because that is the object both call sites hand to the
    composite as its internal half.
    """
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    maker: sessionmaker[Session] = db.session_factory(settings)
    engine = maker.kw["bind"]
    Base.metadata.create_all(engine)
    built = build_retrieval_stack(settings, session_factory=maker)
    await built.reindex_runner(KNOWLEDGE_DIR)
    try:
        yield built
    finally:
        engine.dispose()


async def test_the_stack_knowledge_tool_is_a_composable_internal_half(
    indexed_stack: RetrievalStack,
) -> None:
    """The internal half the call sites pass is a real, answering ``ToolGateway``.

    ``RetrievalStack`` already carried a ``KnowledgeTool`` before anything was
    composed -- the wiring existed and nothing used it. This asserts the
    composition root's actual input: a tool that returns the corpus's own hits,
    not a ``not_configured`` refusal that would make every composition test pass
    while the eval metrics stayed empty.
    """
    result = await indexed_stack.knowledge_tool.call_tool(
        "knowledge.search", {"query": _GOLDEN_PATH_QUESTION, "top_k": _TOP_K}
    )

    assert result.ok is True
    slugs = _slugs(result)
    assert _EXPECTED_DOCUMENTS & slugs, f"retrieved {sorted(slugs)}"
