"""The OpsPilot test tree.

Directories mirror the milestones: ``unit`` (pure logic), ``integration`` (real
subprocess MCP servers, API and database), ``agent`` (the runtime and the golden
path), ``security`` (the four invariants and the gate bypass attempts) and
``evals`` (the harness's own test). In M0 every module here contains a single
skipped placeholder; the tests are written as their milestone lands.
"""
