"""The committed Windows icon must still be what the artwork generates.

`desktop/assets/icon.ico` is a build artefact that lives in the repository:
`desktop/scripts/make-icon.py` generates it from `docs/assets/icon-source.png`,
and `desktop/electron-builder.yml` points the Windows build at the committed
file. Nothing in the build regenerates it, so the two drift the moment the
artwork changes and nobody reruns the script -- and the drift is invisible in
a review, because a binary is not something a diff shows. The icon that ships
is then the badge from three artwork revisions ago, and the only symptom is
that nobody can say why the icon looks wrong.

This is the same species of failure as the dashboard's generated client types
(`tests/integration/test_web_client_types.py`): a copy that is meant to be
derived from a source, and is not. The precedent is followed here in the way
that matters -- the check calls the builder's own `--check` mode rather than
reimplementing the comparison here. A guard that regenerated the icon itself
and compared it against its own logic would be testing that guard's logic,
not that the committed artefact is current.

Three things are checked:

1. **The committed icon is what the artwork generates.** `make-icon.py --check`
   regenerates the icon into a temporary directory and compares it with the
   committed file -- picture against picture, not byte against byte -- and
   never writes to that file.
2. **The guard is not empty.** The check is run against an icon built in a
   temporary directory from different artwork, and it has to fail. A check
   that cannot fail is not a check, and the real icon is never touched to
   prove that it can.
3. **The committed icon carries every size Windows may ask for.** Those ten
   sizes are the builder's own invariant, and until now only the builder
   asserted them: an icon that was hand-edited and lost a size would have
   gone unnoticed, because nothing outside the script ever looked at what the
   committed file contains.

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

from PIL import Image

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BUILDER = _REPO_ROOT / "desktop" / "scripts" / "make-icon.py"
_ARTWORK = _REPO_ROOT / "docs" / "assets" / "icon-source.png"
_ICON = _REPO_ROOT / "desktop" / "assets" / "icon.ico"

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


def test_the_committed_icon_is_current_with_the_artwork() -> None:
    """The committed icon is exactly what the artwork generates.

    Runs the builder's own ``--check`` mode rather than reimplementing the
    comparison here, for the reason the file's docstring gives: a guard that
    regenerated the icon itself and compared it against its own logic would be
    testing that guard's logic, not that the committed artefact is current.
    """
    before = _ICON.read_bytes()

    result = _run_builder("--check")

    assert result.returncode == 0, (
        "desktop/assets/icon.ico has drifted from docs/assets/icon-source.png.\n"
        "Run `python desktop/scripts/make-icon.py` and commit the result.\n\n"
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
    built = _run_builder(other_artwork, stale)
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
    """`--check` names the icon, and refuses a second path.

    The check compares an icon against one regenerated from the artwork the
    tree owns. Were it allowed to name the artwork as well, it could be
    pointed at an icon built from something else and a match against the
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


# -- helpers -----------------------------------------------------------------


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
    """Run the icon builder with `arguments` and capture what it said.

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
