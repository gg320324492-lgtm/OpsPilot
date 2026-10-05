"""Contract tests for the ``issues`` MCP server.

Direct in-process dispatch (``mcp-sdk-notes.md`` S6): ``await
server.call_tool(name, args)``, no agent, no LLM. The store fixture seats an
isolated ``Store`` in ``tmp_path`` so each test starts from the committed seed.

The load-bearing test here is the one that states ``issues.create`` is *not*
idempotent: two calls allocate two distinct keys. That is the contract
(``docs/mcp-contracts.md`` S3 / S5.4), and a test that demanded otherwise would
be encoding a bug.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from mcp_servers._store import Store
from mcp_servers.issues.server import create_server

_SEED_PATH = Path(__file__).resolve().parents[2] / "mcp_servers" / "issues" / "seed.json"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear any ambient data dir so the import-time server stays hermetic."""
    monkeypatch.delenv("OPSPILOT_MCP_DATA_DIR", raising=False)
    yield


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """An isolated issues store seeded from the committed seed."""
    return Store(_SEED_PATH, filename="issues.json", data_dir=tmp_path)


def _call(store: Store, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call in-process and return its structured content.

    Narrowed from ``CallToolResult | InputRequiredResult`` with ``isinstance``;
    these tools never elicit input.
    """
    server = create_server(store)
    result = asyncio.run(server.call_tool(name, args))
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return dict(result.structured_content)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_allocates_first_key_from_seed_counter(store: Store) -> None:
    out = _call(store, "issues.create", {"title": "Duplicate billing incident for ACME"})
    assert out["code"] is None
    assert out["key"] == "OPS-1001"
    assert out["status"] == "open"
    assert store.data["next_key"] == 1002
    assert len(store.data["issues"]) == 1


def test_create_twice_allocates_two_distinct_keys(store: Store) -> None:
    """``issues.create`` is NOT idempotent -- two calls, two keys, two rows.

    Stated as the contract, not as a defect. There is no idempotency key on this
    tool by design; a duplicate issue is annoying, not a financial event.
    """
    first = _call(store, "issues.create", {"title": "Duplicate billing ACME", "priority": "high"})
    second = _call(store, "issues.create", {"title": "Duplicate billing ACME", "priority": "high"})

    assert first["key"] == "OPS-1001"
    assert second["key"] == "OPS-1002"
    assert first["key"] != second["key"]
    assert len(store.data["issues"]) == 2


def test_create_persists_labels_and_priority(store: Store) -> None:
    out = _call(
        store,
        "issues.create",
        {
            "title": "Duplicate billing incident for ACME (INV-2026-384)",
            "description": "Two charges for one invoice",
            "priority": "high",
            "labels": ["billing", "duplicate-charge"],
        },
    )
    assert out["priority"] == "high"
    assert out["labels"] == ["billing", "duplicate-charge"]
    assert store.data["issues"][0]["labels"] == ["billing", "duplicate-charge"]


def test_create_with_blank_title_is_validation_error(store: Store) -> None:
    out = _call(store, "issues.create", {"title": "   "})
    assert out["code"] == "validation_error"
    # A refusal consumes nothing: no row, no counter advance.
    assert store.data["issues"] == []
    assert store.data["next_key"] == 1001


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_finds_what_was_created(store: Store) -> None:
    created = _call(store, "issues.create", {"title": "Duplicate billing ACME"})
    found = _call(store, "issues.search", {"query": "duplicate billing ACME"})
    assert found["code"] is None
    assert found["total"] == 1
    assert found["issues"][0]["key"] == created["key"]


def test_search_is_case_insensitive_on_title(store: Store) -> None:
    _call(store, "issues.create", {"title": "Duplicate Billing ACME"})
    found = _call(store, "issues.search", {"query": "acme"})
    assert found["total"] == 1


def test_search_on_fresh_store_returns_nothing(store: Store) -> None:
    found = _call(store, "issues.search", {"query": "duplicate billing ACME"})
    assert found["total"] == 0
    assert found["issues"] == []


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_issues_server_lists_tools_over_stdio() -> None:
    """Start the server as a real stdio subprocess and list its tools."""

    async def _run() -> list[str]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_servers.issues.server"],
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
    assert names == ["issues.create", "issues.search"]
