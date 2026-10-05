"""The three demo MCP servers (crm, billing, issues).

Each is a standalone stdio MCP server with its own seeded SQLite store. They
stand in for Salesforce, Stripe and Jira respectively; see
``docs/mcp-contracts.md`` for the exact tool surface. Nothing in this package is
business logic for the agent -- the servers are the simulated external systems
the agent calls.
"""
