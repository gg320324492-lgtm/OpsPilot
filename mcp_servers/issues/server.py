"""MCP server: ``issues`` -- ticket search and creation.

Stub. The real stdio MCP server is implemented in M2 (see
``docs/milestones.md`` and ``docs/mcp-contracts.md`` S3). This module declares
the intended shape only.

The tool surface this server will expose:

- ``issues.search`` -- READ, pure.
- ``issues.create`` -- SAFE_WRITE, mutates, audited (the ``tool_executed`` event
  is written before the call returns). NOT idempotent by design: two calls
  allocate two distinct keys from the ``next_key`` counter, so the first created
  issue in a fresh store is always ``OPS-1001``.

Seed data lives beside this module in ``seed.json`` and starts with an empty
issues list.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``issues`` stdio MCP server.

    Raises:
        NotImplementedError: Always, until M2 implements the server.
    """
    raise NotImplementedError("issues MCP server is implemented in M2")


if __name__ == "__main__":
    main()
