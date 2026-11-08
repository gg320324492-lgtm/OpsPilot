"""The readiness probe's states are the ones the contract names.

## Why this file exists

``GET /ready`` answers with two named checks, and each check has a small
vocabulary of outcomes: ``database`` is ``ok`` or ``unreachable``; ``migrations``
is ``ok``, ``unknown``, ``unmigrated`` or ``behind``. A client branches on
those words, and ``docs/api-contract.md`` §7 lists them.

Two things can rot that list, and neither is visible from the route:

* **The implementation grows a state the contract does not name.** A client
  that switches on the four documented words falls through on the fifth, and a
  probe that reports a state nobody documented is a probe whose answer nobody
  can act on.
* **The contract names a state the implementation cannot produce.** The reader
  writes a branch for an outcome that cannot happen, and believes the probe
  has a case it does not have.

And one thing rotted *in the prose* while the code was right:
``readiness.py``'s ``_head_revision`` said that a script directory with
several heads "returns the first, because ... any single answer is more
useful than a crash". Alembic's ``get_current_head`` refuses to pick one and
raises instead, and the caller's broad catch turns that into ``unknown`` --
which is the honest answer, since a deployment whose migrations are ambiguous
cannot certify a database against either head. The prose was corrected; this
file is what keeps the corrected claim true.

## What this file asserts

1. **The two checks produce exactly the states the contract names** -- read out
   of the implementation's own return statements, so a new state has to be
   added in two places to pass.
2. **The contract names every one of them**, and names nothing the
   implementation cannot produce.
3. **A script directory with two heads answers ``unknown``**, rather than
   raising out of the route or picking one of the heads.
4. **A missing script directory answers ``unknown``** too -- a deployment whose
   migrations cannot be read has not been certified, and saying so is the
   difference between a probe and a rubber stamp.

## What it does not do

It does not check the route's HTTP behaviour (that ``/ready`` answers 503 when
a check is not ``ok``) -- the route's own tests do that -- and it does not
check what happens when the database is unreachable, which needs a server to
unreach.
"""

from __future__ import annotations

import ast
import pathlib
import re

from opspilot.adapters.persistence.readiness import (
    _check_migrations,
    resolve_migrations_dir,
)
from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "api-contract.md"
READINESS = REPO_ROOT / "src" / "opspilot" / "adapters" / "persistence" / "readiness.py"

#: The states each check can produce. The implementation is checked against
#: these, not the other way round, so a new state has to be added here too.
DATABASE_STATES = frozenset({"ok", "unreachable"})
MIGRATION_STATES = frozenset({"ok", "unknown", "unmigrated", "behind"})

#: The states the route can report that the probe cannot produce: the probe was
#: never bound at all.
UNCONFIGURED_STATES = frozenset({"unconfigured"})


def _states_in(expression: ast.expr | None) -> set[str]:
    """The literal states an expression can produce.

    A conditional expression counts: ``return "ok" if current == head else
    "behind"`` produces two states, and a reader of the function is entitled
    to both of them.
    """
    if expression is None:
        return set()
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return {expression.value}
    if isinstance(expression, ast.IfExp):
        return _states_in(expression.body) | _states_in(expression.orelse)
    return set()


def _returned_states(function_name: str) -> set[str]:
    """The literal states a function can return.

    Read out of the function's own ``return`` statements rather than by calling
    it: a state that only appears on one branch would not be found by calling
    the happy path, and the whole point is to catch the states that are hard to
    reach.
    """
    tree = ast.parse(READINESS.read_text(encoding="utf-8"))
    states: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return):
                states |= _states_in(inner.value)
    return states


def _contract_section() -> str:
    """The contract's section 7, the one that lists the states."""
    lines = CONTRACT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 7."))
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_the_checks_produce_exactly_the_states_the_contract_names() -> None:
    """The implementation's vocabulary is the one this file pins."""
    database = _returned_states("_check_database")
    migrations = _returned_states("_check_migrations")

    assert database == set(DATABASE_STATES), (
        f"`_check_database` can return {sorted(database)}, not {sorted(DATABASE_STATES)}"
    )
    assert migrations == set(MIGRATION_STATES), (
        f"`_check_migrations` can return {sorted(migrations)}, not {sorted(MIGRATION_STATES)}"
    )


def test_the_contract_names_every_state_and_nothing_else() -> None:
    """The contract and the implementation agree, in both directions."""
    section = _contract_section()
    named = set(re.findall(r"`(ok|unreachable|unmigrated|behind|unknown|unconfigured)`", section))

    missing = (DATABASE_STATES | MIGRATION_STATES | UNCONFIGURED_STATES) - named
    assert not missing, (
        f"the contract's §7 does not name {sorted(missing)}, which the probe can "
        "report. A client that branches on the documented words falls through on "
        "the others."
    )

    producible = DATABASE_STATES | MIGRATION_STATES | UNCONFIGURED_STATES
    extra = named - producible
    assert not extra, (
        f"the contract's §7 names {sorted(extra)}, which the probe cannot "
        "produce. A reader who writes a branch for it is writing a branch for "
        "an outcome that cannot happen."
    )


def _script_directory(tmp_path: pathlib.Path, heads: int) -> pathlib.Path:
    """A minimal Alembic script directory with ``heads`` heads."""
    versions = tmp_path / "versions"
    versions.mkdir()
    for index in range(heads):
        (versions / f"rev{index}.py").write_text(
            f"revision = 'rev{index}'\ndown_revision = None\n", encoding="utf-8"
        )
    return tmp_path


def test_several_heads_answer_unknown_rather_than_picking_one(tmp_path: pathlib.Path) -> None:
    """A branched script directory is ambiguous, and the probe says so.

    Alembic's ``get_current_head`` raises on several heads rather than picking
    one. The probe's broad catch turns that into ``unknown`` -- which is the
    answer the contract gives for "the migration scripts could not be read" --
    and not into a head that was chosen for the reader.
    """
    from opspilot.adapters.persistence import db

    directory = _script_directory(tmp_path, heads=2)
    factory = db.session_factory(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))

    state = _check_migrations(factory, directory)
    assert state == "unknown", (
        f"a script directory with two heads answered {state!r}; the contract says "
        "'unknown' -- a probe that picks one of two heads is certifying a "
        "database it cannot name."
    )


def test_a_missing_script_directory_answers_unknown(tmp_path: pathlib.Path) -> None:
    """A deployment whose migrations cannot be found has not been certified."""
    from opspilot.adapters.persistence import db

    factory = db.session_factory(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))
    absent = tmp_path / "no-such-directory"

    assert _check_migrations(factory, absent) == "unknown", (
        "a missing script directory answered something other than 'unknown'"
    )


def test_the_repository_ships_the_directory_the_probe_looks_for() -> None:
    """The probe finds the migrations directory in this checkout.

    The directory is resolved by walking up to ``migrations/env.py``, which is
    not part of the installed package -- so a deployment whose image forgot to
    mount it answers ``unknown`` forever, and that is worth a test here rather
    than only in the image.
    """
    resolved = resolve_migrations_dir()
    assert resolved is not None, (
        "the probe cannot find the repository's migrations directory; every "
        "deployment built this way would answer 'unknown' for the migrations check"
    )
    assert (resolved / "env.py").is_file()
