"""MCP server helpers bundled with the package.

Responsibility: the seed command that populates the three demo MCP servers'
stores (10 companies, 20-30 invoices, an empty issues tracker). The servers
themselves are standalone processes under the repo-root ``mcp_servers/``
directory; this package holds only the seeding entry point so it ships as an
``opspilot-seed`` console script.

Layer: ``adapters``-adjacent tooling. It is allowed to know the MCP servers exist
because it only writes their seed data.
"""

from __future__ import annotations
