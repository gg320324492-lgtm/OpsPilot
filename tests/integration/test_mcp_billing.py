"""Contract tests for the ``billing`` MCP server.

These call the server directly through ``await server.call_tool(name, args)`` --
in-process dispatch, no subprocess and no stdio (``mcp-sdk-notes.md`` S6) -- so
they exercise the tools' behaviour in milliseconds. There is no agent and no
LLM anywhere in this file.

The store fixture seeds an isolated ``Store`` from the committed ``seed.json``
into ``tmp_path``, so each test starts from the same data and a mutation in one
test cannot leak into the next. Both the fixture and the tests inspect the store
document directly, which is what makes the idempotency claim checkable at the
level it is actually guaranteed -- the store -- rather than only through the
response.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from mcp_servers._store import Store
from mcp_servers.billing.server import create_server

_SEED_PATH = Path(__file__).resolve().parents[2] / "mcp_servers" / "billing" / "seed.json"

# Fields that would betray the server pre-deciding the duplicate question. The
# spec forbids any of them in the list_transactions output; the test asserts the
# absence explicitly because it is a design property, not an oversight.
_JUDGEMENT_MARKERS = ("duplicate", "is_dup", "dup_of", "suspected", "anomaly", "suspicious")

# A generous ceiling on how long a helper thread may take before the test gives
# up on it. It is a hang guard, not a deadline the test waits for: the thread
# it guards waits on nothing at all, because the ordering it depends on is
# fixed by the code around it. A busy machine cannot turn a slow thread into a
# failure here -- see the boundary tests below.
_THREAD_TIMEOUT_SECONDS = 30.0


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """An isolated billing store seeded from the committed seed."""
    return Store(_SEED_PATH, filename="billing.json", data_dir=tmp_path)


def _call(store: Store, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call in-process and return its structured content.

    ``call_tool`` is declared as returning ``CallToolResult | InputRequiredResult``
    (the latter for elicitation). None of these tools elicit input, so the union
    is narrowed with ``isinstance`` rather than silenced with an ignore.
    """
    server = create_server(store)
    result = asyncio.run(server.call_tool(name, args))
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return dict(result.structured_content)


# ---------------------------------------------------------------------------
# get_invoice
# ---------------------------------------------------------------------------


def test_get_invoice_returns_acme_invoice_with_total(store: Store) -> None:
    out = _call(store, "billing.get_invoice", {"invoice_id": "INV-2026-384"})
    assert out["code"] is None
    assert out["invoice_id"] == "INV-2026-384"
    assert out["customer_id"] == "CUS-1001"
    assert out["total"] == 129.00
    assert out["status"] == "paid"


def test_get_invoice_unknown_id_returns_not_found(store: Store) -> None:
    out = _call(store, "billing.get_invoice", {"invoice_id": "INV-9999-999"})
    assert out["code"] == "not_found"
    assert out["invoice_id"] is None


def test_get_invoice_is_pure(store: Store) -> None:
    before = json.dumps(store.data, sort_keys=True)
    _call(store, "billing.get_invoice", {"invoice_id": "INV-2026-384"})
    assert json.dumps(store.data, sort_keys=True) == before
    # A read never writes the store file.
    assert not store.path.exists()


# ---------------------------------------------------------------------------
# list_transactions
# ---------------------------------------------------------------------------


def test_list_transactions_returns_both_charged_rows(store: Store) -> None:
    out = _call(store, "billing.list_transactions", {"invoice_id": "INV-2026-384"})
    assert out["code"] is None
    ids = sorted(t["transaction_id"] for t in out["transactions"])
    assert ids == ["TX-88218", "TX-88219"]
    assert all(t["amount"] == 129.00 for t in out["transactions"])
    assert all(t["status"] == "charged" for t in out["transactions"])
    assert out["total_charged"] == 258.00


def test_list_transactions_makes_no_duplicate_judgement(store: Store) -> None:
    """The server reports rows; it never says which are duplicates.

    Asserted on the whole JSON-serialised response, not just the row keys, so a
    nested field or a top-level verdict cannot sneak the judgement in. A tool
    that pre-decides the question makes the agent's reasoning untestable.
    """
    out = _call(store, "billing.list_transactions", {"invoice_id": "INV-2026-384"})
    serialised = json.dumps(out).lower()
    for marker in _JUDGEMENT_MARKERS:
        assert marker not in serialised, f"response leaked a judgement field: {marker!r}"


