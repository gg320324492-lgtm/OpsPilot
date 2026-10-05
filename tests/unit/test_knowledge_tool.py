"""The in-process ``knowledge.search`` dispatcher.

Written from the specification:

- ``docs/mcp-contracts.md`` §4: ``knowledge.search`` "is registered in the tool
  registry with permission ``READ`` and ``server="internal"``. It is implemented
  in-process against pgvector rather than as a fourth MCP server." It "is still a
  registered tool with a ``ToolSpec``, so the same permission machinery covers
  it".
- ``docs/tool-permissions.md`` §3: an error is a *result*, not an exception.

These are unit tests of the tool surface (name, permission, result shape, refusal
codes), not of retrieval quality -- that is ``tests/integration/test_retrieval.py``.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from opspilot.adapters.retrieval.search import RetrievalOutcome
from opspilot.adapters.tools.knowledge_tool import KnowledgeTool
from opspilot.domain.tools import TOOL_ARGUMENT_SCHEMAS, TOOL_REGISTRY, Permission
from opspilot.ports.vector_store import SearchHit

_TOOL_NAME = "knowledge.search"


def _hit(rank: int, score: float, anchor: str = "refund-limits") -> SearchHit:
    return SearchHit(
        chunk_id=UUID(int=rank),
        document_id=UUID(int=100),
        document_slug="refund-policy.md",
        anchor=anchor,
        content="Refunds above $100 need approval.",
        score=score,
        rank=rank,
    )


def _tool_returning(hits: list[SearchHit], *, abstained: bool) -> KnowledgeTool:
    top = hits[0].score if hits else None

    async def _retriever(_query: str, _top_k: int) -> RetrievalOutcome:
        return RetrievalOutcome(hits=hits, abstained=abstained, top_score=top)

    return KnowledgeTool(retriever=_retriever)


# ---------------------------------------------------------------------------
# Registration (docs/mcp-contracts.md §4)
# ---------------------------------------------------------------------------


def test_the_tool_name_is_registered_as_read_internal() -> None:
    """``knowledge.search`` is a registered ``READ`` tool with ``server="internal"``.

    §4's contract, read from the static registry -- the authority for permissions
    (``docs/tool-permissions.md`` §2).
    """
    assert _TOOL_NAME in TOOL_REGISTRY
    spec = TOOL_REGISTRY[_TOOL_NAME]
    assert spec.permission is Permission.READ
    assert spec.server == "internal"
    assert _TOOL_NAME in TOOL_ARGUMENT_SCHEMAS, "gate 1 needs an argument schema for the tool"


def test_list_tools_returns_the_registry() -> None:
    """``list_tools`` returns the static registry, not a second one."""
    import asyncio

    tools = asyncio.run(KnowledgeTool().list_tools())
    assert {spec.name for spec in tools} == set(TOOL_REGISTRY)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_returns_hits_in_the_tool_result_shape() -> None:
    """A successful search is ``ok=True`` with the hits, count and abstained flag.

    The hit fields are ``document_slug`` / ``anchor`` / ``score`` / ``rank`` --
    everything a structural citation needs (``docs/api-contract.md`` §3).
    """
    result = await _tool_returning([_hit(1, 0.91), _hit(2, 0.72)], abstained=False).call_tool(
        _TOOL_NAME, {"query": "duplicate charge", "top_k": 5}
    )

    assert result.ok is True
    assert result.error is None
    assert result.result is not None
    assert result.result["count"] == 2
    assert result.result["abstained"] is False
    hits = result.result["hits"]
    assert isinstance(hits, list)
    assert [h["rank"] for h in hits] == [1, 2]
    assert hits[0]["document_slug"] == "refund-policy.md"
    assert hits[0]["anchor"] == "refund-limits"
    assert hits[0]["score"] == pytest.approx(0.91)


async def test_abstention_is_a_success_carrying_the_flag() -> None:
    """An abstained search is ``ok=True`` with ``abstained=True``.

    Abstention is a supported outcome (``docs/architecture.md`` §9), not a
    failure: the index answered, and the answer was "nothing scores above the
    threshold".
    """
    result = await _tool_returning([], abstained=True).call_tool(_TOOL_NAME, {"query": "anything"})

    assert result.ok is True
    assert result.result is not None
    assert result.result["abstained"] is True
    assert result.result["count"] == 0


async def test_arguments_reach_the_retriever_with_a_bounded_top_k() -> None:
    """The query and the bounded ``top_k`` reach the retriever."""
    seen: dict[str, object] = {}

    async def _retriever(query: str, top_k: int) -> RetrievalOutcome:
        seen["query"] = query
        seen["top_k"] = top_k
        return RetrievalOutcome(hits=[], abstained=True, top_score=None)

    result = await KnowledgeTool(retriever=_retriever).call_tool(
        _TOOL_NAME, {"query": "refund policy", "top_k": 3}
    )
    assert result.ok is True
    assert seen == {"query": "refund policy", "top_k": 3}


# ---------------------------------------------------------------------------
# Errors are results (docs/tool-permissions.md §3)
# ---------------------------------------------------------------------------


async def test_not_configured_is_a_refusal_with_a_code() -> None:
    """With no retriever, the tool refuses with ``not_configured``, never raises.

    The distinction that matters: a zero-hit *success* from an unconfigured index
    is indistinguishable from a zero-hit success from a configured index that
    found nothing, and "index not configured" must be distinguishable from "found
    nothing".
    """
    result = await KnowledgeTool().call_tool(_TOOL_NAME, {"query": "duplicate charge"})

    assert result.ok is False
    assert result.error == "not_configured"
    assert result.result is not None
    assert result.result["code"] == "not_configured"


async def test_a_raising_backend_is_a_retrieval_failed_result() -> None:
    """A backend that raises is reported as ``retrieval_failed``, not propagated.

    An exception would become an opaque error with the code lost; the caller must
    see a result it can branch on.
    """

    async def _retriever(_query: str, _top_k: int) -> RetrievalOutcome:
        msg = "the index is on fire"
        raise RuntimeError(msg)

    result = await KnowledgeTool(retriever=_retriever).call_tool(_TOOL_NAME, {"query": "q"})

    assert result.ok is False
    assert result.error == "retrieval_failed"


async def test_an_empty_query_is_a_validation_error_result() -> None:
    """A blank query is refused at the tool as well as at gate 1."""
    result = await _tool_returning([_hit(1, 0.9)], abstained=False).call_tool(
        _TOOL_NAME, {"query": "   "}
    )
    assert result.ok is False
    assert result.error == "validation_error"


async def test_an_unknown_tool_name_is_refused() -> None:
    """The tool answers for exactly one name; anything else is ``unknown_tool``."""
    result = await KnowledgeTool().call_tool("crm.get_customer", {"customer_id": "X"})
    assert result.ok is False
    assert result.error == "unknown_tool"
