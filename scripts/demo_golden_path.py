"""Drive the README golden path end to end and print what happens, for the GIF.

Why this exists
---------------
``docs/milestones.md`` §M9 asks for "a recorded GIF from the deterministic path".
``MODEL_PROVIDER=fake`` is that path: no API key, a scripted replay, a
byte-identical trace every run.

But the *two-process* deployment cannot show the golden path on SQLite. The
vector store is ``InMemoryVectorStore`` (``adapters/wiring.py``, chosen when
``settings.is_sqlite``) and keeps embeddings in **process memory**. The API's
reindex populates the API's memory; the worker is a second process with an empty
one, so retrieval returns zero hits, the run abstains, and it ends at
``RESPONDING`` with an escalation reply -- no tool call, no approval, no refund.
Recorded in ``docs/progress.md``, ``docs/limitations.md`` and ADR-0004.

So this script runs the API and the worker **in one process**, which is the only
arrangement in which the deterministic path actually refunds. It is not the
deployment: it is the deployment's components, assembled in a single process so
they share the vector store, exactly as ``tests/agent/_golden_harness.py`` does.

What it does, and what is real
------------------------------
* the **real** ``FastAPI`` app, served by **uvicorn** on a real TCP port;
* the **real** ``poll_forever`` worker loop, in a background thread;
* the **real** MCP servers, over in-process transports, on a private store;
* the **real** retrieval stack over the committed ``knowledge/`` corpus;
* every operator action is a **real HTTP request** to ``127.0.0.1`` -- ``curl``
  is shown for each so the reader can issue the same call by hand.

Recording it:

    vhs docs/demo/golden-path.tape

Run it by hand (it prints the same output):

    ./.venv/Scripts/python.exe scripts/demo_golden_path.py

It exits non-zero if the refund does not land, so a broken run is not recorded
as a success.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# The deterministic path, pinned before anything reads settings. `fake` needs no
# key and replays a committed script, so the trace is byte-identical every run.
# The scenario names the script the fake replays; without it the provider has no
# data and the run dies at `classifying`.
os.environ["MODEL_PROVIDER"] = "fake"
os.environ["OPSPILOT_FAKE_SCENARIO"] = "duplicate_charge"
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["RETRIEVAL_MIN_SCORE"] = "0.22"
os.environ["OPSPILOT_OPERATOR_TOKEN"] = "local-dev-token-not-a-secret"
os.environ["API_HOST"] = "127.0.0.1"
os.environ["API_PORT"] = "8000"

# The recording must not depend on the terminal's encoding. Windows consoles
# default to a codepage (GBK here) that cannot encode the box-drawing and
# tick characters below, which would turn a working run into a
# UnicodeEncodeError at the last print. Reconfigure to UTF-8 and keep every
# literal below ASCII, so the same script records the same bytes anywhere.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
TOKEN = os.environ["OPSPILOT_OPERATOR_TOKEN"]

_SUBJECT = "We were charged twice for invoice INV-2026-384."
_BODY = "Please investigate and fix it."

# Pacing, for the recording only. The run itself is real and unaffected -- this
# only slows the *printing*, so a screen recording at a few frames per second
# actually shows the stages advancing instead of cutting straight from the first
# line to the last. `scripts/record_demo_gif.mjs` sets it. Unset (the default)
# is a normal, unthrottled run.
_STEP_DELAY = float(os.environ.get("OPSPILOT_DEMO_STEP_DELAY", "0"))


def _pace() -> None:
    """Wait between sections so a recording can be read."""
    if _STEP_DELAY:
        time.sleep(_STEP_DELAY)


def _rule(title: str = "") -> None:
    """A section banner, so the recording has visible structure."""
    line = "-" * 4
    if title:
        print(f"\x1b[1;36m{line} {title} {line}\x1b[0m", flush=True)
    else:
        print(f"\x1b[90m{'-' * 68}\x1b[0m", flush=True)


def _curl(
    method: str,
    path: str,
    *,
    authorized: bool = True,
    body: dict[str, Any] | None = None,
) -> None:
    """Show the equivalent curl, so the reader can reproduce the call by hand.

    ``-X`` is shown for every verb rather than only the POSTs: a reader copying
    the line should get the same request this script made, and ``curl``'s
    default of GET is an inference the recording should not make for them.
    """
    parts = ["curl", "-s", "-X", method]
    if authorized:
        parts += ["-H", '"Authorization: Bearer $TOKEN"']
    if body is not None:
        parts += ["-H", '"Content-Type: application/json"', "-d", f"'{json.dumps(body)}'"]
    parts.append(f"{BASE}{path}")
    print(f"\x1b[90m$ {' '.join(parts)}\x1b[0m", flush=True)


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    show_curl: bool = False,
) -> tuple[int, dict[str, Any]]:
    """One real HTTP call to the running API."""
    if show_curl:
        _curl(method, path, body=body)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", method=method, data=data)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode())
        return exc.code, payload


def _show(payload: dict[str, Any], *, keep: list[str] | None = None) -> None:
    """Print a response, trimmed to the fields that matter for the demo."""
    if keep is not None:
        payload = {k: payload[k] for k in keep if k in payload}
    print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)
    _pace()


def _wait_for_ready(timeout: float = 30.0) -> bool:
    """Poll ``/ready`` until it certifies -- database reachable, migrations head.

    ``/ready`` genuinely checks the database (``api/routers/health.py``), so it
    is not instant after the socket binds. A recording that started submitting
    tickets before it passed would race a 503.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, payload = _request("GET", "/ready")
            if status == 200:
                _curl("GET", "/ready", authorized=False)
                print(f"HTTP {status}")
                _show(payload)
                return True
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    return False


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="opspilot-demo-"))
    db_path = tmp / "opspilot.db"
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"

    # -- migrate the fresh database ---------------------------------------
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")

    from opspilot.adapters.models.fake import FakeModelProvider
    from opspilot.adapters.orchestration.linear import LinearOrchestrator
    from opspilot.adapters.persistence import db
    from opspilot.adapters.persistence.repositories import (
        SqlApprovalStore,
        SqlCitationStore,
        SqlRunStore,
        SqlTicketStore,
        SqlToolCallStore,
    )
    from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers
    from opspilot.adapters.wiring import build_retrieval_stack
    from opspilot.api.app import create_app
    from opspilot.settings import get_settings
    from opspilot.tracing.recorder import TraceRecorder
    from opspilot.worker import loop as worker_loop

    settings = get_settings()
    factory = db.session_factory(settings)

    # ONE retrieval stack, shared by the API's reindex endpoint and the worker's
    # search. This is the whole reason the demo is single-process: on SQLite the
    # vectors live in process memory, so reindex and search must be the same
    # object or the worker retrieves nothing (see the module docstring).
    stack = build_retrieval_stack(settings, session_factory=factory)
    asyncio.run(stack.reindex_runner(REPO_ROOT / "knowledge"))

    # The real API app, with the shared retrieval stack injected so its
    # `POST /api/knowledge/reindex` and the worker search the same vectors.
    app = create_app(
        knowledge_store=stack.knowledge_store,
        reindex_runner=stack.reindex_runner,
    )
    # The app builds its own SQL stores from DATABASE_URL; point them at the same
    # file the migration and the worker use.
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    # The real worker loop, driving the same database in a background thread.
    mcp_dir = tmp / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    gateway = MCPToolGateway(servers=build_in_process_servers(mcp_dir))
    stores = {
        "run_store": SqlRunStore(factory),
        "ticket_store": SqlTicketStore(factory),
        "tool_call_store": SqlToolCallStore(factory),
        "approval_store": SqlApprovalStore(factory),
    }

    def run_worker() -> None:
        asyncio.run(
            worker_loop.poll_forever(
                worker_id="worker-demo-1",
                provider=FakeModelProvider(scenario="duplicate_charge"),
                gateway=gateway,
                orchestrator=LinearOrchestrator(),
                recorder_factory=lambda rid: TraceRecorder(run_id=rid, session_factory=factory),
                retrieval=stack.retrieval,
                citation_store=SqlCitationStore(factory),
                retrieval_min_score=settings.retrieval_min_score,
                poll_interval=0.2,
                **stores,
            )
        )

    threading.Thread(target=run_worker, daemon=True).start()

    # -- the demo ---------------------------------------------------------
    print("\x1b[1mOpsPilot - the golden path, deterministic provider\x1b[0m", flush=True)
    print("MODEL_PROVIDER=fake   OPSPILOT_FAKE_SCENARIO=duplicate_charge", flush=True)

    _rule("1. Readiness - /ready checks the database, not just the socket")
    if not _wait_for_ready():
        print("\x1b[1;31m/ready never certified\x1b[0m", flush=True)
        return 1

    _pace()
    _rule("2. Submit the ticket - it enqueues a run in RECEIVED")
    status, created = _request(
        "POST",
        "/api/tickets",
        body={"subject": _SUBJECT, "body": _BODY, "customer_email": "billing@acme.example"},
        show_curl=True,
    )
    print(f"HTTP {status}", flush=True)
    _show(created)
    run_id = created["run"]["id"]

    _pace()
    _rule("3. Wait for the run to park on the human approval gate")
    pending: dict[str, Any] | None = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, run = _request("GET", f"/api/runs/{run_id}")
        if run.get("pending_approval") is not None:
            pending = run["pending_approval"]
            break
        if run.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.25)
    if pending is None:
        print("\x1b[1;31mthe run never reached the approval gate\x1b[0m", flush=True)
        _show(_request("GET", f"/api/runs/{run_id}")[1])
        return 1
    _curl("GET", f"/api/runs/{run_id}")
    print(f"\x1b[1;33mrun {run_id} is parked: WAITING_APPROVAL\x1b[0m", flush=True)
    _show(pending)

    _pace()
    _rule("4. A human approves - this is the only thing that moves money")
    status, decision = _request(
        "POST", f"/api/approvals/{pending['id']}/approve", body={}, show_curl=True
    )
    print(f"HTTP {status}", flush=True)
    _show(decision)

    _pace()
    _rule("5. The worker resumes the approved run and executes the refund once")
    deadline = time.monotonic() + 30
    final: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, final = _request("GET", f"/api/runs/{run_id}")
        if final.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.25)
    _curl("GET", f"/api/runs/{run_id}")
    print(f"\x1b[1;32mstatus: {final.get('status')}\x1b[0m", flush=True)

    _pace()
    _rule("6. The run detail - every step, tool call and citation, persisted")
    _, detail = _request("GET", f"/api/runs/{run_id}", show_curl=True)
    executed = [c for c in detail.get("tool_calls", []) if c.get("status") == "executed"]
    for call in executed:
        print(f"  executed  {call.get('tool_name')}", flush=True)
    seen: set[str] = set()
    for cite in detail.get("citations", []):
        doc = str(cite.get("document"))
        if doc not in seen:
            seen.add(doc)
            print(f"  cited     {doc}", flush=True)
    reply = detail.get("customer_reply") or {}
    if reply.get("body"):
        print(f'\n\x1b[90mcustomer reply:\x1b[0m "{reply["body"]}"', flush=True)

    # -- the assertion that matters: did the money move exactly once? ------
    # Ask a fresh gateway to the same store, so this is the server's answer,
    # not the run's own account of itself (the repository's core habit).
    import asyncio as _asyncio

    async def server_refunds() -> list[str]:
        gw = MCPToolGateway(servers=build_in_process_servers(mcp_dir))
        rows = await gw.call_tool("billing.list_transactions", {"invoice_id": "INV-2026-384"})
        txns = (rows.result or {}).get("transactions", [])
        return [t["transaction_id"] for t in txns if t.get("status") == "refunded"]

    refunded = _asyncio.run(server_refunds())
    _rule()
    refund_ok = refunded == ["TX-88219"] and final.get("status") == "completed"
    if refund_ok:
        print(
            "\x1b[1;32m[OK] billing server reports exactly one refunded transaction: "
            "TX-88219\x1b[0m",
            flush=True,
        )
    else:
        print(
            f"\x1b[1;31m[!!] unexpected: status={final.get('status')} refunded={refunded}\x1b[0m",
            flush=True,
        )

    server.should_exit = True
    time.sleep(0.4)
    return 0 if refund_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
