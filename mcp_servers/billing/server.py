"""MCP server: ``billing`` -- invoices, transactions and the idempotent refund.

Stub. The real stdio MCP server is implemented in M2 (see
``docs/milestones.md`` and ``docs/mcp-contracts.md`` S2). This module declares
the intended shape only. This is the server where correctness matters: it
mutates state.

The tool surface this server will expose:

- ``billing.get_invoice``         -- READ, pure.
- ``billing.list_transactions``   -- READ, pure; reports charged rows faithfully
  and never editorialises about which rows are a duplicate.
- ``billing.issue_refund``        -- HIGH_RISK_WRITE, mutates. ``idempotency_key``
  is REQUIRED and unique in the ``refunds`` table, so the same key twice returns
  the existing ``refund_id`` with ``replayed: true`` rather than inserting a
  second refund row. Refusals are structured error results (``validation_error``,
  ``not_found``, ``invalid_state``, ``amount_exceeds_transaction``), never
  exceptions.

Seed data lives beside this module in ``seed.json``; its top-level ``refunds``
list is the idempotency store the golden-path demo depends on.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``billing`` stdio MCP server.

    Raises:
        NotImplementedError: Always, until M2 implements the server.
    """
    raise NotImplementedError("billing MCP server is implemented in M2")


if __name__ == "__main__":
    main()
