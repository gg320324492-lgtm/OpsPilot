"""The committed artwork must still be what the artwork generates.

`desktop/assets/icon.ico` and `docs/assets/logo.png` are build artefacts that
live in the repository: `desktop/scripts/make-assets.py` generates the icon
from `docs/assets/icon-source.png` and the wordmark from
`docs/assets/logo-source.png`, and `desktop/electron-builder.yml` points the
Windows build at the committed `.ico` while the root README carries the
committed `.png`. Nothing in the build regenerates them, so the two drift the
moment the artwork changes and nobody reruns the script -- and the drift is
invisible in a review, because a binary is not something a diff shows. The
icon that ships is then the badge from three artwork revisions ago, and the
only symptom is that nobody can say why the icon looks wrong.

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

1. **The committed icon is what the artwork generates.** `make-assets.py
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
_BUILDER = _REPO_ROOT / "desktop" / "scripts" / "make-assets.py"
_ARTWORK = _REPO_ROOT / "docs" / "assets" / "icon-source.png"
_ICON = _REPO_ROOT / "desktop" / "assets" / "icon.ico"
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
        "Run `python desktop/scripts/make-assets.py icon` and commit the result.\n\n"
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


def test_the_committed_wordmark_is_current_with_the_artwork() -> None:
    """The committed wordmark is exactly what the artwork generates.

    The same contract as the icon, and for the same reason: the check is the
    builder's own, and the file being checked is not written to.
    """
    before = _WORDMARK.read_bytes()

    result = _run_builder("--check", _WORDMARK)

    assert result.returncode == 0, (
        "docs/assets/logo.png has drifted from docs/assets/logo-source.png.\n"
        "Run `python desktop/scripts/make-assets.py wordmark` and commit the "
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
