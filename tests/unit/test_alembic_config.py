"""The two ``alembic.ini`` files must describe the same migration history.

The repository has a config at the root and one in ``migrations/``. The root one
exists so that the documented command -- ``alembic upgrade head``, run from the
repository root, as the README and ``docs/milestones.md`` both say -- works.
Without it, ``alembic`` finds no config and fails with

    FAILED: No 'script_location' key found in configuration.

Duplicating a config file creates exactly one new way to be wrong: the two can
drift, and then ``alembic upgrade head`` and ``alembic -c migrations/alembic.ini
upgrade head`` apply different histories depending on how they were invoked. The
failure would be silent and confusing -- a migration that exists in one path and
not the other.

This test closes that. It checks the property that actually matters (the same
``script_location``, therefore the same ``env.py`` and the same ``versions/``
directory) rather than comparing the files for equality, because the logging
sections are allowed to differ and a byte comparison would fail for reasons that
do not matter.
"""

from __future__ import annotations

import configparser
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROOT_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_INI = REPO_ROOT / "migrations" / "alembic.ini"


@pytest.fixture(params=[ROOT_INI, MIGRATIONS_INI], ids=["root", "migrations"])
def config_path(request: pytest.FixtureRequest) -> pathlib.Path:
    """Each alembic config, so both are checked rather than just one."""
    return pathlib.Path(request.param)


def _read(path: pathlib.Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    # `read` returns the files it actually parsed; an empty list means the file
    # was missing, which would otherwise make every assertion below vacuously
    # true on an empty parser.
    parsed = parser.read(path, encoding="utf-8")
    assert parsed, f"{path} could not be read"
    return parser


def test_both_configs_exist() -> None:
    """Both files are present, so neither path is silently unavailable."""
    missing = [p for p in (ROOT_INI, MIGRATIONS_INI) if not p.is_file()]
    assert not missing, (
        f"missing alembic config: {[str(p.relative_to(REPO_ROOT)) for p in missing]}. "
        "The root file makes the documented `alembic upgrade head` work from the "
        "repository root; the migrations/ file is the file it mirrors."
    )


def test_script_location_is_declared(config_path: pathlib.Path) -> None:
    """Each config declares ``script_location`` -- the key whose absence was the bug."""
    parser = _read(config_path)
    assert parser.has_option("alembic", "script_location"), (
        f"{config_path.name} has no [alembic] script_location, so `alembic` run "
        f"from a directory that finds this file fails with "
        f"'No script_location key found in configuration'."
    )


def test_both_configs_point_at_the_same_history() -> None:
    """The load-bearing property: one migration history, one ``env.py``.

    Not a byte comparison of the two files -- the logging and hook sections are
    free to differ. What must not differ is where the migrations live, because
    that is what decides which history a command applies.
    """
    root_loc = _read(ROOT_INI).get("alembic", "script_location")
    mig_loc = _read(MIGRATIONS_INI).get("alembic", "script_location")
    assert root_loc == mig_loc, (
        f"the two alembic configs declare different script_location values: "
        f"root={root_loc!r} migrations={mig_loc!r}. They would apply different "
        f"migration histories depending on how alembic was invoked."
    )


def test_script_location_resolves_to_the_migrations_directory() -> None:
    """``script_location`` points at a real directory containing ``env.py``."""
    location = _read(ROOT_INI).get("alembic", "script_location")
    assert location is not None
    target = (REPO_ROOT / location).resolve()
    assert target.is_dir(), f"script_location {location!r} is not a directory: {target}"
    assert (target / "env.py").is_file(), f"{target} has no env.py"
    assert (target / "versions").is_dir(), f"{target} has no versions/ directory"


def test_no_database_url_is_hardcoded() -> None:
    """Neither config embeds a connection string.

    The URL comes from ``opspilot.settings`` at runtime, so there is one place a
    deployment configures its connection. A ``sqlalchemy.url`` in either file
    would be a second source of truth -- and the one most likely to be committed
    with a real password in it.
    """
    for path in (ROOT_INI, MIGRATIONS_INI):
        parser = _read(path)
        if parser.has_option("alembic", "sqlalchemy.url"):
            value = parser.get("alembic", "sqlalchemy.url")
            pytest.fail(
                f"{path.name} hardcodes sqlalchemy.url = {value!r}. The URL must come "
                f"from settings at runtime (migrations/env.py injects it), so that a "
                f"connection string is never committed."
            )
