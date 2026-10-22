"""The MCP servers' claims about themselves must match what they do.

Three drifts are guarded here, all of them found by reading the code rather
than by reading the tests:

1. **A ``--reset`` that resets a store nobody serves.** ``billing`` and
   ``issues`` each built a *second* ``Store`` for the reset, so the file was
   rewritten from the seed while the running server kept serving the previous
   run's document in memory -- and the server's first write put that stale
   document back over the reset. The flag's promise ("a demo run always starts
   from the same data") was therefore not kept, in exactly the case the flag
   exists for: a live file that differs from the seed.
2. **A docstring that states a stricter rule than the tool enforces.**
   ``crm.get_customer``'s docstring said "Exactly one of ``customer_email`` /
   ``customer_id`` must be given", while the tool accepts both when they
   agree. The contract's stricter rule is enforced at gate 1, *before* a call
   reaches the tool, so the tool's own docstring has to describe the tool.
3. The two servers' ``--reset`` must rewrite the **file** as well as the
   in-memory document: ``Store.reset`` is documented as an in-memory restore,
   and the file is the next server's input.

The store tests **reload** the module under test: a server builds its default
store at import time, so a reload is what makes it pick up an isolated data
directory, and a reload is also how the state is put back for the next test.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import ValidationError

from mcp_servers._store import Store
from mcp_servers.billing import server as billing_server
from mcp_servers.crm import server as crm_server
from mcp_servers.issues import server as issues_server

_ROOT = Path(__file__).resolve().parents[2]
_BILLING_SEED = _ROOT / "mcp_servers" / "billing" / "seed.json"
_ISSUES_SEED = _ROOT / "mcp_servers" / "issues" / "seed.json"


@pytest.fixture(autouse=True)
def _restore_module_state() -> Iterator[None]:
    """Reload both servers after each test, so the next one starts clean.

    The reload happens with the data-dir environment variable already
    restored (the ``monkeypatch`` fixture undoes it first), so the rebuilt
    default store points at the ordinary location rather than at a ``tmp_path``
    that is about to be deleted.
    """
    yield
    importlib.reload(billing_server)
    importlib.reload(issues_server)


async def _call(server: MCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Dispatch one tool call in-process and return its structured content."""
    result = await server.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return dict(result.structured_content)


# ---------------------------------------------------------------------------
# billing --reset
# ---------------------------------------------------------------------------


def test_billing_reset_restores_the_store_the_server_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--reset`` must reset the store the server serves, not a copy of it.

    The bug this pins: ``main()`` built a second ``Store`` for the reset, so
    the file was rewritten from the seed while the module-level ``server``
    kept the previous run's document in memory. A run started with
    ``--reset`` therefore served the *old* data, and its first write -- a
    refund -- put the old document back over the reset on disk.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))

    # A live file that is not the seed: the state --reset exists to clear.
    document = json.loads(_BILLING_SEED.read_text(encoding="utf-8"))
    document["next_refund_id"] = 99999
    for transaction in document["transactions"]:
        transaction["status"] = "refunded"
    (tmp_path / "billing.json").write_text(json.dumps(document), encoding="utf-8")

    billing = importlib.reload(billing_server)
    assert billing._default_store.data["next_refund_id"] == 99999, (
        "the default store did not pick up the live file, so this test would "
        "prove nothing about the reset"
    )

    billing._reset_default_store()

    # The store the server serves is the seed again ...
    assert billing._default_store.data["next_refund_id"] == 10091, (
        "the reset did not restore the store the server serves: its document "
        f"still holds next_refund_id={billing._default_store.data['next_refund_id']}"
    )
    # ... the file on disk is the seed too ...
    on_disk = json.loads((tmp_path / "billing.json").read_text(encoding="utf-8"))
    assert on_disk["next_refund_id"] == 10091, (
        "the reset did not rewrite the persisted file; the next server would "
        f"read next_refund_id={on_disk['next_refund_id']}"
    )
    # ... and the server itself serves the seed, which is the part that used
    # to be wrong: it used to keep answering with the pre-reset document.
    rows = asyncio.run(
        _call(billing.server, "billing.list_transactions", {"invoice_id": "INV-2026-384"})
    )
    transactions = cast("list[dict[str, object]]", rows["transactions"])
    assert transactions, "the seed has transactions for this invoice"
    assert all(row["status"] == "charged" for row in transactions), (
        "the server is still serving the pre-reset document"
    )


