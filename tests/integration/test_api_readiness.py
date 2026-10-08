"""``GET /ready`` must actually check the database, or say why it cannot.

## Why this file exists

``/ready`` reported ``{"database":"ok","migrations":"ok"}`` unconditionally.
``create_app`` accepted a ``readiness_check`` parameter and bound it to
``app.state``, but nothing ever constructed one, so every deployment took the
"no checker bound -> report ok" branch and the probe never opened a connection.
Compose keys ``api``'s healthcheck on ``/ready`` (``docker-compose.yml``), so an
orchestrator was being told to send traffic to a process that could not serve.

The proof of the defect, and the shape these tests pin: with a genuinely
unreachable database the old probe returned ``200``. A readiness probe that
always says ready is worse than no probe.

## What is asserted

Three states, each against the *real* app construction path (no fakes injected):

- a migrated SQLite database -> ``200``, both checks ``ok``;
- a SQLite path that cannot be opened -> ``503``, ``database`` named as the
  failure (``migrations`` cannot be ``ok`` either, since the same connection
  failed);
- a reachable but unmigrated SQLite database -> ``503``, ``migrations`` named,
  which is the "table does not exist three layers down" state the endpoint
  exists to catch.

SQLite is the dialect here because it is the local dev path and it can be made
unreachable deterministically (a directory that does not exist), so no external
service is required. The Postgres path uses the same two queries.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from opspilot.settings import get_settings

_TOKEN = "readiness-probe-test-token"  # noqa: S105 -- a fixture value


def _migrate(db_path: pathlib.Path) -> None:
    """Run ``alembic upgrade head`` against *db_path*.

    The real migration path, not ``metadata.create_all``: the probe reads
    ``alembic_version``, and a schema built from the models would leave that
    table absent -- which is exactly the state the migrations check reports, so
    creating the schema any other way would test nothing.

    ``DATABASE_URL`` must already name *db_path*: ``migrations/env.py`` sets
    ``sqlalchemy.url`` from ``opspilot.settings``, so it overrides the config's
    own value. The caller sets the environment first.
    """
    from alembic import command
    from alembic.config import Config

    root = pathlib.Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(config, "head")


def _client_for(monkeypatch: pytest.MonkeyPatch, database_url: str) -> TestClient:
    """Build the real app (no injected stores) against *database_url*."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    get_settings.cache_clear()

    from opspilot.api.app import create_app

    return TestClient(create_app())


def test_ready_is_200_against_a_migrated_database(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path: a real, migrated database is certified ready.

    This is the assertion that must stay true -- a fix that made the probe fail
    everything would satisfy "503 when down" and be useless.
    """
    db_path = tmp_path / "ready.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()
    _migrate(db_path)
    get_settings.cache_clear()

    with _client_for(monkeypatch, f"sqlite+pysqlite:///{db_path}") as client:
        response = client.get("/ready")
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "migrations": "ok"}


def test_ready_is_503_when_the_database_is_unreachable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unopenable database is a 503 naming ``database``, never a 200.

    The directory does not exist, so the SQLite driver cannot open the file. The
    old probe returned ``200 ok`` here -- this is the regression this whole
    change is about, and it must go red if the checker ever reports ok
    unconditionally again.
    """
    missing = tmp_path / "no-such-dir" / "x.db"

    with _client_for(monkeypatch, f"sqlite+pysqlite:///{missing}") as client:
        response = client.get("/ready")
        get_settings.cache_clear()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] != "ok", (
        "an unreachable database must not be reported ok; this is the defect"
    )


def test_ready_is_503_when_migrations_are_not_at_head(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reachable but unmigrated database is a 503 naming ``migrations``.

    The database opens (an empty SQLite file), so ``database`` is ``ok`` -- and
    ``alembic_version`` does not exist, so ``migrations`` is not. This is the
    state §7 says the distinction exists for: alive, reachable, and about to
    fail every query with "table does not exist".
    """
    db_path = tmp_path / "empty.db"
    # Touch the file so the database exists and is reachable, but apply nothing.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    with _client_for(monkeypatch, f"sqlite+pysqlite:///{db_path}") as client:
        response = client.get("/ready")
        get_settings.cache_clear()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migrations"] != "ok", (
        "a database with no alembic_version must not be reported migrated; "
        "this check is the one that catches the unmigrated-database failure"
    )
