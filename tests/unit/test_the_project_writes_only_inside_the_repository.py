"""Everything this project writes at run time stays inside the repository.

## Why this file exists

The project used to write its temporary files to the **system temp
directory** -- ``%LOCALAPPDATA%\\Temp`` on this machine -- and it left them
there:

| What | How many |
|---|---|
| ``opspilot-eval-*`` (one per eval case, never removed) | 1,494 |
| ``opspilot-packaging-*`` | 140 |
| ``opspilot-menu-*`` | 41 |
| ``pytest-of-admin`` (the suite's basetemp) | 7 |
| ``opspilot-desktop-updater`` (electron-builder's cache) | 1 |

Two things were wrong with that, and the second is the one that matters.

The first is the **leak**: the eval harness made a private directory per case
and threw the handle away, so every case of every run left a directory
behind. A leak is a leak wherever it lands.

The second is that the files were **outside the project**. A temporary file
under the repository is one the developer can see, delete and ignore. A
temporary file under ``%LOCALAPPDATA%\\Temp`` is invisible, ``.gitignore``
cannot protect it, ``git status`` does not show it, and the only way to find
out how much of the disk it has taken is to go looking.

So the rule is narrow and mechanical: **the project writes inside the
repository, under its own gitignored ``.opspilot/`` directory, and nothing
goes to the system temp directory.**

## What this file asserts

1. **No source file names a temporary location outside the repository.** Every
   call that *chooses* a directory -- ``mkdtemp``, ``TemporaryDirectory``,
   ``TemporaryFile``, ``mkstemp``, ``mkdtempSync`` -- must either pass an
   explicit directory or name the repository's own scratch directory.
2. **The one exception is still an exception.** ``mcp_servers/_store.py``
   writes its temporary file *next to the file it is replacing* -- which is
   inside the repository, and is the only way the replacement can be atomic.
3. **The redirect works.** ``tempfile.gettempdir()`` -- called *from a test*,
   so after ``conftest.py`` has run -- resolves inside the repository. This is
   the assertion that would catch the redirect silently breaking.
4. **The scratch directory is gitignored**, so the repository stays clean.
5. **The MCP servers' live stores** default to a directory inside the
   repository rather than wherever the process happened to be started.

## What it does not do

It does not check the *content* of what is written, and it does not check the
tooling that is not in this repository's tree -- the Electron packager's
updater cache lives under ``%LOCALAPPDATA%`` and is configured by
electron-builder rather than by this repository.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The directory, under the repository, that the project's runtime files live in.
LOCAL_DIR = ".opspilot"

#: The trees whose source is checked. The build outputs (``web/.next``,
#: ``desktop/dist``, ``node_modules``) are excluded: they are copies of source
#: that a copy cannot fix.
_SOURCE_TREES = (
    "src",
    "mcp_servers",
    "scripts",
    "web/scripts",
    "web/e2e",
    "web/src",
    "desktop/src",
    "desktop/e2e",
    "desktop/scripts",
)

#: A call that *chooses* a temporary location. ``gettempdir`` and ``tmpdir``
#: are in it because they name a location too -- they are just the ones that
#: are allowed to be redirected by the environment.
_CHOOSERS = (
    re.compile(r"\btempfile\.mkdtemp\b"),
    re.compile(r"\btempfile\.TemporaryDirectory\b"),
    re.compile(r"\btempfile\.TemporaryFile\b"),
    re.compile(r"\btempfile\.mkstemp\b"),
    re.compile(r"\btempfile\.gettempdir\b"),
    re.compile(r"\bmkdtempSync\b"),
    re.compile(r"\btmpdir\(\)"),
)

#: The expressions that name a location inside the repository.
_INSIDE = re.compile(r"scratch_dir\(\)|SCRATCH_DIR|data_dir\(\)|" + re.escape(LOCAL_DIR))

#: An explicit directory argument. A call that passes one cannot reach the
#: system temp directory by accident: it uses the directory it was given.
_EXPLICIT_DIR = re.compile(r"\bdir\s*=")

#: The files allowed to name a temporary location without naming the
#: repository's own: why, in one line each.
_EXCEPTIONS: dict[str, str] = {
    "mcp_servers/_store.py": (
        "the temporary file is written *next to the file it replaces*, which is "
        "what makes the replacement atomic -- and that file is inside the repository"
    ),
}


def _source_files() -> list[pathlib.Path]:
    """Every source file in the trees this guard reads."""
    files: list[pathlib.Path] = []
    for tree in _SOURCE_TREES:
        root = REPO_ROOT / tree
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".mjs", ".ts", ".tsx", ".js"}:
                continue
            if any(
                part in {"node_modules", "dist", ".next", "stage", "release"} for part in path.parts
            ):
                continue
            files.append(path)
    return sorted(files)


def _lines_with_a_chooser(path: pathlib.Path) -> list[tuple[int, str]]:
    """The places in a file that name a temporary location.

    A place is a line *and the two that follow it*, because a call's arguments
    are often on the next line -- ``tempfile.TemporaryDirectory(\\n    dir=...\\n)``
    is one call, not two lines to judge separately.
    """
    hits: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "#")):
            # A comment that *describes* the old behaviour is not a call.
            continue
        if stripped.startswith(("import ", "from ")) and "node:" in stripped:
            # An import names a function; it does not choose a location.
            continue
        if any(pattern.search(line) for pattern in _CHOOSERS):
            snippet = " ".join(part.strip() for part in lines[number - 1 : number + 2])
            hits.append((number, snippet))
    return hits


def _is_routed(snippet: str) -> bool:
    """Whether a call names a location inside the repository.

    Two ways to pass: it names the project's own helper directly, or it passes
    an explicit directory argument -- ``dir=tmp_root``, where ``tmp_root`` was
    computed from ``scratch_dir()`` a few lines up. A call that passes no
    directory at all is the one this guard exists to catch: it gets the system
    temp directory, and the system temp directory is on the system drive.
    """
    return bool(_INSIDE.search(snippet) or _EXPLICIT_DIR.search(snippet))


def test_no_source_file_names_a_temporary_location_outside_the_repository() -> None:
    """Every temporary location the project chooses is inside the repository."""
    unrouted: list[str] = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        for number, snippet in _lines_with_a_chooser(path):
            if relative in _EXCEPTIONS:
                continue
            if _is_routed(snippet):
                continue
            unrouted.append(f"{relative}:{number}  {snippet}")

    assert not unrouted, (
        "these lines choose a temporary location outside the repository:\n  "
        + "\n  ".join(unrouted)
        + "\n\nEvery temporary file the project writes belongs under the "
        "repository's own gitignored `.opspilot/` directory, so that it is "
        "visible, erasable and never on a drive nobody is watching."
    )


def test_the_exception_is_still_an_exception() -> None:
    """The one allowed file is allowed for the stated reason.

    An exception is a hole in the guard, so it is checked rather than trusted:
    the store's temporary file must still be written beside the file it
    replaces, and that file must be inside the repository.
    """
    store = REPO_ROOT / "mcp_servers" / "_store.py"
    source = store.read_text(encoding="utf-8")

    assert "mkstemp(" in source, "the store no longer writes a temporary file at all"
    assert "dir=directory" in source, (
        "the store's temporary file is no longer written beside the file it "
        "replaces; that would make the replacement non-atomic"
    )
    assert _EXCEPTIONS["mcp_servers/_store.py"], "the exception lost its reason"


def test_the_temporary_directory_resolves_inside_the_repository() -> None:
    """The redirect works -- asserted from a test, so after ``conftest.py`` ran.

    This is the assertion that catches the redirect breaking silently: the
    root ``conftest.py`` points ``TMPDIR``/``TEMP``/``TMP`` at the repository
    and clears ``tempfile``'s cache, and this test checks the result.
    """
    resolved = pathlib.Path(tempfile.gettempdir()).resolve()
    assert resolved.is_relative_to(REPO_ROOT), (
        f"the suite's temporary directory is {resolved}, which is outside the "
        f"repository ({REPO_ROOT}). The root conftest.py points it at the "
        "project's own scratch directory; if this fails, that redirect broke."
    )


def test_the_scratch_directory_is_gitignored() -> None:
    """The project's runtime files do not show up in ``git status``.

    Read from ``.gitignore`` rather than by asking ``git``: the question is
    "does the ignore file cover this directory", and that is a question about
    the file's contents. A rule of ``.opspilot/`` covers the directory at any
    depth, which is what the project wants.
    """
    rules = [
        line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]
    covered = any(
        rule.rstrip("/") in {LOCAL_DIR, f"/{LOCAL_DIR}", f"**/{LOCAL_DIR}"}
        for rule in rules
        if rule and not rule.startswith("#")
    )
    assert covered, (
        f"`.gitignore` does not cover `{LOCAL_DIR}/`, so the project's "
        "temporary files would show up as untracked changes"
    )


def test_the_mcp_servers_persist_inside_the_repository() -> None:
    """The servers' live stores default to a directory inside the repository."""
    from mcp_servers._store import default_data_dir

    resolved = default_data_dir(REPO_ROOT / "mcp_servers" / "billing" / "seed.json")
    assert resolved.is_relative_to(REPO_ROOT), (
        f"the billing server's data directory is {resolved}, which is outside the "
        f"repository ({REPO_ROOT}). It used to be derived from the current "
        "working directory, so the same code wrote into the checkout when run "
        "from the checkout and onto the system drive when run from elsewhere."
    )


def test_the_guard_reads_the_source() -> None:
    """The guard is not vacuously green.

    A guard that scans nothing checks nothing, so the number of files it read
    and the number of temporary-location calls it found are both asserted.
    """
    files = _source_files()
    assert len(files) >= 40, f"only {len(files)} source files were scanned"

    choosers = sum(len(_lines_with_a_chooser(path)) for path in files)
    assert choosers >= 5, (
        f"only {choosers} temporary-location calls were found in the tree; the "
        "patterns have probably stopped matching how the code writes them"
    )
