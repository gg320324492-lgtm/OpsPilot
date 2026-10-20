"""The migration chain's head revision, computed rather than written down.

## The drift this exists for

Two test files need to name the head revision of the Alembic chain: the one
that asserts ``alembic upgrade head`` reaches it against a real PostgreSQL, and
the one that renders the whole chain to PostgreSQL SQL offline. Both used to
**write it down** -- ``"0002"`` in one place and ``"0003"`` in the other -- and a
written-down head is a promise somebody has to remember every time a revision
is added, and nothing in the repository checks that they did.

The consequence was not hypothetical. ``0003`` was added, and
``tests/integration/test_migration_schema.py`` kept saying the head was
``"0002"``. The test that exists to prove the migration chain works on the
production dialect **failed on the machine that finally had a PostgreSQL**, and
had never been run anywhere else -- because it carries the ``postgres`` mark
and skips wherever no server is reachable, and CI's PostgreSQL job did not
include it.

So the head is computed, by Alembic's own script directory -- the same
authority ``alembic heads`` consults and the same one the deployment's
``alembic upgrade head`` walks. There is no second answer left to drift from.

## What this does and does not buy

It removes the *bookkeeping* half of the drift: a new revision no longer
requires two literals to be updated, so the head cannot be wrong by
omission. It does **not** remove the drift that matters: a chain whose head is
``0003`` but whose ``0003`` does not apply to a real PostgreSQL is still
broken, and that is what ``tests/integration/test_migration_schema.py`` and
``tests/integration/test_migration_chain_renders_for_postgres.py`` are for.
Computing the head is the smaller half.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def head_revision() -> str:
    """Return the head revision of the repository's Alembic chain.

    Read from Alembic's script directory rather than from a literal in a test
    file, so that adding a revision does not require remembering to update one.

    Raises ``RuntimeError`` when the chain has no head at all, which is a
    broken repository rather than a broken assertion -- and a broken assertion
    would be the wrong thing to report it as.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    # `alembic.ini`'s own `script_location` is relative, and the test may run
    # from anywhere; the absolute path is what the migration fixture uses too.
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError(
            "the migration chain has no head revision: `versions/` is empty, or "
            "every revision in it names a `down_revision` that does not exist"
        )
    return head


def revision_files() -> list[tuple[str, str]]:
    """Return every ``(revision id, file name)`` in the chain, in order.

    The list is what the rendered-SQL test compares against: a revision file
    that alembic does not run is a schema the deployment never gets, and the
    way to notice is to compare the *rendered* chain with the *declared* one.

    The revision id comes from the file's own ``revision = "..."``, not from
    its file name. They happen to agree today -- ``0003_...py`` declares
    ``revision = "0003"`` -- but the file name is a label and the attribute is
    the identity, and a chain where they disagree is a chain where guessing
    from the name reads the wrong answer.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return [
        (script.revision, pathlib.Path(script.path).name)
        for script in ScriptDirectory.from_config(config).walk_revisions()
    ]
