"""Worker process entry point.

Responsibility: the ``opspilot-worker`` console script and ``python -m
opspilot.worker``. Wires concrete adapters, marks interrupted runs on boot, then
runs the poll loop until a shutdown signal.

Layer: ``worker`` (composition root). This is the one place the worker chooses
concrete adapters -- the loop itself depends only on the ports and the runtime.

**Retrieval wiring (M5c).** The worker builds the retrieval stack from settings
and passes it to :func:`poll_forever`. ``knowledge.search`` is not an MCP server
(``docs/mcp-contracts.md`` §4), so retrieval is built in-process -- the embedder,
the vector store and the ``RETRIEVAL_MIN_SCORE`` threshold -- and threaded through
as the runtime's published ``RetrievalCallable``. A worker that passed ``None``
would still record the RETRIEVING step, but with zero hits and no citations, so
the run would have no Sources panel.

The full loop wiring (provider, gateway, orchestrator, stores) is completed in
M6, which is where the orchestrator's step executor lands; ``build_worker_retrieval``
is deliberately separate and complete now, because that is M5's piece.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opspilot.settings import get_settings

if TYPE_CHECKING:
    from opspilot.adapters.wiring import RetrievalStack


def build_worker_retrieval() -> RetrievalStack:
    """Build the worker's retrieval stack from settings.

    Named rather than inlined so it is the single place the worker's retrieval
    wiring lives and so a test can call it without starting the loop. The adapter
    import is local, matching the entry point's "import concrete adapters only at
    composition time" style.
    """
    from opspilot.adapters.wiring import build_retrieval_stack

    return build_retrieval_stack(get_settings())


def main() -> None:
    """Run the worker until interrupted.

    M0 stub pending M6's loop wiring: the orchestrator's step executor and the
    golden-path loop land there. M5's retrieval stack is built by
    :func:`build_worker_retrieval` and is ready to be threaded into
    ``poll_forever(..., retrieval=..., citation_store=..., retrieval_min_score=...)``
    when that wiring lands.
    """
    raise NotImplementedError


if __name__ == "__main__":
    main()
