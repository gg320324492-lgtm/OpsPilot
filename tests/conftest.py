"""Shared fixtures for the OpsPilot test suite.

M0 keeps this honest: the fixtures that can work today do work (a settings
override reading the test environment, an in-memory SQLite engine, a per-test
store directory for the MCP servers), and the fixtures that depend on milestones
not yet built are defined but call ``pytest.skip`` in their bodies.

The rule that matters: never import a module that does not exist yet at import
time. Every optional import is inside the fixture body, so collection of the
whole suite does not depend on ``src/opspilot`` being complete. A collection-time
ImportError would fail every test in the tree, including the ones that do not
need the missing module, and a red suite for the wrong reason is worse than a
skip.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

# The three seed files are real and committed; the path is stable from M0 on.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_SERVERS = _REPO_ROOT / "mcp_servers"


def _optional(module: str) -> ModuleType:
    """Import an optional runtime module, skipping the test if it is absent.

    Args:
        module: Dotted module path, e.g. ``opspilot.settings``.

    Returns:
        The imported module object.

    Raises:
        pytest.skip.Exception: If the module cannot be imported yet.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - the M0 path
        pytest.skip(f"{module} is not implemented yet: {exc}")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def mcp_servers_dir() -> Path:
    """Absolute path to ``mcp_servers/``, the home of the committed seed data."""
    return _MCP_SERVERS


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    """A fresh per-test directory for a seeded MCP server's SQLite file.

    Each test gets its own copy of the store path, so a refund written by one
    test cannot leak into another's assertions. The MCP server is pointed at this
    path (rather than a fixed file) via its ``--db`` flag once it exists.
    """
    store_dir = tmp_path / "stores"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir


@pytest.fixture
def seed_data() -> dict[str, object]:
    """The three committed seed files, parsed, keyed by server name.

    This fixture is real from M0: the seed JSON is the data the golden path
    depends on, so a test may load it before the servers exist.
    """
    seeds: dict[str, object] = {}
    for name in ("crm", "billing", "issues"):
        path = _MCP_SERVERS / name / "seed.json"
        seeds[name] = json.loads(path.read_text(encoding="utf-8"))
    return seeds


@pytest.fixture
def sqlite_engine(store_path: Path) -> Iterator[sqlite3.Connection]:
    """An in-memory SQLite connection for tests that need one without Postgres.

    Yields:
        A connection to ``:memory:`` with foreign keys enabled, closed on
        teardown. This is deliberately the stdlib ``sqlite3`` driver rather than
        SQLAlchemy: the MCP servers own their stores with the stdlib, and the
        in-memory engine here is for that layer. Database tests that need the
        ORM use the ``postgres``-marked fixtures instead.
    """
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Override settings with values that are safe and deterministic in tests.

    Reads ``opspilot.settings`` on first use. Until that module exists (M1) this
    skips. When it does exist, the override forces the local, keyless defaults:
    SQLite rather than Postgres, the deterministic local embedder rather than a
    real one, and the fake provider rather than a network call.
    """
    settings_module = _optional("opspilot.settings")
    monkeypatch.setenv("OPSPILOT_ENV", "test")
    monkeypatch.setenv("OPSPILOT_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("OPSPILOT_EMBEDDER", "local")
    monkeypatch.setenv("OPSPILOT_PROVIDER", "fake")
    return settings_module


@pytest.fixture
def fake_provider() -> object:
    """The deterministic scripted provider used for harness smoke runs."""
    provider_module = _optional("opspilot.adapters.models.fake")
    return provider_module.FakeProvider()


@pytest.fixture
def stub_gateway() -> object:
    """A ``ToolGateway`` stub whose calls are recorded and never perform I/O.

    Used by runtime tests to assert *which* tools the agent proposed without
    standing up the MCP servers. Until the gateway port exists (M3) this skips.
    """
    pytest.skip("stub ToolGateway lands with the gateway port in M3")


@pytest.fixture
def mcp_server_process() -> None:
    """Start one MCP server as a real subprocess and yield a handle to it.

    This is the fixture ``tests/integration/test_mcp_*.py`` will use: the servers
    are exercised over stdio as sibling processes, not imported, so a
    serialisation or protocol mistake fails here rather than at the agent layer.
    """
    pytest.skip("MCP server subprocess manager lands in M2")


@pytest.fixture
def seeded_store(store_path: Path, seed_data: dict[str, object]) -> Path:
    """Lay a server's SQLite store down from the committed seed data.

    Returns the directory holding the store; the caller picks the server. Until
    the server's schema exists (M2) this skips, because there is nothing to
    insert the seed rows into yet.
    """
    pytest.skip("seeded MCP store lands in M2")
