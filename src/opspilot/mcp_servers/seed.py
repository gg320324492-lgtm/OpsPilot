"""Seed command for the demo MCP servers.

Responsibility: the ``opspilot-seed`` entry point. Populates each server's store
so the golden workflow has data to find: 10 companies (ACME, Globex, Initech,
Umbrella, Stark Industries, Wayne Enterprises, ...), 20-30 invoices covering the
normal/duplicate-charged/already-refunded/partially-paid/overdue/
subscription-mismatch cases, and an empty issues tracker.

The seed is deterministic: the same run produces the same data, so the golden
path and the eval suite are reproducible.

Layer: tooling entry point.
"""

from __future__ import annotations


def main() -> None:
    """Seed every demo server's store from the committed fixtures. M0 stub."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
