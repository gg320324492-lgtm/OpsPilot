"""MCP server: ``issues`` -- ticket search and creation.

The tool surface (``docs/mcp-contracts.md`` S3):

- ``issues.search`` -- READ, pure. Substring match over title/description.
- ``issues.create`` -- SAFE_WRITE, mutates, audited. **Deliberately NOT
  idempotent**: there is no idempotency key, so two calls allocate two distinct
  keys from the ``next_key`` counter and create two rows. That is the contract,
  not an oversight -- a duplicate issue is annoying, not a financial event, and
  the cross-cutting rule that every mutating tool take an idempotency key names
  ``issues.create`` as the exception (``docs/mcp-contracts.md`` S5.4).

Keys are allocated as ``OPS-{n}`` from ``next_key``; the seed starts at 1001, so
the first created issue in a fresh store is ``OPS-1001``. That determinism is
what makes the tests readable and the demo reproducible.

Errors are results, never exceptions (``mcp-sdk-notes.md`` S5). Both tools
declare a Pydantic return model and register with ``structured_output=True``.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from mcp_servers._store import Store

__all__ = ["create_server", "main", "server"]

_SEED_PATH = Path(__file__).parent / "seed.json"

ErrorCode = Literal["validation_error", "not_found"]


# ---------------------------------------------------------------------------
# Return models
# ---------------------------------------------------------------------------


class IssueLine(BaseModel):
    """One issue, as returned by search and create."""

    key: str
    title: str
    description: str | None = None
    status: str
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None


class SearchResult(BaseModel):
    """Result of ``issues.search``."""

    code: ErrorCode | None = None
    issues: list[IssueLine] = Field(default_factory=list)
    total: int = 0


class CreateResult(BaseModel):
    """Result of ``issues.create``.

    On a validation failure ``key`` is ``None`` and only ``code`` is set; the row
    is not created, so the counter is not consumed either.
    """

    code: ErrorCode | None = None
    key: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    """Return the current UTC time as an ISO-8601 ``Z`` string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(store: Store, name: str) -> list[dict[str, Any]]:
    """Return a top-level collection as mutable dicts (see billing server)."""
    return cast("list[dict[str, Any]]", store.data.get(name, []))


def _search(store: Store, query: str) -> SearchResult:
    """Implement ``issues.search``.

    A case-insensitive substring match over the title and description. An empty
    query returns every issue rather than nothing -- it is a list operation, not
    an error.
    """
    needle = query.strip().lower()
    matched: list[IssueLine] = []
    for row in store.collection("issues"):
        extra: dict[str, Any] = row.model_extra or {}
        haystack = f"{extra.get('title', '')} {extra.get('description', '')}".lower()
        if needle and needle not in haystack:
            continue
        matched.append(IssueLine.model_validate(row.model_dump()))
    return SearchResult(issues=matched, total=len(matched))


def _create(
    store: Store,
    *,
    title: str,
    description: str | None,
    priority: str | None,
    labels: list[str],
) -> CreateResult:
    """Implement ``issues.create``, allocating a fresh ``OPS-{n}`` key.

    Not idempotent by design: the counter is read, the key built, the counter
    incremented and the row appended, unconditionally. A second call with the
    identical arguments allocates the *next* key and creates a second row.
    """
    if not title.strip():
        return CreateResult(code="validation_error")

    number = int(store.data.get("next_key", 1001))
    key = f"OPS-{number}"
    created_at = _now()

    row: dict[str, Any] = {
        "key": key,
        "title": title,
        "description": description,
        "status": "open",
        "priority": priority,
        "labels": list(labels),
        "created_at": created_at,
    }
    store.data["next_key"] = number + 1
    _rows(store, "issues").append(row)
    store.save()

    return CreateResult(
        key=key,
        title=title,
        description=description,
        status="open",
        priority=priority,
        labels=list(labels),
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def create_server(store: Store | None = None) -> MCPServer:
    """Build the issues MCP server, wiring the tools to ``store``.

    Args:
        store: The backing store. A fixture injects an isolated one; when
            omitted the server builds the default store, which honours
            ``OPSPILOT_MCP_DATA_DIR``.
    """
    backing = store if store is not None else Store(_SEED_PATH, filename="issues.json")
    mcp = MCPServer(name="issues")

    # `name=` is REQUIRED on every tool -- see the note in billing/server.py and
    # docs/mcp-sdk-notes.md §8. The SDK would otherwise register the bare
    # function name while the contract specifies `issues.search` / `issues.create`.
    @mcp.tool(
        name="issues.search",
        structured_output=True,
        description="Search issues by free-text query.",
    )
    def search(query: str) -> SearchResult:
        return _search(backing, query)

    @mcp.tool(
        name="issues.create",
        structured_output=True,
        description=("Create an issue. NOT idempotent: each call allocates a new OPS-{n} key."),
    )
    def create(
        title: str,
        description: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
    ) -> CreateResult:
        return _create(
            backing,
            title=title,
            description=description,
            priority=priority,
            labels=labels if labels is not None else [],
        )

    return mcp


server = create_server()


def main() -> None:
    """Entry point for the ``issues`` stdio MCP server."""
    if "--reset" in sys.argv[1:]:
        server_store = Store(_SEED_PATH, filename="issues.json")
        server_store.reset()
        server_store.save()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
