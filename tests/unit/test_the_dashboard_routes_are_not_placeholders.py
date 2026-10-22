"""The dashboard's six routes are real pages, and nothing imports the placeholder.

## Why this file exists

``docs/milestones.md``'s M7 criterion says the dashboard is "six routes under
``web/src/app``, all substantive rather than stubs". It used to *evidence*
that with a line count for each file -- ``page.tsx`` 315 lines,
``runs/[runId]/page.tsx`` 628, ``runs/page.tsx`` 232 -- and three of the six
numbers were wrong by the time anyone read them. The files had grown; the
numbers had not; and the numbers were the only thing standing between the
claim and the reader's trust.

A line count is a number that can only ever drift, and it says nothing about
the property it was standing in for. What the property *is*, is mechanical:
a route that renders a stub imports nothing that reaches the API.

## What this file asserts

1. **There are six route pages**, and they are the six the criterion names.
2. **Every route page imports ``@/lib/data``** -- the layer that talks to the
   API client. A page that imported nothing would be a page that renders
   nothing.
3. **Nothing in the web tree imports ``components/placeholder``.** The file
   exists; the criterion says it is imported by none of the six, and this
   checks the stronger claim -- by nothing at all.
4. **``lib/data.ts`` really does reach the API client**, so the chain the
   criterion relies on is a chain and not a coincidence.

## What it does not do

It does not run the dashboard or count what a page renders. The build claim
in the criterion ("``npm run build`` exits 0 and lists all six as
server-rendered routes") is a fact about a build, and it is checked by the
build itself in CI.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"
APP = WEB / "src" / "app"

#: The six routes the criterion names, as paths relative to ``web/src/app``.
EXPECTED_ROUTES = (
    "page.tsx",
    "runs/page.tsx",
    "runs/[runId]/page.tsx",
    "approvals/page.tsx",
    "knowledge/page.tsx",
    "tickets/page.tsx",
)

#: The import that makes a page reach the API.
_DATA_LAYER = re.compile(r"""from\s+["']@/lib/data["']""")

#: The placeholder the criterion says nothing imports.
_PLACEHOLDER = re.compile(r"""(?:from|import)\s+["'][^"']*components/placeholder["']""")


def _route_pages() -> list[pathlib.Path]:
    """Every ``page.tsx`` under ``web/src/app``."""
    return sorted(APP.rglob("page.tsx"))


def test_there_are_exactly_six_route_pages() -> None:
    """The criterion names six routes, and the tree has six."""
    pages = _route_pages()
    relative = sorted(path.relative_to(APP).as_posix() for path in pages)

    assert relative == sorted(EXPECTED_ROUTES), (
        "the dashboard's route pages are not the six the criterion names:\n"
        f"  found: {relative}\n"
        f"  named: {sorted(EXPECTED_ROUTES)}"
    )


def test_every_route_reaches_the_api() -> None:
    """A route that imports nothing cannot render anything."""
    pages = _route_pages()
    assert pages, "no route pages were found under web/src/app"

    silent = [
        path.relative_to(APP).as_posix()
        for path in pages
        if not _DATA_LAYER.search(path.read_text(encoding="utf-8"))
    ]
    assert not silent, (
        "these route pages do not import the data layer, so they render "
        f"nothing but a stub: {silent}"
    )


def test_nothing_imports_the_placeholder() -> None:
    """``components/placeholder.tsx`` is imported by nothing."""
    placeholder = WEB / "src" / "components" / "placeholder.tsx"
    assert placeholder.is_file(), (
        f"{placeholder} does not exist; the criterion says it is imported by "
        "none of the six routes, which is only a claim worth making while the "
        "file is there"
    )

    importers = [
        path.relative_to(WEB).as_posix()
        for path in sorted((WEB / "src").rglob("*.ts"))
        if path.is_file() and path != placeholder
        for _ in [0]
        if _PLACEHOLDER.search(path.read_text(encoding="utf-8"))
    ]
    importers += [
        path.relative_to(WEB).as_posix()
        for path in sorted((WEB / "src").rglob("*.tsx"))
        if path.is_file()
        and path != placeholder
        and _PLACEHOLDER.search(path.read_text(encoding="utf-8"))
    ]

    assert not importers, (
        "these files import the placeholder component, so the dashboard's "
        f"routes are not all substantive: {importers}"
    )


def test_the_data_layer_reaches_the_api_client() -> None:
    """The chain the criterion rests on is a chain."""
    data_layer = WEB / "src" / "lib" / "data.ts"
    assert data_layer.is_file(), f"{data_layer} does not exist"

    source = data_layer.read_text(encoding="utf-8")
    assert "api/client" in source, (
        "web/src/lib/data.ts does not import the API client, so a route that "
        "imports the data layer still renders nothing"
    )