def test_list_transactions_unknown_invoice_returns_empty(store: Store) -> None:
    out = _call(store, "billing.list_transactions", {"invoice_id": "INV-9999-999"})
    assert out["transactions"] == []
    assert out["total_charged"] == 0.0


# ---------------------------------------------------------------------------
# issue_refund -- the central idempotency test
# ---------------------------------------------------------------------------


def test_same_idempotency_key_twice_creates_one_refund(store: Store) -> None:
    """Two calls, one key -> one refund row, same id, replayed false then true.

    The count is asserted against the store document, not the response: the
    duplicate row would appear in the store, so that is where the check belongs.
    (The idempotency check itself is the handler's, not a store constraint --
    the billing store is a JSON document with no ``UNIQUE`` constraint.)

    This is the *single-writer* case the contract promises: one process, calls
    served one at a time, a sequential repeat. The boundary it does **not**
    cover -- two writers over one file -- is pinned by
    ``test_second_writer_over_the_same_key_is_discarded`` below.
    """
    key = "refund:run-abc:TX-88219"
    args = {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": key}

    first = _call(store, "billing.issue_refund", args)
    second = _call(store, "billing.issue_refund", args)

    assert first["code"] is None
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["refund_id"] == second["refund_id"]

    refunds = store.data["refunds"]
    assert len(refunds) == 1
    assert refunds[0]["refund_id"] == first["refund_id"]
    assert refunds[0]["idempotency_key"] == key


# ---------------------------------------------------------------------------
# issue_refund -- the concurrency boundary, made explicit and pinned
#
# The contract deliberately does NOT add a lock to the JSON store: `_store.py`
# records that each server is a standalone stdio process with its own file, so
# the two-writer race the lock would guard does not exist in the deployment,
# and a lock would add a stale-lock failure mode for nothing. The honest thing
# is therefore to state what is guaranteed -- one writer, sequential calls --
# and to test that the *documented failure mode* for two writers is what
# actually happens, rather than leaving the boundary implicit.
#
# The tests below script their interleaving instead of racing for it. A
# barrier with a timeout turns a busy machine into a red test, and no test here
# has an outcome that depends on the schedule it was racing for -- each
# docstring says why.
# ---------------------------------------------------------------------------


def test_concurrent_callers_sharing_one_document_create_one_refund(store: Store) -> None:
    """Two callers on the *same* store document -> one refund. This is the boundary.

    The real deployment is one process serving calls one at a time off the stdio
    loop, so both callers run against the single in-memory document under
    ``store.data``. The first caller appends its refund row *before* it returns,
    so the second caller -- whenever it is scheduled -- scans and finds it. The
    result must be one refund row and two responses agreeing on the id, with the
    second marked ``replayed``.

    The second caller runs on its own thread, so the machine chooses the moment
    it is scheduled; what the test checks does not depend on when that is. What
    puts the row in the shared document is the *first call having returned*,
    which is why that call is made and finished before the thread starts. There
    is nothing here for two callers to race on -- the contract's guarantee is
    scoped to calls served one at a time -- so the test pins that ordering
    rather than a schedule. It used to rendezvous the two threads on a
    ``threading.Barrier`` whose ``wait(timeout=5)`` expired on a busy machine
    and failed the test at random; the barrier was holding two threads together
    that had no reason to be held together, since the ordering that matters is
    fixed by the code above it.
    """
    import threading  # local: threading is used for this boundary test only

    key = "refund:run-concurrent:TX-88219"
    args = {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": key}

    first_response = _call(store, "billing.issue_refund", args)

    outcome: dict[str, Any] = {}

    def second_caller() -> None:
        try:
            outcome["response"] = _call(store, "billing.issue_refund", args)
        except Exception as exc:
            # Carried back to the calling thread, so a failure inside the
            # second caller names its real cause instead of a missing key in
            # ``outcome``.
            outcome["error"] = exc

    worker = threading.Thread(target=second_caller, daemon=True)
    worker.start()
    worker.join(timeout=_THREAD_TIMEOUT_SECONDS)
    assert not worker.is_alive(), "the second caller did not finish"
    if "error" in outcome:
        raise outcome["error"]

    response = outcome["response"]
    assert first_response["code"] is None
    assert response["code"] is None
    assert first_response["replayed"] is False
    assert response["replayed"] is True
    assert response["refund_id"] == first_response["refund_id"]

    assert len(store.data["refunds"]) == 1


def test_second_writer_over_the_same_key_is_discarded(tmp_path: Path) -> None:
    """The documented failure mode: two writers over one file, neither serialised.

    The store's ``save()`` replaces the *whole document* via ``os.replace``,
    which is atomic for a reader but gives no read-modify-write isolation between
    two writers. Two independent stores over one file -- two processes, or a
    process and a hand-run server -- each load the same document, so each scan
    misses the other, each appends a refund, and each reports ``replayed:
    false``. The atomic replace then keeps one document in full and discards the
    other: the file is never torn and ends with a single row, but one caller's
    success was not persisted.

    This test asserts that failure mode rather than denying it, because it is
    what the contract promises is *not* covered.

    The interleaving is *scripted*, not raced. Writer A's write is held back
    until writer B has scanned and written, so the file ends with A's document
    and B's is the one discarded -- the writer this test is named after. That is
    the interleaving ``_store.py`` says nothing prevents: two writers, each
    holding a snapshot taken before the other wrote, each scanning a document
    that does not yet hold the other's refund row.

    It used to be forced with a ``threading.Barrier`` and a ``wait(timeout=5)``,
    which expired on a busy machine and failed the test at random -- a fact
    about the machine's speed, not about the store. No schedule can change the
    outcome here, because a writer's scan is over the in-memory document it
    loaded at construction and never re-reads: whichever order the two writes
    land in, both writers miss each other and the last write wins. Scripting the
    interleaving therefore exercises the same failure mode deterministically,
    and the test can additionally say *which* writer was discarded.

    If the guarantee is ever *made* -- an atomic check-and-write, a lock, a
    compare-and-swap -- this test is expected to change: it would then assert one
    refund and one ``replayed: true``. Until then, the boundary is this.
    """
    seed = _SEED_PATH
    # Two stores, one file. Neither has written, so both hold the seed document:
    # each writer's scan is over a snapshot taken before the other wrote.
    writer_a = Store(seed, filename="billing.json", data_dir=tmp_path)
    writer_b = Store(seed, filename="billing.json", data_dir=tmp_path)

    key = "refund:run-z:TX-88219"
    # The two writers are told apart by their ``reason``, so the test can see
    # *which* document survived the replace rather than merely that one did.
    # The reason takes no part in the idempotency check -- the handler scans on
    # the key alone -- so telling the writers apart changes nothing about the
    # behaviour under test.
    args_a: dict[str, Any] = {
        "transaction_id": "TX-88219",
        "amount": 129.00,
        "idempotency_key": key,
        "reason": "writer-a",
    }
    args_b: dict[str, Any] = {**args_a, "reason": "writer-b"}

    # Writer A's write is held back until writer B has written, so A's document
    # lands last and displaces B's.
    held: list[Callable[[], None]] = []
    original_save = writer_a.save

    def hold_save() -> None:
        held.append(original_save)

    writer_a.save = hold_save  # type: ignore[method-assign]

    first = _call(writer_a, "billing.issue_refund", args_a)
    second = _call(writer_b, "billing.issue_refund", args_b)

    # Writer A has scanned and appended by now and has not written yet, which is
    # the race: neither writer saw the other's refund row.
    assert held, "writer A never reached its write, so nothing was displaced"
    for flush in held:
        flush()

    # Both passed the scan and reported a fresh refund: the key did not stop the
    # second writer, which is the point of the boundary.
    assert first["code"] is None
    assert second["code"] is None
    assert first["replayed"] is False
    assert second["replayed"] is False
    assert first["refund_id"] == second["refund_id"]

    # The atomic replace kept one whole document, not an interleaving and not a
    # torn file: one refund row is on disk, and it is writer A's.
    on_disk = json.loads(writer_a.path.read_text(encoding="utf-8"))
    assert len(on_disk["refunds"]) == 1
    assert on_disk["refunds"][0]["idempotency_key"] == key
    assert on_disk["refunds"][0]["reason"] == "writer-a"

    # All of writer A's document is on disk: the refund row and the
    # transaction-status write together, never one without the other.
    assert on_disk == writer_a.data

    # Writer B was told it succeeded and its row is not the one on disk: the
    # second writer's success was not persisted, which is the boundary this
    # test pins.
    assert len(writer_b.data["refunds"]) == 1
    assert writer_b.data["refunds"][0]["reason"] == "writer-b"
    assert writer_b.data["refunds"][0] != on_disk["refunds"][0]


def test_the_second_writer_still_misses_the_first_writer_s_persisted_row(tmp_path: Path) -> None:
    """The same boundary, one schedule later -- and the tripwire for the fix.

    ``test_second_writer_over_the_same_key_is_discarded`` above fixes the
    schedule in which *both* writers scan before either writes. This one fixes
    the schedule in which the second writer scans *after* the first has written,
    which is the schedule in which the guarantee is most likely to be made, and
    therefore the one worth watching.

    Both writers were constructed before either wrote, so the second writer is
    still holding the document it loaded -- the store never re-reads the file,
    and the idempotency scan is over that loaded document. So the second writer
    misses the row that is on disk, appends a second one, and displaces the
    first writer's whole document: the key protects neither writer, and now not
    even when the two do not overlap in time.

    That is what makes this the tripwire. If the guarantee is ever made -- a
    handler that re-reads the file before scanning, a lock, a compare-and-swap
    -- the second writer would find the first writer's row, report
    ``replayed: true``, and *this* test would fail. The test above cannot
    detect that, because in its schedule both scans happen before either write:
    a re-reading handler would find nothing there either. Between them, the two
    schedules watch both ways the guarantee could appear.
    """
    seed = _SEED_PATH
    # Two stores, one file, both constructed before either has written.
    writer_a = Store(seed, filename="billing.json", data_dir=tmp_path)
    writer_b = Store(seed, filename="billing.json", data_dir=tmp_path)

    key = "refund:run-z-late:TX-88219"
    # As above: the writers are told apart by their ``reason`` so that the
    # surviving document can be identified, and so that a save that merged
    # instead of replacing would leave two rows rather than one.
    args_a: dict[str, Any] = {
        "transaction_id": "TX-88219",
        "amount": 129.00,
        "idempotency_key": key,
        "reason": "writer-a",
    }
    args_b: dict[str, Any] = {**args_a, "reason": "writer-b"}

    first = _call(writer_a, "billing.issue_refund", args_a)
    # The first writer's row is on disk now. The second writer scans the
    # document it loaded, which predates that row, and misses it.
    second = _call(writer_b, "billing.issue_refund", args_b)

    # The key did not stop the second writer, and it reported a fresh refund
    # even though the row it could not see was already persisted.
    assert first["code"] is None
    assert second["code"] is None
    assert first["replayed"] is False
    assert second["replayed"] is False
    assert first["refund_id"] == second["refund_id"]

    # One whole document on disk -- the second writer's, this time, because it
    # wrote last. The first writer's success is the one that did not survive.
    on_disk = json.loads(writer_a.path.read_text(encoding="utf-8"))
    assert len(on_disk["refunds"]) == 1
    assert on_disk["refunds"][0]["idempotency_key"] == key
    assert on_disk["refunds"][0]["reason"] == "writer-b"
    assert on_disk == writer_b.data

    # And the first writer's row is not the one on disk.
    assert len(writer_a.data["refunds"]) == 1
    assert writer_a.data["refunds"][0]["reason"] == "writer-a"
    assert writer_a.data["refunds"][0] != on_disk["refunds"][0]


def test_first_refund_id_comes_from_seed_counter(store: Store) -> None:
    out = _call(
        store,
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "k"},
    )
    assert out["refund_id"] == "REF-10091"
    assert store.data["next_refund_id"] == 10092


def test_successful_refund_mutates_transaction_exactly_once(store: Store) -> None:
    key = "refund:run-xyz:TX-88219"
    args = {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": key}
    first = _call(store, "billing.issue_refund", args)
    _call(store, "billing.issue_refund", args)

    rows = {t["transaction_id"]: t for t in store.data["transactions"]}
    tx = rows["TX-88219"]
    assert tx["status"] == "refunded"
    assert tx["refund_id"] == first["refund_id"]
    # The other charge is untouched: one refund, one state change.
    assert rows["TX-88218"]["status"] == "charged"
    assert len(store.data["refunds"]) == 1


# ---------------------------------------------------------------------------
# issue_refund -- refusals (each a result with a code, never an exception)
# ---------------------------------------------------------------------------


def test_refund_on_already_refunded_transaction_is_invalid_state(store: Store) -> None:
    args = {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "k1"}
    _call(store, "billing.issue_refund", args)

    out = _call(
        store,
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "k2"},
    )
    assert out["code"] == "invalid_state"

    # The transaction is unchanged afterwards -- re-read it.
    rows = {t["transaction_id"]: t for t in store.data["transactions"]}
    assert rows["TX-88219"]["status"] == "refunded"
    assert rows["TX-88219"]["refund_id"] == "REF-10091"
    # No second refund row was appended by the refused call.
    assert len(store.data["refunds"]) == 1


def test_refund_amount_exceeding_transaction_is_refused(store: Store) -> None:
    out = _call(
        store,
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 500.00, "idempotency_key": "k"},
    )
    assert out["code"] == "amount_exceeds_transaction"
    assert store.data["refunds"] == []
    rows = {t["transaction_id"]: t for t in store.data["transactions"]}
    assert rows["TX-88219"]["status"] == "charged"


