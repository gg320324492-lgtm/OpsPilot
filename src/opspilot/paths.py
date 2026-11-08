"""Where this project writes the files it must not commit — and not off the disk.

## Why this file exists

A project that runs on a developer's machine writes temporary files: an eval
case's private MCP store, a generated-types scratch directory, a demo's
throwaway database, an assets build's intermediate PNGs. The obvious place for
them is the system temp directory, and that is where they went -- which meant
that **every run of the suite left a directory behind on the system drive**:
2,172 of them, 197 MB of ``opspilot-eval-*`` and ``opspilot-demo-*`` folders
under ``%LOCALAPPDATA%\\Temp``, plus 225 MB of pytest basetemps beside them.

Two things were wrong with that, and only one of them is about tidiness.

The first is that the files were **never cleaned up**. The eval harness made a
private directory per case and threw the handle away, so the directory
outlived the case that needed it. A leak is a leak wherever it lands, but it
lands on the drive the developer was not expecting.

The second is that the location was **outside the project**. A temporary file
that lives under the repository is one the developer can see, delete, and
ignore; a temporary file that lives in ``%LOCALAPPDATA%\\Temp`` is invisible,
and the only way to find out how much of the disk it has taken is to go
looking. The project's own ``.gitignore`` cannot protect a directory that is
not in the repository.

So the rule this module encodes is narrow and mechanical: **everything this
project writes at run time goes under the repository's own ``.opspilot/``
directory**, which is gitignored, and nothing goes to the system temp
directory.

## The two knobs

``OPSPILOT_SCRATCH_DIR``
    Where scratch files go. It defaults to ``<repo>/.opspilot/tmp``. A
    deployment or a test that wants them elsewhere sets it.

``OPSPILOT_DATA_DIR``
    Where the MCP servers persist their mutable JSON stores. It defaults to
    ``<repo>/.opspilot/data``. This is *not* the same thing as the scratch
    directory: the scratch directory is disposable and may be erased at any
    moment, while the data directory holds the servers' live state and losing
    it loses a demo's refunds.

## Why the repository root and not the current directory

The repository root is found by walking up from this file until a directory
containing ``pyproject.toml`` appears. The obvious alternative -- the current
working directory -- is how this went wrong in the first place: the MCP
servers' data directory was ``<cwd>/.opspilot-data``, so the same code wrote
into the repository when it was run from the repository and into ``C:\\Users``
when it was run from a terminal elsewhere. A path derived from *where the code
lives* is the same path on every machine and in every shell.

## What it does not do

It does not create anything at import time. :func:`scratch_dir` and
:func:`data_dir` create their directory when they are called, so importing
this module in a process that never writes anything leaves the disk alone.
"""

from __future__ import annotations

import os
import pathlib

#: The environment variable that overrides the scratch directory.
SCRATCH_ENV = "OPSPILOT_SCRATCH_DIR"

#: The environment variable that overrides the data directory.
DATA_ENV = "OPSPILOT_DATA_DIR"

#: The directory name, under the repository root, that holds both.
LOCAL_DIR_NAME = ".opspilot"


def repo_root() -> pathlib.Path:
    """The repository root: the directory that holds ``pyproject.toml``.

    Found by walking up from this file rather than from the current working
    directory, so the answer does not depend on where a command was run from.
    """
    for candidate in pathlib.Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    # A wheel install has no pyproject.toml above it; the package's own
    # directory is then the closest thing to a root, and the caller gets a
    # writable directory rather than an error.
    return pathlib.Path(__file__).resolve().parents[2]


def _configured(variable: str, default: pathlib.Path) -> pathlib.Path:
    """The value of ``variable``, or ``default`` when it is unset or blank."""
    raw = os.environ.get(variable, "").strip()
    if raw:
        return pathlib.Path(raw).expanduser().resolve()
    return default


def scratch_dir() -> pathlib.Path:
    """Where scratch files go, created if it does not exist.

    Returns:
        An absolute path. The directory is created by this call, so a caller
        can write into it immediately.
    """
    path = _configured(SCRATCH_ENV, repo_root() / LOCAL_DIR_NAME / "tmp")
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> pathlib.Path:
    """Where the mutable runtime state goes, created if it does not exist.

    Returns:
        An absolute path. The directory is created by this call.
    """
    path = _configured(DATA_ENV, repo_root() / LOCAL_DIR_NAME / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["DATA_ENV", "LOCAL_DIR_NAME", "SCRATCH_ENV", "data_dir", "repo_root", "scratch_dir"]
