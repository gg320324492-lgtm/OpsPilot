"""MCP server: ``crm`` -- customer, account and subscription reads.

Stub. The real stdio MCP server is implemented in M2 (see
``docs/milestones.md`` and ``docs/mcp-contracts.md`` S1). This module declares
the intended shape only: three ``READ``-permission tools, all pure, no writes.

The tool surface this server will expose:

- ``crm.get_customer``  -- exactly one of ``customer_email`` / ``customer_id``;
  neither or both-disagreeing is a ``validation_error`` result.
- ``crm.get_account``   -- billing contact, payment terms, currency, address.
- ``crm.get_subscription`` -- plan, seat count, monthly amount, current period.

Seed data lives beside this module in ``seed.json`` and is loaded by the
``--reset`` flag and by the pytest fixture.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``crm`` stdio MCP server.

    Raises:
        NotImplementedError: Always, until M2 implements the server.
    """
    raise NotImplementedError("crm MCP server is implemented in M2")


if __name__ == "__main__":
    main()
