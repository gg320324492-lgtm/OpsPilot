"""The three demo MCP servers (crm, billing, issues).

Each is a standalone MCP server with its own seeded store -- a JSON document,
not a database (see ``mcp_servers/_store.py`` for why). They
stand in for Salesforce, Stripe and Jira respectively; see
``docs/mcp-contracts.md`` for the exact tool surface. Nothing in this package is
business logic for the agent -- the servers are the simulated external systems
the agent calls.
"""
