"""Offline probe for the retrieval-abstain analysis (tool-005/006/012).

Rebuilds the eval harness's exact retrieval stack (local embedder, in-memory
store, corpus from ``knowledge/``), embeds each tool_selection case's ticket
the way ``_retrieve`` does (``subject + "\n" + body`` with the ticket as both),
and prints the top-5 hits with scores, marking the ``RETRIEVAL_MIN_SCORE``
threshold and which hits survive it.

Run: ``py -3.12 scripts/retrieval_abstain_probe.py``
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.retrieval.embeddings import _terms
from opspilot.adapters.wiring import build_retrieval_stack
from opspilot.settings import Settings

_CASE_IDS = ("tool-005", "tool-006", "tool-012")


def _cases() -> list[dict]:
    lines = Path("evals/datasets/tool_selection.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _show(text: str) -> None:
    sys.stdout.write(text + "\n")


async def main() -> None:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    factory = db.session_factory(settings)
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    stack = build_retrieval_stack(settings, session_factory=factory)
    await stack.reindex_runner(Path("knowledge"))
    threshold = settings.retrieval_min_score

    _show(f"RETRIEVAL_MIN_SCORE = {threshold}, top_k = {settings.retrieval_top_k}\n")
    for case in _cases():
        if case["id"] not in _CASE_IDS:
            continue
        ticket = case["ticket"]
        _show(f"=== {case['id']}: {ticket!r}")
        _show(f"    query terms: {_terms(ticket)}")
        hits = await stack.retrieval(ticket)
        for hit in hits:
            marker = "PASS" if hit.score >= threshold else "FAIL"
            _show(f"    [{marker}] {hit.score:.4f}  {hit.document_slug}#{hit.anchor}")
        top = hits[0].score if hits else None
        abstained = not hits or top < threshold
        _show(f"    top={top}  abstained={abstained}\n")

    _show(f"=== contrast: 'refund duplicate charge' terms: {_terms('refund duplicate charge')}")
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
