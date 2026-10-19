"""The committed artwork must still be what the artwork generates.

`desktop/assets/icon.ico`, `web/src/app/favicon.ico`,
`web/src/app/apple-icon.png` and `docs/assets/logo.png` are build artefacts
that live in the repository: `scripts/make_assets.py` generates the icon, the
favicon and the apple-touch icon from `docs/assets/icon-source.png` and the
wordmark from `docs/assets/logo-source.png`, `desktop/electron-builder.yml`
points the Windows build at the committed `.ico`, the root README carries the
committed `.png`, and Next.js picks the committed favicon and the committed
apple-touch icon up by the App Router's file convention. Nothing in the build
regenerates them, so they drift the moment the artwork changes and nobody
reruns the script -- and the drift is invisible in a review, because a binary
is not something a diff shows. The icon that ships is then the badge from
three artwork revisions ago, and the only symptom is that nobody can say why
the icon looks wrong.

The favicon is the one that needed this most. Nothing in the dashboard's
source names `web/src/app/favicon.ico`: Next.js finds it by the file
convention alone, so there is no reference to follow and no compiler to
complain. A favicon three artwork revisions old is exactly as invisible as
the icon was, and it is the one an operator sees in every browser tab.

The wordmark was the same failure with less of it: it had a committed
artefact and a reference to it, and neither a source nor a guard. That is the
icon's own arrangement one file over, and it is why the wordmark's source is
now in the tree and why the same check covers it.

This is the same species of failure as the dashboard's generated client types
(`tests/integration/test_web_client_types.py`): a copy that is meant to be
derived from a source, and is not. The precedent is followed here in the way
that matters -- the check calls the builder's own `--check` mode rather than
reimplementing the comparison here. A guard that regenerated the artefact
itself and compared it against its own logic would be testing that guard's
logic, not that the committed artefact is current.

The icon has four things checked:

1. **The committed icon is what the artwork generates.** `make_assets.py
   --check` regenerates the icon into a temporary directory and compares it
   with the committed file -- picture against picture, not byte against byte
   -- and never writes to that file.
2. **The guard is not empty.** The check is run against an icon built in a
   temporary directory from different artwork, and it has to fail. A check
   that cannot fail is not a check, and the real icon is never touched to
   prove that it can.
3. **The committed icon carries every size Windows may ask for.** Those ten
   sizes are the builder's own invariant, and until now only the builder
   asserted them: an icon that was hand-edited and lost a size would have
   gone unnoticed, because nothing outside the script ever looked at what the
   committed file contains.
4. **A different compressor is not a drift.** The frames of an `.ico` are
   PNGs, and the bytes of a PNG depend on the zlib that packed it, which is
   not the same zlib on a CI runner as on the machine that committed the
   file.

The favicon has four:

1. **The committed favicon is what the artwork generates**, and the check
   does not write to it.
2. **The guard is not empty for it either**: a favicon built in a temporary
   directory from different artwork has to be reported as stale, and the
   report has to name the favicon. The favicon and the shell's icon are the
   same picture, so a report that said "the icon" would send the reader to
   the wrong file -- and a report that named neither would be a report
   nobody could act on.
3. **The check tells the two icons apart.** Both are `.ico` files holding
   the same picture, and the only thing that distinguishes them is the
   sizes they pack. A check that could not tell them apart would either
   compare the favicon against the icon and call it stale forever, or
   compare both against the icon and call the favicon's five sizes a
   difference.
4. **It carries the five sizes a browser may ask for** -- 16, 32, 48, 64
   and 256 -- and nothing else. The 256 is the frame a reader takes when
   it reads the `.ico` for a single picture, and it is most of the file:
   an `.ico` is fetched whole, so its 80,707 bytes of PNG are downloaded
   whether the browser wanted that size or not.

And it has the favicon's own invariant, which the icon's rule does not
express: the corners are transparent and the middle is opaque. The favicon
sits in a browser's tab strip, where an opaque white square is a bright
block beside the other tabs, and the badge in the middle of it is the
drawing -- a hole where the badge should be is not a favicon either.

The apple-touch icon has four, and the first three are the favicon's:

1. **The committed apple-touch icon is what the artwork generates**, and the
   check does not write to it.
2. **The guard is not empty for it either**: an apple-touch icon built in a
   temporary directory from different artwork has to be reported as stale, and
   the report has to name the apple-touch icon rather than the wordmark --
   the two are both PNGs, and a report that named the wrong one would send
   the reader to the wrong file.
3. **The check tells it from the wordmark.** Both are PNGs and both are
   opaque, and the only thing that distinguishes them is the size: the
   apple-touch icon is written at the 180x180 iOS asks for and the wordmark
   is the wide lockup. A check that could not tell them apart would compare
   the apple-touch icon against the wordmark and call it stale forever.
4. **It is a filled square, which is the opposite of the favicon's
   invariant.** iOS puts the picture in an opaque container and masks the top
   of it, so a transparent corner is a hole that shows the container's black
   through the badge. Every pixel of the apple-touch icon has to be opaque,
   and its corners are the badge's own blue rather than the transparent
   corners the favicon is required to have.

The wordmark has three:

1. **The committed wordmark is what the artwork generates**, and the check
   does not write to it.
2. **The guard is not empty for it either**: a wordmark built in a temporary
   directory from different artwork has to be reported as stale, and the
   report has to name the wordmark rather than the icon -- a check that
   cannot say which artefact it found stale is a check that cannot be acted
   on.
3. **It is still a wordmark.** The properties the check cannot express, and
   that the builder's rule alone would not pin down, are asserted here: the
   wordmark is drawn on an opaque white ground rather than on transparency
   (a transparent wordmark puts its dark half on a dark background), it is
   a horizontal lockup rather than a square, and it carries no leftover
   whitespace from the export it was cropped out of.

Reading an ICO back needs Pillow, which is in the repository's `dev` extra
and is therefore present wherever the suite is run from a checkout.
"""

