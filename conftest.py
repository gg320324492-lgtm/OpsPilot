"""Keep the suite's temporary files inside the repository.

## Why this file exists

Every test that asks for a temporary directory -- ``tmp_path``, ``tmp_path_factory``,
or a ``tempfile`` call of its own -- gets it from the **system temp
directory**. On this machine that is ``C:\\Users\\admin\\AppData\\Local\\Temp``,
and a single full run of the suite left 225 MB of ``pytest-of-admin`` behind
there. Nothing in the repository can protect a directory that is not in the
repository: ``.gitignore`` does not reach it, ``git status`` does not show it,
and the only way to find out how much of the disk it has taken is to go
looking for it.

So the temporary directory is pointed at the repository's own
``.opspilot/tmp``, which is gitignored, visible, and erasable with one
``rm -rf``.

**How this works.** ``tempfile`` decides where to put things by reading
``TMPDIR``, ``TEMP`` and ``TMP`` from the environment, in that order, and it
caches the answer in ``tempfile.tempdir``. Setting the three variables is
therefore not enough on its own -- the cache has to be cleared as well, which
is what the second half of this file does.

This module is imported by pytest before any test runs, which is early enough:
pytest creates its base temporary directory the first time a test asks for
one, not at start-up.

## What it does not do

It does not change where the *product* writes. The application's own runtime
files go through ``src/opspilot/paths.py``, and the MCP servers' live stores
through ``mcp_servers/_store.py``. This file is about the test suite's own
scratch space.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

#: The repository root -- the directory that holds ``pyproject.toml``.
REPO_ROOT = pathlib.Path(__file__).resolve().parent

#: Where the suite's temporary files go.
SCRATCH = REPO_ROOT / ".opspilot" / "tmp"


def _point_tempfile_at_the_repository() -> None:
    """Make ``tempfile`` and pytest put their scratch inside the repository."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for variable in ("TMPDIR", "TEMP", "TMP"):
        os.environ[variable] = str(SCRATCH)
    # ``tempfile`` caches the directory it chose. Clearing the cache is what
    # makes the new value take effect -- without it, the first call in the
    # process would have already fixed the old path.
    tempfile.tempdir = None


_point_tempfile_at_the_repository()