# ---------------------------------------------------------------------------
# issues --reset
# ---------------------------------------------------------------------------


def test_issues_reset_restores_the_store_the_server_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``issues.create`` must not be able to write into a pre-reset document."""
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))

    document = json.loads(_ISSUES_SEED.read_text(encoding="utf-8"))
    document["next_key"] = 1002
    document["issues"] = [
        {
            "key": "OPS-1001",
            "title": "An issue from the previous run",
            "description": None,
            "status": "open",
            "priority": None,
            "labels": [],
            "created_at": "2026-10-01T00:00:00Z",
        }
    ]
    (tmp_path / "issues.json").write_text(json.dumps(document), encoding="utf-8")

    issues = importlib.reload(issues_server)
    assert issues._default_store.data["next_key"] == 1002, (
        "the default store did not pick up the live file, so this test would "
        "prove nothing about the reset"
    )

    issues._reset_default_store()

    assert issues._default_store.data["next_key"] == 1001, (
        "the reset did not restore the store the server serves: its document "
        f"still holds next_key={issues._default_store.data['next_key']}"
    )
    on_disk = json.loads((tmp_path / "issues.json").read_text(encoding="utf-8"))
    assert on_disk["next_key"] == 1001, (
        "the reset did not rewrite the persisted file; the next server would "
        f"read next_key={on_disk['next_key']}"
    )
    found = asyncio.run(_call(issues.server, "issues.search", {"query": ""}))
    assert found["total"] == 0, "the server is still serving the pre-reset document"


# ---------------------------------------------------------------------------
# The default store is the one the server serves
# ---------------------------------------------------------------------------


def test_the_default_store_is_the_store_the_server_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create_server()`` with no argument must not build a *second* store.

    A second store over the same file is two in-memory documents over one
    file -- the situation ``_store.py`` documents as the two-writer boundary
    the contract does not cover. The default server is the one writer, and it
    writes into the same document ``--reset`` restores.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    billing = importlib.reload(billing_server)

    server = billing.create_server()
    out = asyncio.run(
        _call(
            server,
            "billing.issue_refund",
            {
                "transaction_id": "TX-88219",
                "amount": 129.0,
                "idempotency_key": "refund:reset-guard:TX-88219",
            },
        )
    )
    assert out["code"] is None
    assert len(billing._default_store.data["refunds"]) == 1, (
        "the default server wrote into a store of its own; the module-level "
        "default store is the one writer over the file"
    )


# ---------------------------------------------------------------------------
# crm --reset
# ---------------------------------------------------------------------------


def test_crm_reset_restores_the_store_the_tools_read(tmp_path: Path) -> None:
    """``crm --reset`` must restore the store the tools serve.

    CRM's restore is in memory only -- it never writes, so it cannot rewrite
    the file -- but the store the tools read must still be the seed rather
    than the previous run's document.
    """
    seed_path = _ROOT / "mcp_servers" / "crm" / "seed.json"
    document = json.loads(seed_path.read_text(encoding="utf-8"))
    document["customers"] = [
        {**document["customers"][0], "customer_id": "CUS-9999", "company": "Stale"}
    ]
    (tmp_path / "crm.json").write_text(json.dumps(document), encoding="utf-8")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        crm_server,
        "_store",
        Store(seed_path=seed_path, filename="crm.json", data_dir=tmp_path),
    )
    try:
        before = asyncio.run(
            _call(crm_server.server, "crm.get_customer", {"customer_id": "CUS-9999"})
        )
        assert before["code"] is None, (
            "the live file was not picked up, so this test would prove nothing about the reset"
        )

        crm_server._reset_store()

        after = asyncio.run(
            _call(crm_server.server, "crm.get_customer", {"customer_id": "CUS-9999"})
        )
        assert after["code"] == "not_found", (
            "the reset did not restore the store the server serves: it is "
            f"still answering with {after}"
        )
        acme = asyncio.run(
            _call(crm_server.server, "crm.get_customer", {"customer_id": "CUS-1001"})
        )
        customer = cast("dict[str, object]", acme["customer"])
        assert customer["company"] == "ACME"
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# crm.get_customer -- the rule the docstring states
# ---------------------------------------------------------------------------


def test_the_get_customer_docstring_states_the_rule_the_tool_enforces() -> None:
    """The docstring must describe this tool, not gate 1's stricter rule.

    The contract says "exactly one of ``customer_email`` / ``customer_id``
    must be given", and the runtime enforces that at gate 1, before a call
    reaches this tool. The tool itself accepts both identifiers when they
    agree -- ``tests/integration/test_mcp_crm.py`` asserts that -- so a
    docstring that told a reader this tool required exactly one would be
    wrong about the tool it documents.
    """
    import inspect

    doc = inspect.getdoc(crm_server.get_customer) or ""
    assert "At least one" in doc, (
        "the docstring no longer states the rule the tool enforces (at least "
        f"one identifier, both allowed when they agree):\n{doc}"
    )
    assert "Exactly one of ``customer_email``" not in doc, (
        "the docstring claims this tool requires exactly one identifier, which "
        "it does not: it accepts both when they agree.\n" + doc
    )


def test_get_customer_accepts_both_identifiers_when_they_agree(tmp_path: Path) -> None:
    """The behaviour the docstring above describes, asserted directly.

    The gate-1 rule is a different layer: this is the tool, called directly.
    """
    import inspect

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        crm_server,
        "_store",
        Store(
            seed_path=_ROOT / "mcp_servers" / "crm" / "seed.json",
            filename="crm.json",
            data_dir=tmp_path,
        ),
    )
    try:
        doc = inspect.getdoc(crm_server.get_customer) or ""
        assert "at least one" in doc.lower()

        result = asyncio.run(
            _call(
                crm_server.server,
                "crm.get_customer",
                {"customer_email": "billing@acme.example", "customer_id": "CUS-1001"},
            )
        )
        assert result["code"] is None
        customer = cast("dict[str, object]", result["customer"])
        assert customer["customer_id"] == "CUS-1001"
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# The seed and the result models
# ---------------------------------------------------------------------------


def test_every_crm_seed_row_validates_against_its_result_model() -> None:
    """The seed's rows must fit the models the CRM tools return.

    The result models are ``extra="forbid"`` on purpose -- "a typo in a field
    name should fail loudly in a test rather than silently adding an
    undocumented field to the agent's schema"
    (``mcp_servers/crm/server.py``). So a seed row carrying a field the model
    does not declare raises a ``ValidationError`` *inside* the tool, and the
    agent sees an opaque error instead of a customer. The claim says that
    should fail loudly in a test; until now no test did.
    """
    seed = json.loads((_ROOT / "mcp_servers" / "crm" / "seed.json").read_text(encoding="utf-8"))
    pairs = (
        ("customers", crm_server.Customer),
        ("accounts", crm_server.Account),
        ("subscriptions", crm_server.Subscription),
    )
    for key, model in pairs:
        rows = seed[key]
        assert rows, f"the seed has no {key} to check"
        for row in rows:
            try:
                model.model_validate(row)
            except ValidationError as exc:
                pytest.fail(
                    f"a {key} row does not fit {model.__name__}, so the CRM tool "
                    f"would raise instead of returning a record: {exc}"
                )