from __future__ import annotations

import io
import pathlib
import struct
import subprocess
import sys

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageChops

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BUILDER = _REPO_ROOT / "scripts" / "make_assets.py"
_ARTWORK = _REPO_ROOT / "docs" / "assets" / "icon-source.png"
_ICON = _REPO_ROOT / "desktop" / "assets" / "icon.ico"
_FAVICON = _REPO_ROOT / "web" / "src" / "app" / "favicon.ico"
_APPLE = _REPO_ROOT / "web" / "src" / "app" / "apple-icon.png"
_WORDMARK_ARTWORK = _REPO_ROOT / "docs" / "assets" / "logo-source.png"
_WORDMARK = _REPO_ROOT / "docs" / "assets" / "logo.png"

#: The sizes Windows may ask the shell for. Written out here rather than
#: imported from the builder: a test that reads its expectation from the thing
#: it checks passes whatever that thing happens to say today.
EXPECTED_SIZES = frozenset(
    {
        (16, 16),
        (20, 20),
        (24, 24),
        (32, 32),
        (40, 40),
        (48, 48),
        (64, 64),
        (96, 96),
        (128, 128),
        (256, 256),
    }
)

#: The sizes a browser may ask a favicon for, and the one a reader takes
#: when it reads the `.ico` for a single picture. Written out here for the
#: same reason as the sizes above, and for the same reason it matters more:
#: these five are the whole contract with the browser, and the favicon's
#: identity is carried by its size set -- it is what tells the favicon apart
#: from the shell's icon, which holds the same picture at ten sizes.
EXPECTED_FAVICON_SIZES = frozenset(
    {
        (16, 16),
        (32, 32),
        (48, 48),
        (64, 64),
        (256, 256),
    }
)

#: The alpha a favicon's corners must have: nothing at all. The favicon sits
#: in a browser's tab strip, and an opaque white square there is a bright
#: block beside the other tabs.
_TRANSPARENT = 0

#: The alpha the middle of a favicon must have: full. The badge is the
#: drawing, and a hole where the badge should be is not a favicon.
_OPAQUE = 255

#: The size iOS asks an apple-touch icon at. Apple's spec names one size,
#: and Next.js's metadata image loader writes the picture's real dimensions
#: into the `sizes` attribute of the link tag, so a 180x180 picture is
#: announced as `sizes="180x180"` and any other size is announced as one
#: Apple does not ask for. Written out here rather than imported from the
#: builder, for the same reason the sizes above are.
EXPECTED_APPLE_SIZE = (180, 180)

#: How far from white a pixel may be and still count as the drawing rather
#: than the export's ground. The same measure the builder uses to find the
#: wordmark's band, written out here for the same reason the sizes above are:
#: a test that reads its expectation out of the thing it checks proves
#: nothing.
_DRAWING_TOLERANCE = 8

#: How much bare ground the wordmark may keep around the drawing, at most.
#: The builder's rule is a small fixed margin, and this is a generous band
#: around it: a wordmark with a wide white border is the export's
#: whitespace that the crop failed to remove.
_MAX_WHITESPACE = 16

#: The wordmark is a horizontal lockup -- the badge beside the name -- and
#: not a square and not a banner. The bounds are wide on purpose: they say
#: "wide and flat", and leave the drawing room to be redrawn.
_MIN_ASPECT = 3.0
_MAX_ASPECT = 3.6


