"""Shared fixtures for the OpsPilot test suite.

## What is left here, and why so little

This file used to carry eight fixtures. Six of them are gone, and the reason
is worth writing down: **a fixture that nothing uses is a promise nobody
keeps**, and three of them were promises about milestones that had already
passed.

* ``fake_provider`` asked for ``FakeProvider`` -- a class that does not exist;
  the real one is ``FakeModelProvider`` -- and no test asked for the fixture
  anyway.
* ``stub_gateway`` skipped with "stub ToolGateway lands with the gateway port
  in M3". The port landed in M3. The fixture still skipped.
* ``mcp_server_process`` skipped with "MCP server subprocess manager lands in
  M2", and ``seeded_store`` with "seeded MCP store lands in M2". The MCP
  servers are exercised over real stdio in
  ``tests/integration/test_mcp_stdio_transport.py`` now, without a fixture.
* ``store_path``, ``seed_data`` and ``sqlite_engine`` existed only to feed
  ``seeded_store``. ``settings_override`` set three environment variables that
  are **not** the settings' names -- ``OPSPILOT_DATABASE_URL``,
  ``OPSPILOT_EMBEDDER`` and ``OPSPILOT_PROVIDER`` are not aliases of
  ``DATABASE_URL``, ``EMBEDDING_PROVIDER`` and ``MODEL_PROVIDER`` -- so even
  a test that had asked for it would have configured nothing.

What remains is ``repo_root``, which five tests ask for by name, and the rule
the file used to state: never import a module that does not exist at import
time. A collection-time ``ImportError`` fails every test in the tree,
including the ones that do not need the missing module.
"""

from __future__ import annotations

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    """Absolute path to the repository root.

    The five tests that ask for it read a document out of the tree, so the
    path has to be the checkout's and not a copy of it.
    """
    return _REPO_ROOT
