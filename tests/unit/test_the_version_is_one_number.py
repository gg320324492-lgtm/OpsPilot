"""The version is one number, written in three places.

## Why this file exists

Three files carry the version, and none of them reads the others:

* ``pyproject.toml``'s ``version`` -- what is built and published;
* ``opspilot/__init__.py``'s ``__version__`` -- what the package reports;
* ``src/opspilot/api/routers/health.py``'s ``_VERSION`` -- what ``GET
  /health`` answers, and the health router's own docstring says it is "the
  package version, so a probe can tell which build answered".

The health router keeps a **literal** there on purpose: reading it from the
installed distribution's metadata at import time would make ``/health`` depend
on the installed distribution. That is a good reason for a literal, and it is
also a reason the literal can go stale -- a release that bumps one of the
three and forgets the other two ships a probe that reports the wrong build.

There is nothing else to check: the three are the same number, and this file
is what keeps them the same number.

## What this file asserts

1. **The three declarations agree.**
2. **The health router's literal is the package's**, read through the package
   rather than through a second copy of the number.
"""

from __future__ import annotations

import pathlib
import re

import opspilot
from opspilot.api.routers import health

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    """The version ``pyproject.toml`` declares."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, "pyproject.toml declares no version"
    return match.group(1)


def test_the_three_version_declarations_agree() -> None:
    """``pyproject.toml``, the package and the health router are one number."""
    declared = _pyproject_version()

    assert opspilot.__version__ == declared, (
        f"opspilot.__version__ is {opspilot.__version__!r}; pyproject.toml says {declared!r}"
    )
    assert declared == health._VERSION, (
        f"the health router answers {health._VERSION!r}; pyproject.toml says "
        f"{declared!r}. A probe that reads the version to tell which build "
        "answered is being told the wrong build."
    )


def test_the_health_router_reads_the_same_number_the_package_does() -> None:
    """The literal in the health router is the package's version.

    Asserted against ``opspilot.__version__`` rather than against a copy of
    the number, so this file cannot itself become the fourth place that has to
    be updated.
    """
    assert opspilot.__version__ == health._VERSION, (
        f"the health router answers {health._VERSION!r} and the package says "
        f"{opspilot.__version__!r}"
    )