def test_refund_unknown_transaction_is_not_found(store: Store) -> None:
    out = _call(
        store,
        "billing.issue_refund",
        {"transaction_id": "TX-00000", "amount": 10.00, "idempotency_key": "k"},
    )
    assert out["code"] == "not_found"
    assert store.data["refunds"] == []
    assert store.data["next_refund_id"] == 10091


def test_refund_non_positive_amount_is_validation_error(store: Store) -> None:
    for bad in (0.0, -5.0):
        out = _call(
            store,
            "billing.issue_refund",
            {"transaction_id": "TX-88219", "amount": bad, "idempotency_key": "k"},
        )
        assert out["code"] == "validation_error"
    assert store.data["refunds"] == []


def test_refund_blank_idempotency_key_is_validation_error(store: Store) -> None:
    out = _call(
        store,
        "billing.issue_refund",
        {"transaction_id": "TX-88219", "amount": 129.00, "idempotency_key": "   "},
    )
    assert out["code"] == "validation_error"
    assert store.data["refunds"] == []


# ---------------------------------------------------------------------------
# Refusals never raise (mcp-sdk-notes.md S5). A missing required argument is
# rejected by the protocol before our code runs, which is the point of making
# idempotency_key a parameter with no default.
# ---------------------------------------------------------------------------


