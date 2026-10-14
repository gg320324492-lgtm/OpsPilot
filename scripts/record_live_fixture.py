"""Record a golden-path fixture from a live endpoint, rather than by hand.

Why this exists
---------------
``docs/milestones.md`` §M4 has one criterion unticked: the fixtures under
``evals/datasets/fixtures/`` must be **recorded** from a real provider, not
hand-invented. Both committed files carry ``"recorded": false`` and a note
saying so, and the repo has been honest about that rather than ticking the box.

``record_fixture()`` (``adapters/models/fake.py``) is the recorder, but it cannot
make *these* fixtures. It takes an explicit list of ``{system, prompt}``
descriptors, and the real prompts are assembled at run time from the ticket and
the retrieved policy -- which is why the committed files carry
``"<assembled by build_classification_prompt(ctx)>"`` in that slot. Feeding those
placeholders to ``record_fixture`` would record the placeholder as the question.

So this drives the real run instead. It builds the worker exactly as
``worker/__main__.py::build_worker`` does, wraps the real provider in
``_RecordingProvider``, and submits the README's ticket. The wrapper forwards
every call unchanged and appends the actual ``(system, prompt)`` that crossed the
boundary, with the model's actual response -- which is what a fixture is supposed
to hold.

Usage
-----
The key goes in the environment, never in a file and never in a committed one::

    DATABASE_URL=postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot \\
    OPSPILOT_RECORD_KEY=... OPSPILOT_RECORD_MODEL=MiniMax-M3.1-Flash-Preview \\
    .venv/Scripts/python.exe scripts/record_live_fixture.py

Needs PostgreSQL with the migrations applied (the golden path retrieves through
pgvector) and a key with quota. It writes ``.scratch/recorded_calls.json``.

What it does not do
-------------------
**It does not touch the committed fixtures.** It writes to ``.scratch/`` (which
is gitignored) so the recording can be inspected first. Promoting a recording to
``evals/datasets/fixtures/`` is a separate, deliberate act -- and for the reason
the 2026-10-15 run demonstrates, not a mechanical one:

What the first live run showed
------------------------------
Run on 2026-10-15 against ``MiniMax-M3.1-Flash-Preview``, this recorded six real
calls and the run reached ``completed`` **without proposing a refund.** The model
classified the ticket correctly (``duplicate_charge``, 0.99), then called
``crm.get_customer`` with ``{"email": ...}`` -- but the contract's parameter is
``customer_email``, so the call was rejected with ``validation_error``. It tried
again, was rejected again, and correctly escalated to a human via
``issues.create`` rather than proposing a refund it could not verify.

So a live recording of *this* ticket does not reproduce the golden path the
committed fixture scripts, and cannot replace it: eight test modules drive the
approval-and-refund sequence off that fixture. What the recording captures
instead is the tool-argument defect ``docs/evals.md`` measures at 0.471 argument
validity and ``docs/phase2-plan.md`` §P2-B2 could not fit a cause to -- here as a
live, reproducible instance. That is worth keeping as evidence; it is not a
drop-in fixture, and this script does not pretend otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from opspilot.adapters.models.fake import hash_request  # noqa: E402
from opspilot.settings import get_settings  # noqa: E402


class _RecordingProvider:
    """A provider that forwards to a real one and records what crossed it.

    Implements all three ``ModelProvider`` methods. ``choose_tool`` receives
    ``available_tools``, which the fixture format also stores, so a fixture
    recorded here carries what the model was actually offered.
    """

    def __init__(self, inner: Any, *, sink: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._sink = sink

    async def generate_structured(self, *, system: str, prompt: str, schema: Any, **kw: Any) -> Any:
        result = await self._inner.generate_structured(
            system=system, prompt=prompt, schema=schema, **kw
        )
        self._sink.append(
            {
                "method": "generate_structured",
                "schema": schema.__name__,
                "request": {"system": system, "prompt": prompt},
                "request_hash": hash_request(system, prompt),
                "response": {
                    "value": result.value.model_dump(mode="json"),
                    "usage": result.usage.model_dump(mode="json"),
                },
            }
        )
        return result

    async def choose_tool(
        self, *, system: str, prompt: str, available_tools: Any, **kw: Any
    ) -> Any:
        value = await self._inner.choose_tool(
            system=system, prompt=prompt, available_tools=available_tools, **kw
        )
        self._sink.append(
            {
                "method": "choose_tool",
                "schema": None,
                "request": {"system": system, "prompt": prompt},
                "request_hash": hash_request(system, prompt),
                "available_tools": list(available_tools or []),
                "response": {"value": value, "usage": {}},
            }
        )
        return value

    async def generate_text(self, *, system: str, prompt: str, **kw: Any) -> Any:
        value = await self._inner.generate_text(system=system, prompt=prompt, **kw)
        self._sink.append(
            {
                "method": "generate_text",
                "schema": None,
                "request": {"system": system, "prompt": prompt},
                "request_hash": hash_request(system, prompt),
                "response": {"value": value, "usage": {}},
            }
        )
        return value


async def main() -> int:
    key = os.environ.get("OPSPILOT_RECORD_KEY", "")
    model = os.environ.get("OPSPILOT_RECORD_MODEL", "")
    if not key or not model:
        print("OPSPILOT_RECORD_KEY and OPSPILOT_RECORD_MODEL must both be set")
        return 2

    from opspilot.adapters.models.openai_provider import OpenAIModelProvider
    from opspilot.worker.__main__ import build_worker
    from opspilot.worker.loop import drain_once

    settings = get_settings()
    worker = build_worker(settings)

    real = OpenAIModelProvider(
        api_key=key,
        model_name=model,
        timeout_seconds=180.0,
        base_url="https://api.minimaxi.com/v1",
        json_object_fallback=True,
    )

    sink: list[dict[str, Any]] = []
    # ``_Worker`` is a frozen dataclass, so the provider is swapped by building a
    # new instance rather than assigning the field.
    import dataclasses

    worker = dataclasses.replace(worker, provider=_RecordingProvider(real, sink=sink))

    print(f"provider: {type(real).__name__} / {model}")
    print(f"database: {settings.database_url.split('@')[-1]}")

    # Index the policy documents so retrieval has something to return; the
    # golden path's classification prompt embeds the retrieved hits. The stack's
    # reindex runner is the same one POST /api/knowledge/reindex invokes, so this
    # is the dashboard's own ingest path.
    from opspilot.adapters.wiring import build_retrieval_stack

    stack = build_retrieval_stack(settings, session_factory=worker.session_factory)
    print("indexing policy documents ...")
    await stack.reindex_runner(REPO / "knowledge")
    print("indexed")

    # The endpoint does not reliably reproduce the golden path on the first
    # attempt -- the same instability `docs/phase2-plan.md` §P2-B2 measured as
    # 3/15 versus 2/15 across two identical runs. A run here can pick
    # `issues.create` instead of `billing.get_invoice`, or hand a tool arguments
    # its schema rejects, which ends the run at `failed`. So a recording is
    # attempted up to `ATTEMPTS` times and the first run that reaches the
    # approval gate (or completes) is the one kept: a fixture is a script for a
    # run that worked, not a transcript of one that did not.
    ATTEMPTS = 6
    best: list[dict[str, Any]] = []

    for attempt in range(1, ATTEMPTS + 1):
        sink.clear()
        ticket_id = await worker.ticket_store.create(
            subject="We were charged twice for invoice INV-2026-384",
            body=(
                "Hi, our account was billed twice for invoice INV-2026-384. "
                "Please refund the duplicate. Customer billing@acme.example."
            ),
            customer_email="billing@acme.example",
        )
        run = await worker.run_store.create(
            ticket_id=ticket_id, model_provider="openai", model_name=model
        )
        print(f"\n--- attempt {attempt}: run {run.id} ---")

        for step in range(14):
            processed = await drain_once(
                worker_id="recorder",
                run_store=worker.run_store,
                ticket_store=worker.ticket_store,
                tool_call_store=worker.tool_call_store,
                approval_store=worker.approval_store,
                provider=worker.provider,
                gateway=worker.gateway,
                orchestrator=worker.orchestrator,
                recorder_factory=worker.recorder_factory,
                retrieval=worker.retrieval,
                citation_store=worker.citation_store,
            )
            refreshed = await worker.run_store.get(run.id)
            status = refreshed.status.value if refreshed else "?"
            print(f"    step {step}: processed={processed} status={status} calls={len(sink)}")
            if status in {"completed", "failed", "waiting_approval"}:
                break

        if len(sink) > len(best):
            best = list(sink)
        if status in {"waiting_approval", "completed"}:
            print(f"    -> attempt {attempt} reached {status}; keeping it")
            break
        print(f"    -> attempt {attempt} ended {status}; trying again")

    out = REPO / ".scratch" / "recorded_calls.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(best, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nrecorded {len(best)} calls -> {out}")
    for i, call in enumerate(best):
        prompt = call["request"]["prompt"]
        tools = call.get("available_tools") or []
        label = f"{call['method']}/{call['schema']}"
        print(f"  [{i}] {label} prompt={len(prompt)}ch tools={len(tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