def test_the_committed_icon_is_current_with_the_artwork() -> None:
    """The committed icon is exactly what the artwork generates.

    Runs the builder's own ``--check`` mode rather than reimplementing the
    comparison here, for the reason the file's docstring gives: a guard that
    regenerated the icon itself and compared it against its own logic would be
    testing that guard's logic, not that the committed artefact is current.

    The icon is named explicitly. ``--check`` with no path covers every
    artefact, and a failure in the wordmark would otherwise be reported
    against a test about the icon.
    """
    before = _ICON.read_bytes()

    result = _run_builder("--check", _ICON)

    assert result.returncode == 0, (
        "desktop/assets/icon.ico has drifted from docs/assets/icon-source.png.\n"
        "Run `python scripts/make_assets.py icon` and commit the result.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    # The check must not have written the file it was checking. A check that
    # repairs the tree it is checking is not a check: it would make the
    # failure invisible in CI, where the working tree comes back clean.
    assert _ICON.read_bytes() == before, (
        "`--check` modified desktop/assets/icon.ico. A check that repairs the "
        "tree it is checking is not a check -- the failure it exists to report "
        "would be invisible in CI, where the working tree comes back clean."
    )


def test_the_check_fails_on_an_icon_the_artwork_does_not_generate(tmp_path: pathlib.Path) -> None:
    """The guard is not empty: a stale icon is reported, not waved through.

    The stale icon is built in a temporary directory from a different piece of
    artwork, and the real ``desktop/assets/icon.ico`` is never touched. A
    guard that proved itself by corrupting the artefact it guards would be
    trading a false green for a false red.
    """
    # A different piece of artwork: the same ten sizes, different pixels, so
    # the comparison fails on the frames rather than on the file structure.
    other_artwork = tmp_path / "other.png"
    Image.new("RGBA", (256, 256), (255, 0, 255, 255)).save(other_artwork)
    stale = tmp_path / "icon.ico"
    # The asset is named first: one script builds two artefacts, so the
    # command says which one it is building. What is asserted here has not
    # changed -- a stale icon still has to be reported.
    built = _run_builder("icon", other_artwork, stale)
    assert built.returncode == 0, (
        "the builder could not produce the icon to compare against.\n\n"
        f"{built.stdout}\n{built.stderr}"
    )

    result = _run_builder("--check", stale)

    assert result.returncode != 0, (
        "the check reported success for an icon the artwork does not generate, "
        "so the guard is empty.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "16x16" in result.stderr, (
        "the check failed but did not say which sizes differ, so the failure "
        "cannot be acted on.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_check_takes_only_the_icon_to_compare() -> None:
    """`--check` names the artefact, and refuses a second path.

    The check compares an artefact against one regenerated from the artwork
    the tree owns. Were it allowed to name the artwork as well, it could be
    pointed at an artefact built from something else and a match against the
    wrong source would read as a pass -- which is the failure the check
    exists to prevent, wearing the check's own clothes.
    """
    result = _run_builder("--check", _ARTWORK, _ICON)

    assert result.returncode != 0, (
        "`--check` accepted two paths. The second one is the artwork, and a "
        "check that can be pointed at its own source can be made to pass "
        "against the wrong icon.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "one path" in result.stderr, (
        "the check rejected the arguments without saying what it wanted.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_check_compares_pictures_and_not_bytes(tmp_path: pathlib.Path) -> None:
    """A different compressor is not a drift.

    The frames of an ``.ico`` are PNGs, and what a PNG encoder writes for an
    image depends on the compressor: the same pixels at compression levels
    6, 7 and 9 come out as 80,707, 77,475 and 75,678 bytes. So the bytes of
    an icon depend on the zlib that packed them, and the zlib on a CI runner
    is not the zlib on the machine that committed the file. A byte
    comparison would then call an unchanged tree stale, and its remedy --
    regenerate the icon -- would change the file and not the problem.

    So the icon here is repacked with a different compression level, which
    makes the same pictures into different bytes, and the check has to call
    it current.
    """
    repacked = tmp_path / "repacked.ico"
    _repack_with_a_different_compressor(_ICON, repacked)

    assert repacked.read_bytes() != _ICON.read_bytes(), (
        "the repack did not change the bytes, so this test is not exercising "
        "the difference it claims to."
    )

    result = _run_builder("--check", repacked)

    assert result.returncode == 0, (
        "the check called an icon stale because its frames were compressed "
        "differently. The frames are the same pictures, and the zlib on a CI "
        "runner is not the zlib on the machine that committed the file, so "
        "this is how an unchanged tree would fail.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_committed_icon_carries_every_size_windows_may_ask_for() -> None:
    """The committed icon holds all ten sizes, and nothing else.

    The sizes are the builder's own invariant, and only the builder asserted
    them: an icon that was hand-edited and lost a size would have gone
    unnoticed, because nothing outside the script ever looked at what the
    committed file contains. Each of the ten is a different rendering Windows
    asks for -- the taskbar, the Start Menu, Explorer's icon view, the Alt-Tab
    switcher -- and a missing one is a blurry render rather than a failure.
    """
    assert _ICON.exists(), (
        "desktop/assets/icon.ico is missing. desktop/electron-builder.yml points "
        "the Windows build at it, so there would be no icon to ship."
    )
    with Image.open(_ICON) as image:
        found = {(int(width), int(height)) for width, height in image.info.get("sizes", ())}

    assert found == EXPECTED_SIZES, (
        f"desktop/assets/icon.ico contains {sorted(found)}, not "
        f"{sorted(EXPECTED_SIZES)}. Every size Windows may ask for has to be in "
        "the one file the packaging configuration points at: the taskbar, the "
        "Start Menu, Explorer's icon view and the Alt-Tab switcher each want a "
        "different one, and Windows picks by what it is rendering rather than "
        "by asking."
    )


def test_the_committed_favicon_is_current_with_the_artwork() -> None:
    """The committed favicon is exactly what the artwork generates.

    The same contract as the icon, and for a sharper reason: nothing in the
    dashboard's source names this file. Next.js finds it by the App Router's
    file convention, so there is no reference to follow, no import to fail
    and no compiler to complain. A favicon that is three artwork revisions
    old is exactly as invisible as the icon was.
    """
    before = _FAVICON.read_bytes()

    result = _run_builder("--check", _FAVICON)

    assert result.returncode == 0, (
        "web/src/app/favicon.ico has drifted from docs/assets/icon-source.png.\n"
        "Run `python scripts/make_assets.py favicon` and commit the result.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert _FAVICON.read_bytes() == before, (
        "`--check` modified web/src/app/favicon.ico. A check that repairs the "
        "tree it is checking is not a check -- the failure it exists to report "
        "would be invisible in CI, where the working tree comes back clean."
    )


def test_the_check_fails_on_a_favicon_the_artwork_does_not_generate(
    tmp_path: pathlib.Path,
) -> None:
    """The guard is not empty for the favicon either.

    The stale favicon is built in a temporary directory from a different
    piece of artwork, and the real ``web/src/app/favicon.ico`` is never
    touched.

    The report has to name the favicon. The favicon and the shell's icon are
    the same picture at the same sizes, so a report that said "the icon"
    would send the reader to the wrong file, and one that named neither
    would be a report nobody could act on.
    """
    other_artwork = tmp_path / "other.png"
    Image.new("RGBA", (256, 256), (255, 0, 255, 255)).save(other_artwork)
    stale = tmp_path / "favicon.ico"
    built = _run_builder("favicon", other_artwork, stale)
    assert built.returncode == 0, (
        "the builder could not produce the favicon to compare against.\n\n"
        f"{built.stdout}\n{built.stderr}"
    )

    result = _run_builder("--check", stale)

    assert result.returncode != 0, (
        "the check reported success for a favicon the artwork does not "
        "generate, so the guard is empty.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "the committed favicon is stale" in result.stderr, (
        "the check failed but did not say which artefact was stale, so the "
        "failure cannot be acted on.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_check_tells_the_shell_icon_from_the_favicon(tmp_path: pathlib.Path) -> None:
    """Two `.ico` files holding the same picture are told apart by their sizes.

    The favicon and the shell's icon are the same badge, cut the same way and
    resampled the same way, so the pixels cannot say which one a file is: the
    favicon's 16, 32, 48, 64 and 256 frames *are* the icon's 16, 32, 48, 64
    and 256 frames. What differs is the sizes packed into the file -- five
    against ten -- and that is the artefact's contract with whatever
    consumes it.

    The copies here live in a temporary directory, so neither is one of the
    committed targets and the check has to work the size set out for itself.
    A check that always took the icon would report the favicon copy as
    stale; one that always took the favicon would report the icon copy as
    stale. Both would be a guard that fails on a correct tree, which is
    worse than no guard at all, because it sends the developer to regenerate
    a file that is already right.
    """
    icon_copy = tmp_path / "icon.ico"
    favicon_copy = tmp_path / "favicon.ico"
    for asset, target in (("icon", icon_copy), ("favicon", favicon_copy)):
        built = _run_builder(asset, _ARTWORK, target)
        assert built.returncode == 0, (
            f"the builder could not produce the {asset} to compare against.\n\n"
            f"{built.stdout}\n{built.stderr}"
        )

    checked_icon = _run_builder("--check", icon_copy)
    assert checked_icon.returncode == 0, (
        "the check called a copy of the shell's icon stale. The copy holds "
        "the ten sizes the icon holds, so it is the icon.\n\n"
        f"{checked_icon.stdout}\n{checked_icon.stderr}"
    )
    assert "the committed icon is current" in checked_icon.stdout, (
        "the check did not report the ten-size file as the icon.\n\n"
        f"{checked_icon.stdout}\n{checked_icon.stderr}"
    )

    checked_favicon = _run_builder("--check", favicon_copy)
    assert checked_favicon.returncode == 0, (
        "the check called a copy of the favicon stale. The copy holds the "
        "five sizes the favicon holds, so it is the favicon.\n\n"
        f"{checked_favicon.stdout}\n{checked_favicon.stderr}"
    )
    assert "the committed favicon is current" in checked_favicon.stdout, (
        "the check did not report the five-size file as the favicon. It was "
        "compared against the shell's icon instead, and the difference in "
        "sizes was read as a drift.\n\n"
        f"{checked_favicon.stdout}\n{checked_favicon.stderr}"
    )


def test_the_committed_favicon_carries_the_sizes_a_browser_may_ask_for() -> None:
    """The committed favicon holds the five sizes a browser may ask for.

    Sixteen is the Windows tab strip and the bookmark bar; thirty-two is the
    macOS tab and the macOS bookmark; forty-eight is Windows' medium icon
    view; sixty-four is what a high-density screen asks for.

    The 256 is there for a different reason, and the reason is a decision
    rather than an oversight: a reader that opens the `.ico` and wants a
    single picture out of it is handed the largest frame, and a high-zoom or
    large-icon view wants that size too. The price is written down in
    `desktop/README.md` and in the builder's docstring -- an `.ico` is
    fetched whole, so the 256 frame's 80,707 bytes of PNG are downloaded
    whether the browser wanted that size or not, and the artefact is 98,231
    bytes rather than 17,508.
    """
    assert _FAVICON.exists(), (
        "web/src/app/favicon.ico is missing. Next.js's App Router finds it by "
        "the file convention, so without it the dashboard serves the Next "
        "template's placeholder instead."
    )
    with Image.open(_FAVICON) as image:
        found = {(int(width), int(height)) for width, height in image.info.get("sizes", ())}

    assert found == EXPECTED_FAVICON_SIZES, (
        f"web/src/app/favicon.ico contains {sorted(found)}, not "
        f"{sorted(EXPECTED_FAVICON_SIZES)}. These five are the whole contract "
        "with the browser, and the size set is what tells the favicon apart "
        "from the shell's icon, which holds the same picture at ten sizes."
    )


def test_the_favicon_is_a_badge_and_not_a_white_square() -> None:
    """The favicon's corners are transparent and its middle is solid.

    The icon's white is cut away, and the favicon is cut the same way: it
    sits in a browser's tab strip beside the other tabs, and an opaque white
    square there is a bright block in the middle of the strip. The badge in
    the middle is opaque, because the badge is the drawing -- a hole where
    the badge should be is not a favicon either.
    """
    for size, frame in _icon_frames(_FAVICON):
        alpha = frame.getchannel("A")
        corners = {
            "top left": alpha.getpixel((0, 0)),
            "top right": alpha.getpixel((frame.width - 1, 0)),
            "bottom left": alpha.getpixel((0, frame.height - 1)),
            "bottom right": alpha.getpixel((frame.width - 1, frame.height - 1)),
        }
        for corner, value in corners.items():
            assert value == _TRANSPARENT, (
                f"the {size[0]}x{size[1]} frame of web/src/app/favicon.ico has an "
                f"opaque {corner} (alpha {value}). The favicon is the badge on "
                "transparency: a white square in a browser's tab strip is a "
                "bright block beside the other tabs."
            )

        centre = alpha.getpixel((frame.width // 2, frame.height // 2))
        assert centre == _OPAQUE, (
            f"the {size[0]}x{size[1]} frame of web/src/app/favicon.ico is "
            f"transparent in the middle (alpha {centre}). The badge is the "
            "drawing, and a hole where the badge should be is not a favicon."
        )


def test_the_committed_apple_touch_icon_is_current_with_the_artwork() -> None:
    """The committed apple-touch icon is exactly what the artwork generates.

    The same contract as the favicon, and for a sharper reason: nothing in
    the dashboard's source names this file either. Next.js finds it by the
    App Router's file convention, so an apple-touch icon that is three
    artwork revisions old is exactly as invisible as the favicon was.
    """
    before = _APPLE.read_bytes()

    result = _run_builder("--check", _APPLE)

    assert result.returncode == 0, (
        "web/src/app/apple-icon.png has drifted from docs/assets/icon-source.png.\n"
        "Run `python scripts/make_assets.py apple` and commit the result.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert _APPLE.read_bytes() == before, (
        "`--check` modified web/src/app/apple-icon.png. A check that repairs "
        "the tree it is checking is not a check -- the failure it exists to "
        "report would be invisible in CI, where the working tree comes back clean."
    )


def test_the_check_fails_on_an_apple_touch_icon_the_artwork_does_not_generate(
    tmp_path: pathlib.Path,
) -> None:
    """The guard is not empty for the apple-touch icon either.

    The stale icon is built in a temporary directory from a different piece
    of artwork, and the real ``web/src/app/apple-icon.png`` is never
    touched.

    The report has to name the apple-touch icon. It and the wordmark are
    both PNGs, and a report that said "the wordmark" would send the reader
    to the wrong file -- and one that named neither would be a report
    nobody could act on.
    """
    other_artwork = tmp_path / "other.png"
    Image.new("RGBA", (256, 256), (255, 0, 255, 255)).save(other_artwork)
    stale = tmp_path / "apple-icon.png"
    built = _run_builder("apple", other_artwork, stale)
    assert built.returncode == 0, (
        "the builder could not produce the apple-touch icon to compare against.\n\n"
        f"{built.stdout}\n{built.stderr}"
    )

    result = _run_builder("--check", stale)

    assert result.returncode != 0, (
        "the check reported success for an apple-touch icon the artwork does "
        "not generate, so the guard is empty.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "the committed apple-touch icon is stale" in result.stderr, (
        "the check failed but did not say which artefact was stale, so the "
        "failure cannot be acted on.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_check_tells_the_apple_touch_icon_from_the_wordmark(tmp_path: pathlib.Path) -> None:
    """Two PNGs holding opaque pictures are told apart by their size.

    The apple-touch icon and the wordmark are both PNGs and both are
    opaque -- the wordmark is drawn on white and the apple-touch icon is a
    filled square -- so the pixels cannot say which one a file is. What
    can is the size: the apple-touch icon is written at the 180x180 iOS
    asks for, and the wordmark is the wide lockup whose size is a property
    of the drawing it was cropped out of.

    The copies here live in a temporary directory, so neither is one of the
    committed targets and the check has to work the size out for itself. A
    check that always took the wordmark would report the apple-touch icon
    copy as stale; one that always took the apple-touch icon would report
    the wordmark copy as stale. Both would be a guard that fails on a
    correct tree, which is worse than no guard at all, because it sends
    the developer to regenerate a file that is already right.
    """
    apple_copy = tmp_path / "apple-icon.png"
    wordmark_copy = tmp_path / "wordmark.png"
    for asset, source, target in (
        ("apple", _ARTWORK, apple_copy),
        ("wordmark", _WORDMARK_ARTWORK, wordmark_copy),
    ):
        built = _run_builder(asset, source, target)
        assert built.returncode == 0, (
            f"the builder could not produce the {asset} to compare against.\n\n"
            f"{built.stdout}\n{built.stderr}"
        )

    checked_apple = _run_builder("--check", apple_copy)
    assert checked_apple.returncode == 0, (
        "the check called a copy of the apple-touch icon stale. The copy is "
        "the size iOS asks for, so it is the apple-touch icon.\n\n"
        f"{checked_apple.stdout}\n{checked_apple.stderr}"
    )
    assert "the committed apple-touch icon is current" in checked_apple.stdout, (
        "the check did not report the 180x180 file as the apple-touch icon. "
        "It was compared against the wordmark instead.\n\n"
        f"{checked_apple.stdout}\n{checked_apple.stderr}"
    )

    checked_wordmark = _run_builder("--check", wordmark_copy)
    assert checked_wordmark.returncode == 0, (
        "the check called a copy of the wordmark stale. The copy is the wide "
        "lockup, so it is the wordmark.\n\n"
        f"{checked_wordmark.stdout}\n{checked_wordmark.stderr}"
    )
    assert "the committed wordmark is current" in checked_wordmark.stdout, (
        "the check did not report the wide file as the wordmark. It was "
        "compared against the apple-touch icon instead.\n\n"
        f"{checked_wordmark.stdout}\n{checked_wordmark.stderr}"
    )


def test_the_apple_touch_icon_is_the_size_ios_asks_for() -> None:
    """The committed apple-touch icon is exactly 180x180.

    Apple's spec names one size for an apple-touch icon, and the size is
    also what the page says it is: Next.js's metadata image loader writes
    the picture's real dimensions into the `sizes` attribute of the link
    tag, so a 180x180 picture is announced as ``sizes="180x180"`` and a
    larger one is announced as a size Apple does not ask for.
    """
    assert _APPLE.exists(), (
        "web/src/app/apple-icon.png is missing. Next.js's App Router finds it "
        "by the file convention, so without it iOS falls back to a screenshot "
        "of the page."
    )
    with Image.open(_APPLE) as image:
        found = image.size

    assert found == EXPECTED_APPLE_SIZE, (
        f"web/src/app/apple-icon.png is {found[0]}x{found[1]}, not "
        f"{EXPECTED_APPLE_SIZE[0]}x{EXPECTED_APPLE_SIZE[1]}. The size is the "
        "whole contract with iOS: the page announces the picture's real "
        "dimensions as its `sizes`, and a size Apple does not ask for is a "
        "size the home screen will not use."
    )


def test_the_apple_touch_icon_is_a_filled_square() -> None:
    """Every pixel of the apple-touch icon is opaque, the corners included.

    iOS puts the picture in an opaque container and masks the top of it
    with its own rounded rectangle, so the picture itself has to be a
    completely filled square: a transparent corner is a hole that shows the
    container's black through the badge, and the badge with four black
    corners is not the mark. This is the opposite of the favicon's
    invariant, and for the same reason -- both are about what the thing
    the picture is put in shows through.

    The corners are the badge's own blue and not white, which is the other
    way the fill could go wrong: a white corner is a glaring square inside
    a dark icon, and the badge's edge is a saturated blue.
    """
    with Image.open(_APPLE) as image:
        apple = image.convert("RGBA")

    lowest = apple.getchannel("A").getextrema()[0]
    assert lowest == _OPAQUE, (
        "web/src/app/apple-icon.png has transparent pixels (the lowest alpha "
        f"is {lowest}). iOS puts the picture in an opaque container and masks "
        "the top of it, so a transparent corner is a hole that shows the "
        "container's black through the badge."
    )

    bands = apple.split()
    alpha_band = bands[3]
    corners = {
        "top left": (0, 0),
        "top right": (apple.width - 1, 0),
        "bottom left": (0, apple.height - 1),
        "bottom right": (apple.width - 1, apple.height - 1),
    }
    for corner, (x, y) in corners.items():
        corner_alpha = alpha_band.getpixel((x, y))
        assert corner_alpha == _OPAQUE, (
            f"the {corner} of web/src/app/apple-icon.png is transparent (alpha "
            f"{corner_alpha}). The picture is a filled square: the corners "
            "are the badge's own colour, and iOS's mask is the only rounding "
            "this icon gets."
        )
        pixel = apple.getpixel((x, y))
        assert isinstance(pixel, tuple), (
            f"the {corner} of web/src/app/apple-icon.png could not be read as a colour: {pixel}"
        )
        red, _green, blue, _alpha = pixel
        assert blue != red, (
            f"the {corner} of web/src/app/apple-icon.png is {pixel[:3]}, which "
            "is not the badge's blue. The corner is filled with the badge's "
            "own colour continued outwards, and a white corner would be a "
            "glaring square inside a dark icon."
        )


def test_the_committed_wordmark_is_current_with_the_artwork() -> None:
    """The committed wordmark is exactly what the artwork generates.

    The same contract as the icon, and for the same reason: the check is the
    builder's own, and the file being checked is not written to.
    """
    before = _WORDMARK.read_bytes()

    result = _run_builder("--check", _WORDMARK)

    assert result.returncode == 0, (
        "docs/assets/logo.png has drifted from docs/assets/logo-source.png.\n"
        "Run `python scripts/make_assets.py wordmark` and commit the "
        "result.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert _WORDMARK.read_bytes() == before, (
        "`--check` modified docs/assets/logo.png. A check that repairs the "
        "tree it is checking is not a check -- the failure it exists to report "
        "would be invisible in CI, where the working tree comes back clean."
    )


def test_the_check_fails_on_a_wordmark_the_artwork_does_not_generate(
    tmp_path: pathlib.Path,
) -> None:
    """The guard is not empty for the wordmark either.

    The stale wordmark is built in a temporary directory from a different
    piece of artwork -- the icon's own export, which is a square and not a
    lockup -- and the real ``docs/assets/logo.png`` is never touched.

    The report has to name the wordmark. A check that reported a stale
    wordmark without saying which artefact it was talking about would leave
    the reader to guess, and the two artefacts are named the same way in the
    tree.
    """
    stale = tmp_path / "wordmark.png"
    built = _run_builder("wordmark", _ARTWORK, stale)
    assert built.returncode == 0, (
        "the builder could not produce the wordmark to compare against.\n\n"
        f"{built.stdout}\n{built.stderr}"
    )

    result = _run_builder("--check", stale)

    assert result.returncode != 0, (
        "the check reported success for a wordmark the artwork does not "
        "generate, so the guard is empty.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "the committed wordmark is stale" in result.stderr, (
        "the check failed but did not say which artefact was stale, so the "
        "failure cannot be acted on.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_check_without_a_path_covers_every_artefact() -> None:
    """One check, with no path, is the whole tree's guard.

    This is the command CI runs, and it has to reach both artefacts: the
    wordmark used to have a committed file and a reference to it and no
    guard, and a check that only looked at the icon would have let that
    stand.
    """
    result = _run_builder("--check")

    assert result.returncode == 0, (
        f"the check over every artefact failed.\n\n{result.stdout}\n{result.stderr}"
    )
    assert "the committed icon is current" in result.stdout, (
        f"the check did not report on the icon.\n\n{result.stdout}\n{result.stderr}"
    )
    assert "the committed favicon is current" in result.stdout, (
        "the check did not report on the favicon, so the favicon is still an "
        "orphan: a committed artefact with no reference to it anywhere in the "
        "source, because Next.js finds it by the file convention alone, and "
        "nothing that would notice it drifting.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "the committed apple-touch icon is current" in result.stdout, (
        "the check did not report on the apple-touch icon, so it is still an "
        "orphan in the same way the favicon was: Next.js finds it by the file "
        "convention alone, and nothing that would notice it drifting.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "the committed wordmark is current" in result.stdout, (
        "the check did not report on the wordmark, so the wordmark is still "
        "an orphan: a committed artefact with a reference to it and nothing "
        "that would notice it drifting.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_wordmark_is_drawn_on_an_opaque_white_ground() -> None:
    """The wordmark keeps its white.

    The icon's white is cut away, because an icon is a badge and a badge
    with a white square around it is a white square with a badge in it. The
    wordmark is the opposite case: it is a dark badge and a dark name on
    white, and a transparent one would put its dark half on whatever is
    behind it -- unreadable on a dark page. The white is therefore part of
    the drawing, and the artefact has to carry it.
    """
    with Image.open(_WORDMARK) as image:
        alpha = image.getchannel("A")

    lowest = alpha.getextrema()[0]
    assert lowest == 255, (
        "docs/assets/logo.png has transparent pixels (the darkest alpha is "
        f"{lowest}). The wordmark is drawn on white on purpose: a transparent "
        "wordmark puts its dark half on whatever is behind it, and the name "
        "is the part that has to be readable."
    )


def test_the_wordmark_carries_no_whitespace_from_the_export() -> None:
    """The crop removed the export's whitespace, and kept a margin.

    The export is a square with the lockup across the middle of it, and most
    of that square is not artwork. The wordmark is the artwork plus a small
    margin of bare ground -- not flush against its own outline, which reads
    as a mistake, and not carrying the export's whitespace, which is what
    the crop exists to remove.
    """
    with Image.open(_WORDMARK) as image:
        wordmark = image.convert("RGB")

    box = _drawing_box(wordmark)
    assert box is not None, (
        "docs/assets/logo.png is blank: every pixel in it is the export's "
        "ground, so there is no wordmark to read."
    )
    left, top, right, bottom = box
    margins = {
        "left": left,
        "top": top,
        "right": wordmark.width - right,
        "bottom": wordmark.height - bottom,
    }
    for side, margin in margins.items():
        assert 1 <= margin <= _MAX_WHITESPACE, (
            f"docs/assets/logo.png has {margin} pixels of {side} whitespace. "
            "The wordmark is cropped out of an export that is mostly white "
            f"canvas, and the crop leaves a margin of at most "
            f"{_MAX_WHITESPACE} pixels: more than that is the export's "
            "whitespace that the crop failed to remove, and less than that "
            "is the drawing cut off at the edge."
        )


def test_the_wordmark_is_a_horizontal_lockup() -> None:
    """The wordmark is wide and flat: the badge beside the name.

    The bounds are wide on purpose. They say "wide and flat" and leave the
    drawing room to be redrawn -- what they rule out is a crop that kept
    only the badge, or one that kept a stripe of the canvas.
    """
    with Image.open(_WORDMARK) as image:
        width, height = image.size

    aspect = width / height
    assert _MIN_ASPECT <= aspect <= _MAX_ASPECT, (
        f"docs/assets/logo.png is {width}x{height}, an aspect of "
        f"{aspect:.2f}:1, which is not the horizontal lockup the wordmark is "
        f"(it is between {_MIN_ASPECT}:1 and {_MAX_ASPECT}:1)."
    )


# -- helpers -----------------------------------------------------------------


def _icon_frames(path: pathlib.Path) -> list[tuple[tuple[int, int], Image.Image]]:
    """The frames of an ICO, each with its size, smallest first.

    The frames are read through the icon's directory rather than off the
    opened image, because the opened image is only the largest frame.
    """
    with Image.open(path) as image:
        # `Image.open` is typed as returning the base `ImageFile`; the `.ico`
        # accessor is on `IcoImageFile`, which is what an `.ico` opens as.
        directory = image.ico  # type: ignore[attr-defined]
        # `convert` loads the frame, so the pixels are read while the file
        # is still open.
        return [
            (size, directory.getimage(size).convert("RGBA"))
            for size in sorted(image.info.get("sizes", []))
        ]


def _drawing_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """The box of the drawing in an image: everything further than the ground.

    The same measure the builder uses, written out here so that the test
    does not read its expectation out of the thing it checks.
    """
    channels = image.convert("RGB").split()
    darkest = channels[0]
    for channel in channels[1:]:
        darkest = ImageChops.darker(darkest, channel)
    distance = ImageChops.invert(darkest)
    mask = distance.point(lambda d: 255 if d > _DRAWING_TOLERANCE else 0)
    return mask.getbbox()


def _repack_with_a_different_compressor(source: pathlib.Path, target: pathlib.Path) -> None:
    """Rewrite an ``.ico`` with its frames compressed differently.

    The frames are the same pictures; only the PNG compression level differs,
    which is what a different zlib does to the same image. This is the case
    a byte comparison calls stale and a picture comparison calls current.
    """
    with Image.open(source) as image:
        # `Image.open` is typed as returning the base `ImageFile`; the `.ico`
        # accessor is on `IcoImageFile`, which is what an `.ico` opens as.
        directory = image.ico  # type: ignore[attr-defined]
        frames = []
        for size in sorted(image.info.get("sizes", [])):
            frame = directory.getimage(size).convert("RGBA")
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG", compress_level=1)
            frames.append((size, buffer.getvalue()))

    offset = 6 + 16 * len(frames)
    entries = b""
    payload = b""
    for (width, height), data in frames:
        entries += struct.pack(
            "<BBBBHHII",
            width if width < 256 else 0,
            height if height < 256 else 0,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    header = struct.pack("<HHH", 0, 1, len(frames))
    target.write_bytes(header + entries + payload)


def _run_builder(*arguments: str | pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run the artwork builder with `arguments` and capture what it said.

    S603: the arguments are module-level constants and paths inside a
    temporary directory -- nothing an attacker controls.
    """
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_BUILDER), *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