def test_issue_refund_without_key_is_rejected_at_protocol_layer(store: Store) -> None:
    """The protocol rejects a call missing `idempotency_key`, before our code runs.

    The assertion is deliberately specific. `pytest.raises(Exception)` would also
    pass if the tool were merely misnamed -- an unknown tool raises the same
    `ToolError` -- so a bare `Exception` here cannot tell "the required argument
    was enforced" from "this server does not have that tool at all". The message
    must name the missing argument.
    """
    server = create_server(store)
    with pytest.raises(ToolError, match="idempotency_key"):
        asyncio.run(
            server.call_tool(
                "billing.issue_refund", {"transaction_id": "TX-88219", "amount": 129.00}
            )
        )
    # Nothing happened: the refusal is before any store mutation.
    assert store.data["refunds"] == []


# ---------------------------------------------------------------------------
# Transport: the server runs as a real stdio subprocess and its tools are
# listable over the wire. In-process dispatch proves behaviour; this proves the
# thing is an MCP server and not a fancier function call.
# ---------------------------------------------------------------------------


def test_billing_server_lists_tools_over_stdio() -> None:
    """Start the server over stdio and list its tools over the wire.

    Uses the low-level SDK stdio client so it does not depend on any helper the
    project may add later. Marked ``slow`` because it starts a subprocess.
    """

    async def _run() -> list[str]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_servers.billing.server"],
            env=None,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            return sorted(tool.name for tool in listed.tools)

    names = asyncio.run(_run())
    assert names == ["billing.get_invoice", "billing.issue_refund", "billing.list_transactions"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the ambient data dir out of these tests.

    The fixture passes ``data_dir`` explicitly, but the module-level ``server``
    built at import time would otherwise pick up a stray ``OPSPILOT_MCP_DATA_DIR``
    from the developer's environment. Clearing it keeps the test hermetic.
    """
    monkeypatch.delenv("OPSPILOT_MCP_DATA_DIR", raising=False)
    yield
