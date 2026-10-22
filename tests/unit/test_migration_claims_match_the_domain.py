"""The migration's claim predicate must match the domain's ``CLAIMABLE``.

## Why this file exists

``migrations/versions/0001_initial_schema.py`` writes the partial index
``ix_agent_runs_claimable`` with a **literal** predicate::

    status IN ('classifying', 'executing', 'planning', 'received', 'responding', 'retrieving')

A migration cannot import application code -- it must be a frozen snapshot of
history -- so that list is typed out. The price is that it can disagree with
``opspilot.domain.runs.CLAIMABLE``, and the disagreement is silent: the index
would cover a different set of rows than the ORM's claim query looks for, and
the symptom would be a claim scan that is slower than it should be, or -- if
the migration's list were *narrower* -- a row the ORM thinks it can claim and
the index never returns.

**The comment in the migration said the suite already checked this.** It said:

    The test suite asserts this predicate matches ``CLAIMABLE`` today.

It did not. Nothing compared the migration's literal with the domain's set.
The ORM's own copy of the predicate *is* checked --
``tests/unit/test_models.py::test_claim_index_predicate_matches_domain_claimable``
compares the model index with ``CLAIMABLE`` -- but the migration's third copy
was not, and a reader of that comment had every reason to believe it was.

So the property is now checked, and this file is the check.

## What this file asserts

1. **The migration's status list is the domain's ``CLAIMABLE``**, compared as
   sets -- order does not matter, membership does.
2. **The migration's SQL predicate names exactly those statuses** and no
   others, so the literal list and the literal SQL cannot drift apart from
   each other either.
3. **The domain's ``CLAIMABLE`` is not the whole enum.** It is derived
   (``frozenset(RunStatus)`` minus the three non-claimable states), so a new
   state is claimable by default; the assertion pins that the derivation
   still excludes the three it should.

## What it does not do

It does not run the migration. ``tests/integration/test_migration_schema.py``
applies the chain to a real database; this file only compares the literals,
which is the half that can be checked without a database.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from types import ModuleType
from typing import cast

from opspilot.domain.runs import CLAIMABLE, TERMINAL, RunStatus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations" / "versions" / "0001_initial_schema.py"


def _migration_module() -> ModuleType:
    """The revision module, imported the way ``test_migration_schema.py`` does.

    Importing it is what proves the constant exists and is the one the
    migration uses; a regex over the file would happily match a status name
    in a comment.
    """
    spec = importlib.util.spec_from_file_location("opspilot_0001_claims", MIGRATION)
    assert spec is not None and spec.loader is not None, f"cannot import {MIGRATION}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constant(module: ModuleType, name: str) -> tuple[str, ...]:
    """A tuple constant the migration declares.

    Read through ``getattr`` and cast, because the module is loaded at run
    time and mypy has nothing to say about its attributes. A missing constant
    raises ``AttributeError`` here, which is the failure a reader wants.
    """
    return cast("tuple[str, ...]", getattr(module, name))


def test_the_migrations_claimable_statuses_are_the_domains() -> None:
    """The migration's literal list is exactly the domain's ``CLAIMABLE``."""
    module = _migration_module()
    literal = _constant(module, "_CLAIMABLE")
    expected = {status.value for status in CLAIMABLE}

    assert literal, "the migration declares no claimable statuses; the index would be empty"
    assert set(literal) == expected, (
        "the migration's claimable statuses differ from the domain's:\n"
        f"  only in the migration: {sorted(set(literal) - expected)}\n"
        f"  only in the domain: {sorted(expected - set(literal))}\n"
        "The index would cover a different set of rows than the ORM's claim "
        "query looks for, and nothing would report it."
    )


def test_the_migrations_sql_predicate_names_exactly_those_statuses() -> None:
    """The literal list and the literal SQL agree with each other.

    Two literals in one file can drift from each other even when the first is
    right: someone adds a status to the Python tuple and forgets the SQL.
    """
    module = _migration_module()
    literal = set(_constant(module, "_CLAIMABLE"))
    where = str(getattr(module, "_CLAIM_WHERE"))  # noqa: B009 -- a literal name is the point

    named = set(re.findall(r"'([a-z_]+)'", where))
    assert named == literal, (
        f"the migration's SQL names {sorted(named)} while its tuple holds {sorted(literal)}"
    )
    assert where.startswith("status IN ("), (
        f"the migration's predicate is not a status list: {where!r}"
    )


def test_the_domain_derives_claimable_from_the_enum() -> None:
    """``CLAIMABLE`` is the enum minus the three states that are not claimable.

    The derivation is the safe direction -- a new state is claimable until
    someone says otherwise -- and this asserts that the three exclusions are
    still the three.
    """
    excluded = {RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.FAILED}

    assert excluded == TERMINAL | {RunStatus.WAITING_APPROVAL}, (
        "the states excluded from claiming are not the two terminal ones plus the parked one"
    )
    assert frozenset(RunStatus) - excluded == CLAIMABLE, (
        "CLAIMABLE is no longer the enum minus the excluded states"
    )
    assert CLAIMABLE, "nothing is claimable"
