"""Build the artwork the repository ships: the Windows icon, the web favicon
and the wordmark.

Three artefacts, one script
---------------------------
The shell ships a Windows icon, the dashboard ships a favicon, and the README
carries a wordmark. All three are derived from artwork rather than drawn by
hand, all three are committed to the repository, and all three need the same
two things: a command that derives them from the artwork, and a check that
notices when the committed file and the artwork have drifted apart. One
script keeps those two things in one place. The three derivations are the
same *kind* of derivation -- an export becomes an artefact -- and a second
script would be a second copy of the same "generate, then compare" skeleton,
and a copied skeleton is the copy that drifts.

Why this script lives at the repository root
--------------------------------------------
The artwork is repository-level art: `docs/assets/icon-source.png`. The three
artefacts it becomes live in three different trees -- the shell's
`desktop/assets/`, the dashboard's `web/src/app/`, and the README's
`docs/assets/`. A generator that sits under one of the three and produces the
other two is in the wrong place: it reads as though the artwork belonged to
the shell, and the moment the dashboard needs an asset of its own the
generator has to move again -- this time for a second reason. `scripts/` is
where the repository's other Python tools already live, and a tool that
produces artefacts for the whole repository belongs with them.

Why the icon is generated and not drawn
---------------------------------------
`electron-builder.yml` points `win.icon` at one file, and that file has to
carry every size Windows might ask for at once: the taskbar, the Start Menu,
Explorer's icon view and the Alt-Tab switcher each want a different one, and
Windows picks by what it is rendering rather than by asking. An `.ico` holding
only a 256-pixel entry still runs -- Windows scales that entry up when nothing
smaller is there -- but every small rendering of it is a blur.

So the icon is generated rather than drawn: one source of truth, resampled to
each size with LANCZOS, packed into a single `.ico`. Changing the artwork is
then one command and one file, instead of ten hand-drawn images that drift
apart from each other and from the artwork they came from.

Why these sizes
---------------
16, 20, 24, 32, 40, 48, 64, 96, 128 and 256. 16, 20 and 24 are what the
taskbar asks for at the three DPI scales Windows ships with. 32 and 48 are the
shell's medium and large icon views -- 16 is its small one. 64 and 96 are
Explorer's large and extra-large tiles. 128 and 256 are what the modern shell
asks for when it renders the icon big. 40 is not one of the shell's standard
sizes; it is here because a size Windows asks for and does not have is a
blurry render, and a size it never asks for costs a few kilobytes. A subset
would render, and would render worse.

The favicon, which is the same picture at fewer sizes
-----------------------------------------------------
The dashboard's favicon is the same badge, cut the same way and resampled the
same way. The only differences are how many sizes are packed into the file
and where the file lands.

**It is built from the artwork, not from the committed icon.** Deriving the
favicon from `desktop/assets/icon.ico` would make one committed artefact
depend on another, and a stale icon would then be reproduced faithfully as a
stale favicon -- a drift copied rather than caught. Both come from
`docs/assets/icon-source.png`.

**It is cut, not cropped.** The white ground is cut away for the favicon
exactly as it is for the icon, and for the same reason: a favicon sits in a
browser's tab strip beside the other tabs, and a white square is a bright
block in the middle of the strip. The badge on transparency is the icon; the
badge on white is a white square with a badge in it.

**It has four sizes: 16, 32, 48 and 64.** 16 is the Windows tab strip and the
bookmark bar. 32 is the macOS tab and the macOS bookmark. 48 is Windows'
medium icon view. 64 is what a high-density screen asks for. There is no
256-pixel frame, and that is a decision rather than an oversight: a 256
frame is the slot a PWA's large icon goes in, this is not a PWA, and a frame
nothing will ever ask for is committed binary that only has to be kept in
step with the artwork.

**Nothing refers to it.** Next.js's App Router picks up `app/favicon.ico` and
injects a `<link rel="icon">` for it with a content-hashed URL, so no code
names the file and there is nothing to update when it changes. That is
exactly why it needed the guard this script provides: an artefact nothing
refers to is an artefact nobody notices has drifted.

Why the white ground is cut away
--------------------------------
The artwork is a badge drawn on a white canvas, which is how it was exported.
Left alone that white becomes part of the icon, and the result is a white
square with a badge inside it rather than a badge. So the ground is removed:
pixels that are white *and reachable from the edge of the canvas* become
transparent, while white pixels that are enclosed by the artwork stay. The
connectivity is the whole distinction -- a flood fill from the corner reaches
the canvas around the badge and stops at the badge, and it would equally fail
to reach a white hole in the middle of a design.

Why the artwork is not padded
-----------------------------
The badge is a full-bleed rounded square on a dark ground, which is what a
Windows application icon is supposed to be. Windows puts its own
rounded-rectangle mask on the shell's icon slots, and a badge that already has
rounded corners and a dark ground loses nothing to that mask. Padding it would
inset the artwork inside the mask, and the result reads as a smaller icon
beside the others rather than as the same size.

The wordmark, and the two things it needs
-----------------------------------------
The wordmark is the same badge beside the name, and it is a different job
from the icon, so it gets a different treatment -- on purpose, and not by
accident of reuse.

**It keeps its white ground.** The flood fill above exists to cut the export's
canvas away so that the badge is a badge and not a white square. The wordmark
must not have that: it is a dark-blue badge and a dark word on white, and a
transparent wordmark would put its dark half on whatever is behind it, which
on a dark page is unreadable. The name is the part that has to be readable, so
the white stays, and the wordmark is cropped rather than cut.

**It is cropped rather than cut.** The export is a square with the lockup
across the middle of it -- a white canvas with a line of type on it, and most
of the canvas is not artwork. The wordmark is that band, cropped out of the
square, with a small margin of bare ground left around it so the wordmark is
not a hard edge against whatever it is placed on.

The crop is a rule rather than four numbers someone typed in: the artwork is
the region of the export that is more than a hair's breadth from white, and
the margin around it is fixed. The rule is stated in the code, so a reader can
see what the crop is and why, and a changed export produces a changed crop
rather than a mysterious one.

**It is scaled to the size the artwork was drawn at.** The export comes at
whatever resolution the drawing tool was set to, and the crop band is a
property of the drawing, not of the file it arrived in. So the export is
scaled to the design size first, and the crop is taken there. The icon does
not need this: it is resampled to ten sizes anyway, so the export's own
resolution is irrelevant to it.

Needs
-----
Pillow, which is in the repository's `dev` extra: this is a build-time tool
for a build-time asset, and nothing that ships imports it.

Why there is a `--check`
-----------------------
The artefacts are committed, and a committed artefact is exactly the kind of
file that drifts. The artwork changes, the artefact does not, and nothing says
so, because a binary is not something anyone reads in a diff. The build is a
one-time act, and one-time acts need a second, cheaper act to notice when
they have been forgotten.

So the script has a second mode. `--check` regenerates the artefacts into a
temporary directory and compares them with the ones in the tree, and reports
which one moved and how far apart the two files are.

The comparison is between the pictures, not the bytes, and the reason is
that an `.ico` holds PNG frames and a `.png` is a PNG: what a PNG encoder
writes for an image depends on the compressor, so the bytes of either
artefact depend on the zlib that packed them and on the Pillow that chose the
deflate parameters -- and both of those differ between a Linux CI runner and
the machine that committed the file. A byte comparison would call an
unchanged tree stale, and its remedy -- regenerate the artefact -- changes
the file and not the problem. The decoded pixels are the same across every
Pillow from 10.0.0 to 13.0.0 and on both platforms, so they are what the
check compares.

It deliberately does not write the artefact it is checking. A check that
repairs the tree it is checking is not a check: it would make the failure
invisible in CI, where the working tree comes back clean, and it would
destroy the only record of what drifted. `--check` reports and stops; the
developer runs the build and looks at the difference themselves.

Which artefact a path names
----------------------------
Two of the three artefacts are `.ico` files, so the check has to be able to
say which one a given path is. A path that is exactly one of the committed
targets names that one. Anything else is read by its shape: a `.png` is the
wordmark, and an `.ico` is whichever icon it *is* by the sizes it holds --
four sizes is the favicon, ten is the shell's icon.

The size set is what tells them apart because the pixels cannot. The favicon
and the icon are the same picture made the same way: the same artwork, the
same ground cut away, the same premultiplied resampling. The favicon's 16,
32, 48 and 64 frames are the icon's 16, 32, 48 and 64 frames, frame for
frame. What differs is only which sizes are packed into the file -- and that
is the artefact's contract with whatever consumes it: Windows asks the
shell's icon for ten sizes, and a browser asks the favicon for four.

The file name is deliberately not what tells them apart. The check has to
work on a path in a temporary directory, because that is how the guard is
proved not to be empty, and the name of a temporary copy is the test's choice
rather than the artefact's. The pixels cannot be used either, because they
are the same pixels.

Run
---
`python scripts/make_assets.py` rebuilds every artefact from the checked-in
artwork.

`python scripts/make_assets.py icon` rebuilds just the icon,
`python scripts/make_assets.py favicon` just the favicon, and
`python scripts/make_assets.py wordmark` just the wordmark. Each takes two
optional paths after the asset name -- the artwork to derive it from, and the
file to write -- and both default to the checked-in ones.

`python scripts/make_assets.py --check` compares every committed artefact
against the artwork rather than writing it. Given one path it compares that
artefact alone; the artwork is always the checked-in source, because the
whole point is to ask whether the artefact matches the source the tree owns.
(A check mode that let you name the source too would let you point it at a
stale artefact and call the match a pass -- which is the thing it exists to
prevent.)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
from typing import NamedTuple

from PIL import Image, ImageChops, ImageDraw, UnidentifiedImageError

#: The sizes Windows may ask for. See the module docstring for why each.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

#: The sizes a browser may ask a favicon for. See the module docstring for
#: why each, and for why there is no 256.
FAVICON_SIZES = (16, 32, 48, 64)

#: How far from white a pixel may be and still count as the white ground.
#: The ground beside the artwork is flat white with a noise of about three
#: units, and the artwork's own edge is antialiased over a pixel or two, so
#: the band between the two is narrow and 24 sits inside it.
GROUND_TOLERANCE = 24

#: Where the artwork's antialiased edge stops and the artwork itself starts.
#: A pixel that is white blended with the badge's colour is "this much blue",
#: and the distance from white is that fraction of a pixel: white is 0, a
#: saturated blue is 255. So the distance is the alpha, and the edge's alpha
#: is the distance -- but only out to the edge. Past that the artwork is
#: simply opaque, and a distance of 200 in the middle of the badge is not
#: "80% transparent blue", it is blue that happens to be dark.
EDGE_WIDTH = 64

#: The size the artwork is drawn at. The exports arrive at whatever
#: resolution the drawing tool was set to, and the wordmark's crop band is a
#: property of the drawing rather than of the file it arrived in, so the
#: export is scaled to this before the band is taken.
DESIGN_SIZE = 1024

#: How far from white a pixel may be and still count as the export's bare
#: ground, when the wordmark's band is found. Tighter than
#: `GROUND_TOLERANCE`, which answers a different question: that one asks
#: whether a pixel is part of the canvas the artwork sits on, and it has to
#: be loose because the flood fill has to cross the artwork's antialiased
#: edge. This one asks where the artwork begins, and the ground's own noise
#: is at most two units, so anything at eight or more is the drawing.
WORDMARK_GROUND_TOLERANCE = 8

#: How much bare ground to keep around the wordmark. The crop is of a
#: drawing, not of the drawing's ink: a wordmark cut flush to its own
#: outline reads as a mistake on a page, and the margin is what keeps it
#: from doing that.
WORDMARK_MARGIN = 6


def _find_repo_root() -> pathlib.Path:
    """The repository root: the nearest ancestor holding a `pyproject.toml`.

    Walking up to a marker file rather than counting directory levels, so
    that the script still works if it is copied somewhere else, and says
    something a person can act on if it cannot find one at all -- the other
    ways this script can fail all say what went wrong in words.
    """
    here = pathlib.Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    print(
        f"cannot find the repository root: no pyproject.toml above {here}. "
        "The script resolves the artwork and the artefacts relative to the "
        "repository, so it has to live inside it.",
        file=sys.stderr,
    )
    raise SystemExit(1)


#: The repository root, and the artwork and artefacts the tree owns.
REPO_ROOT = _find_repo_root()


class _Asset(NamedTuple):
    """One artefact, the artwork it is derived from, and where it lives.

    The name is what a person types on the command line, so it is the
    artefact's own name and not a path: `icon`, `favicon` and `wordmark`.

    The sizes are the frames an icon-shaped artefact is packed at, and None
    for the wordmark, which is a single picture rather than a set of them.
    They are carried here rather than looked up by name because they are
    what tells the two `.ico` artefacts apart -- see `_asset_for`.
    """

    name: str
    source: pathlib.Path
    target: pathlib.Path
    sizes: tuple[int, ...] | None


#: The assets this script owns, in the order they are built and checked.
#: The two that come from the icon's artwork are adjacent, because they are
#: the same picture at different sizes, and the wordmark is a different
#: derivation from a different export.
ASSETS: dict[str, _Asset] = {
    "icon": _Asset(
        name="icon",
        source=REPO_ROOT / "docs" / "assets" / "icon-source.png",
        target=REPO_ROOT / "desktop" / "assets" / "icon.ico",
        sizes=ICON_SIZES,
    ),
    "favicon": _Asset(
        name="favicon",
        source=REPO_ROOT / "docs" / "assets" / "icon-source.png",
        target=REPO_ROOT / "web" / "src" / "app" / "favicon.ico",
        sizes=FAVICON_SIZES,
    ),
    "wordmark": _Asset(
        name="wordmark",
        source=REPO_ROOT / "docs" / "assets" / "logo-source.png",
        target=REPO_ROOT / "docs" / "assets" / "logo.png",
        sizes=None,
    ),
}


def _distance_from_white(rgb: Image.Image) -> Image.Image:
    """How far each pixel is from white, measured by its darkest channel.

    The darkest channel rather than the luminance, because the artwork is a
    saturated blue on a white ground: by luminance a strong blue reads as
    middling, and the ground beside it would look further from white than the
    blue really is. The minimum channel puts white at 0 and a saturated colour
    at 255, which is the distinction the cut needs.
    """
    channels = rgb.split()
    darkest = channels[0]
    for channel in channels[1:]:
        darkest = ImageChops.darker(darkest, channel)
    return ImageChops.invert(darkest)


def _cut_ground(image: Image.Image) -> Image.Image:
    """Return `image` with the white ground transparent and the artwork kept."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    rgb = image.convert("RGB")
    distance = _distance_from_white(rgb)

    # Nothing to cut if the canvas already reaches the edge of the artwork --
    # an icon that is transparent all the way out. Flooding from the corner
    # would then flood the artwork instead, which is why this is checked
    # before the fill rather than after it.
    if distance.getpixel((0, 0)) > GROUND_TOLERANCE:
        return image

    # 0 for ground, 255 for artwork.
    candidate = distance.point(lambda d: 0 if d <= GROUND_TOLERANCE else 255)
    # Flood the ground from the corner. The fill value is 128 so that the
    # reached pixels can be told apart from the untouched ones afterwards.
    ImageDraw.floodfill(candidate, (0, 0), 128, thresh=0)

    # The artwork's edge is white blended with the badge's colour, so the
    # distance from white is the alpha of that blend -- and the edge's alpha
    # is the distance, out to the edge. Past the edge the artwork is opaque
    # whatever its colour.
    base = distance.point(lambda d: d if d <= EDGE_WIDTH else 255)
    # White enclosed by the artwork is part of the artwork: opaque, not a hole.
    inner = candidate.point(lambda v: 255 if v == 0 else 0)
    alpha = ImageChops.lighter(base, inner)
    # The ground the flood reached is not a blend with anything; it is the
    # canvas the badge was drawn on, and it goes away entirely.
    ground = candidate.point(lambda v: 0 if v == 128 else 255)
    alpha = ImageChops.darker(alpha, ground)
    # Whatever transparency the source already had is kept as well.
    alpha = ImageChops.darker(alpha, image.getchannel("A"))

    cut = rgb.convert("RGBA")
    cut.putalpha(alpha)
    return cut


def _unpremultiply(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """Undo a premultiplication: `rgb = premultiplied * 255 / alpha`."""
    out = rgb.copy()
    pixels = out.load()
    alphas = alpha.load()
    for y in range(alpha.height):
        for x in range(alpha.width):
            value = alphas[x, y]
            if value >= 255:
                continue
            if value == 0:
                pixels[x, y] = (0, 0, 0)
                continue
            red, green, blue = pixels[x, y]
            pixels[x, y] = (
                min(255, red * 255 // value),
                min(255, green * 255 // value),
                min(255, blue * 255 // value),
            )
    return out


def _resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale `image` to `size` without letting the transparent ground bleed in.

    A resize of an RGBA image works on each channel on its own, and the
    ground is white, so the white either side of the badge's edge is averaged
    into the edge itself: the icon grows a white rim, which on a dark taskbar
    reads as a halo around the badge. Premultiplying first -- scaling the
    colour by the alpha, so a transparent pixel has no colour to contribute --
    and undoing it afterwards is what keeps the edge the badge's own colour.
    """
    rgb = image.convert("RGB")
    alpha = image.getchannel("A")
    premultiplied = ImageChops.multiply(rgb, alpha.convert("RGB"))
    scaled_colour = premultiplied.resize(size, Image.Resampling.LANCZOS)
    scaled_alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    scaled = _unpremultiply(scaled_colour, scaled_alpha).convert("RGBA")
    scaled.putalpha(scaled_alpha)
    return scaled


def _crop_to_artwork(image: Image.Image) -> Image.Image:
    """Crop the transparent margin away, leaving the artwork square.

    Square, because an `.ico` holds square frames and a rectangle would be
    padded back to a square by the packer anyway -- with the padding in the
    wrong place, because it would be added to the long side.
    """
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return image
    left, top, right, bottom = bounds
    side = max(right - left, bottom - top)
    centre_x = (left + right) // 2
    centre_y = (top + bottom) // 2
    origin_x = centre_x - side // 2
    origin_y = centre_y - side // 2
    return image.crop((origin_x, origin_y, origin_x + side, origin_y + side))


def build_icon(
    source: pathlib.Path,
    target: pathlib.Path,
    sizes: tuple[int, ...],
) -> list[tuple[int, int]]:
    """Write `source` into `target` as a multi-size ICO, and return the sizes.

    The sizes are a parameter rather than a constant because two artefacts
    are packed this way -- the shell's icon and the dashboard's favicon --
    and they are the same picture at different sizes.
    """
    artwork = _crop_to_artwork(_cut_ground(Image.open(source)))
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = [(size, size) for size in sizes]
    # The frames are scaled here rather than left to the ICO writer, which
    # would scale them on each channel on its own and halo the badge's edge.
    # The writer takes them as `append_images`, and the first one is the
    # image being saved.
    images = [_resize(artwork, size) for size in frames]
    images[-1].save(
        target,
        format="ICO",
        sizes=frames,
        append_images=images[:-1],
    )
    return frames


def _frames(path: pathlib.Path) -> dict[tuple[int, int], bytes]:
    """The decoded pixels of every frame in an ICO, keyed by the frame's size."""
    with Image.open(path) as image:
        directory = image.ico
        sizes = sorted(image.info.get("sizes", []))
        return {size: directory.getimage(size).tobytes() for size in sizes}


def _frames_or_none(path: pathlib.Path) -> dict[tuple[int, int], bytes] | None:
    """The frames of an ICO, or None when the file is not one.

    A file that cannot be read as an icon is a fact about the file rather
    than an exception worth a traceback: the report it belongs in is the
    one that says what drifted, not the one that says the script crashed.
    """
    try:
        return _frames(path)
    except (OSError, SyntaxError, ValueError, AttributeError):
        return None


def _format_sizes(sizes: list[tuple[int, int]]) -> str:
    """Frame sizes as ``16x16, 20x20``, for a message a person can read."""
    return ", ".join(f"{width}x{height}" for width, height in sizes)


def _drift_lines(target: pathlib.Path, candidate: pathlib.Path, name: str) -> list[str]:
    """Lines naming what differs between the committed icon and the fresh one.

    The comparison says *that* the two differ; these lines say *what*
    differs. A person told only that two icons are not equal has learned
    nothing they can act on, and "the 16-pixel frame moved" is the difference
    between a five-minute fix and an afternoon of comparing files.
    """
    try:
        committed = _frames(target)
    except (OSError, SyntaxError, ValueError, AttributeError) as exc:
        return [f"the committed {name} could not be read as an ICO: {exc}"]
    try:
        regenerated = _frames(candidate)
    except (OSError, SyntaxError, ValueError, AttributeError) as exc:
        return [f"the regenerated {name} could not be read as an ICO: {exc}"]

    lines: list[str] = []
    only_committed = sorted(set(committed) - set(regenerated))
    only_regenerated = sorted(set(regenerated) - set(committed))
    if only_committed:
        lines.append(f"sizes only in the committed {name}: {_format_sizes(only_committed)}")
    if only_regenerated:
        lines.append(f"sizes only in the regenerated {name}: {_format_sizes(only_regenerated)}")
    shared = set(committed) & set(regenerated)
    differing = sorted(size for size in shared if committed[size] != regenerated[size])
    if differing:
        lines.append(f"sizes whose pixels differ: {_format_sizes(differing)}")
    if not lines:
        lines.append("the same sizes and the same pixels, but the bytes differ")
    return lines


def check_icon(
    source: pathlib.Path,
    target: pathlib.Path,
    sizes: tuple[int, ...],
    name: str,
) -> int:
    """Compare the committed icon with a freshly generated one.

    The comparison is between the *pictures*, not between the bytes, and the
    distinction is not pedantry. An `.ico` holds PNG frames, and what a PNG
    encoder writes for a given image depends on the compressor: the same
    pixels at compression levels 6, 7 and 9 come out as 80,707, 77,475 and
    75,678 bytes. So the bytes of an icon depend on the zlib that packed
    them -- the Windows wheels bundle one zlib and the manylinux wheels
    another -- and on the Pillow that chose the deflate parameters, which
    changed between Pillow 11.0.0 and 11.1.0. A byte comparison would then
    report the icon as stale on a tree where nothing had changed, and the
    failure would be "regenerate the icon" -- advice that changes the file
    and not the problem. The decoded pixels, by contrast, are the same
    across every Pillow from 10.0.0 to 13.0.0 and on both platforms. The
    pictures are what the question is about: is the icon in the tree still
    the icon the artwork makes?

    The regeneration happens in a temporary directory, and the file being
    checked is never written to: a check that repairs the tree it is checking
    is not a check. The temporary directory goes away with the `with` block,
    including on the failure path, so a failed check leaves nothing behind
    either.
    """
    if not target.exists():
        print(f"no such {name}: {target}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix=f"opspilot-{name}-") as scratch:
        candidate = pathlib.Path(scratch) / target.name
        try:
            build_icon(source, candidate, sizes)
        except UnidentifiedImageError:
            print(
                f"the artwork could not be read as an image: {source}",
                file=sys.stderr,
            )
            return 1
        committed_bytes = target.read_bytes()
        regenerated_bytes = candidate.read_bytes()

        if committed_bytes == regenerated_bytes:
            print(
                f"the committed {name} is current: {target} "
                f"({len(committed_bytes)} bytes, {len(sizes)} sizes)",
                flush=True,
            )
            return 0

        committed = _frames_or_none(target)
        regenerated = _frames_or_none(candidate)
        if committed is not None and regenerated is not None:
            same_sizes = set(committed) == set(regenerated)
            differing = [
                size
                for size in sorted(set(committed) & set(regenerated))
                if committed[size] != regenerated[size]
            ]
            if same_sizes and not differing:
                # The same pictures, packed differently. The icon has not
                # drifted, and saying otherwise would send a developer to
                # regenerate a file that is already right.
                print(
                    f"the committed {name} is current: {target} "
                    f"({len(committed_bytes)} bytes, {len(sizes)} sizes) -- "
                    "the frames are the same pictures, encoded differently",
                    flush=True,
                )
                return 0

        print(f"the committed {name} is stale: {target}", file=sys.stderr)
        print(f"  committed:   {len(committed_bytes)} bytes", file=sys.stderr)
        print(f"  regenerated: {len(regenerated_bytes)} bytes", file=sys.stderr)
        for line in _drift_lines(target, candidate, name):
            print(f"  {line}", file=sys.stderr)
        print(
            f"\nRun:  python scripts/make_assets.py {name}\n"
            f"Then commit the regenerated {name} together with the artwork change.",
            file=sys.stderr,
        )
        return 1


def _canonical_export(image: Image.Image) -> Image.Image:
    """The export at the size the artwork is drawn at.

    The export arrives at whatever resolution the drawing tool was set to,
    and the wordmark's crop band is a property of the drawing rather than of
    the file it arrived in, so the export is scaled to `DESIGN_SIZE` first and
    the band is taken there. An export that is already at the design size is
    returned untouched, because resizing an image to its own size is not
    guaranteed to be the identity, and the artefacts are compared pixel by
    pixel.
    """
    longest = max(image.size)
    if longest == DESIGN_SIZE:
        return image
    scale = DESIGN_SIZE / longest
    size = (round(image.width * scale), round(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


def _content_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """The box of the artwork in an export, or None when there is none.

    A pixel is artwork when it is far enough from white to be more than the
    export's own noise: the ground beside the drawing is flat white with a
    noise of at most two units, and the drawing's antialiased edge is well
    above that. The box is the artwork's extent, and it is the artwork's
    extent rather than the drawing's outline, which is what a crop wants.
    """
    distance = _distance_from_white(image.convert("RGB"))
    mask = distance.point(lambda d: 255 if d > WORDMARK_GROUND_TOLERANCE else 0)
    return mask.getbbox()


def _wordmark_band(image: Image.Image) -> tuple[int, int, int, int]:
    """The rectangle of the export that holds the wordmark.

    The artwork's box, plus a fixed margin of bare ground on every side. The
    margin is small on purpose: the export's whitespace is what the crop
    exists to remove, and a margin that grew would be a second, invisible
    copy of it.
    """
    box = _content_box(image)
    if box is None:
        # No artwork at all: an export of nothing but ground. The crop would
        # be the whole canvas, and saying so is more useful than a traceback.
        return (0, 0, image.width, image.height)
    left, top, right, bottom = box
    margin = WORDMARK_MARGIN
    return (left - margin, top - margin, right + margin, bottom + margin)


def build_wordmark(source: pathlib.Path, target: pathlib.Path) -> tuple[int, int]:
    """Write the wordmark cropped out of the export, and return its size.

    The white ground is kept: the wordmark is a dark badge and a dark name
    on white, and a transparent one would put its dark half on whatever is
    behind it. Nothing is cut here, only cropped.
    """
    with Image.open(source) as image:
        export = image.convert("RGB")
    export = _canonical_export(export)
    band = _wordmark_band(export)
    wordmark = export.crop(band)
    target.parent.mkdir(parents=True, exist_ok=True)
    wordmark.convert("RGBA").save(target, format="PNG", optimize=True)
    return wordmark.size


def _pixels(path: pathlib.Path) -> bytes | None:
    """The decoded pixels of an image, or None when the file is not one.

    Converted to RGBA first, so that two files are compared as the pictures
    they show rather than as the encodings they happen to use: an RGB file
    and an RGBA file with an opaque alpha channel are the same picture.
    """
    try:
        with Image.open(path) as image:
            return image.convert("RGBA").tobytes()
    except (OSError, SyntaxError, ValueError):
        return None


def _wordmark_drift_lines(target: pathlib.Path, candidate: pathlib.Path) -> list[str]:
    """Lines naming what differs between the committed wordmark and the new one."""
    try:
        with Image.open(target) as image:
            committed_size = image.size
    except (OSError, SyntaxError, ValueError):
        return ["the committed wordmark could not be read as an image"]
    try:
        with Image.open(candidate) as image:
            regenerated_size = image.size
    except (OSError, SyntaxError, ValueError):
        return ["the regenerated wordmark could not be read as an image"]

    if committed_size != regenerated_size:
        return [
            f"the committed wordmark is {committed_size[0]}x{committed_size[1]} and "
            f"the regenerated one is {regenerated_size[0]}x{regenerated_size[1]}"
        ]
    return ["the same size, but the pixels differ"]


def check_wordmark(source: pathlib.Path, target: pathlib.Path) -> int:
    """Compare the committed wordmark with a freshly generated one.

    The comparison is between the pictures, not between the bytes, for the
    same reason as the icon: a PNG is a compressed image, and what the
    compressor writes for a given picture depends on the zlib that packed it
    and on the Pillow that chose the deflate parameters, both of which
    differ between a Linux CI runner and the machine that committed the file.
    A byte comparison would report the wordmark as stale on a tree where
    nothing had changed.

    As with the icon, the regeneration happens in a temporary directory and
    the file being checked is never written to.
    """
    if not target.exists():
        print(f"no such wordmark: {target}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="opspilot-wordmark-") as scratch:
        candidate = pathlib.Path(scratch) / target.name
        try:
            build_wordmark(source, candidate)
        except UnidentifiedImageError:
            print(
                f"the artwork could not be read as an image: {source}",
                file=sys.stderr,
            )
            return 1

        committed_pixels = _pixels(target)
        regenerated_pixels = _pixels(candidate)
        if committed_pixels is None:
            print(
                f"the committed wordmark could not be read as an image: {target}",
                file=sys.stderr,
            )
            return 1
        if regenerated_pixels is None:
            print(
                f"the regenerated wordmark could not be read as an image: {candidate}",
                file=sys.stderr,
            )
            return 1

        if committed_pixels == regenerated_pixels:
            with Image.open(target) as image:
                size = image.size
            print(
                f"the committed wordmark is current: {target} "
                f"({target.stat().st_size} bytes, {size[0]}x{size[1]})",
                flush=True,
            )
            return 0

        print(f"the committed wordmark is stale: {target}", file=sys.stderr)
        print(f"  committed:   {target.stat().st_size} bytes", file=sys.stderr)
        print(f"  regenerated: {candidate.stat().st_size} bytes", file=sys.stderr)
        for line in _wordmark_drift_lines(target, candidate):
            print(f"  {line}", file=sys.stderr)
        print(
            "\nRun:  python scripts/make_assets.py wordmark\n"
            "Then commit the regenerated wordmark together with the artwork change.",
            file=sys.stderr,
        )
        return 1


def _ico_sizes(path: pathlib.Path) -> frozenset[tuple[int, int]] | None:
    """The frame sizes an ICO holds, or None when it cannot be read as one.

    The sizes are in the directory at the head of the file, so they are
    known without decoding a frame -- which is what makes a file whose
    pixels are damaged still recognisable as an icon.
    """
    try:
        with Image.open(path) as image:
            sizes = image.info.get("sizes")
    except (OSError, SyntaxError, ValueError):
        return None
    if not sizes:
        return None
    return frozenset((int(width), int(height)) for width, height in sizes)


def _icon_asset_for(target: pathlib.Path) -> _Asset | None:
    """The icon-shaped asset an `.ico` path holds, recognised by its sizes.

    Two of the artefacts are icons -- the shell's and the dashboard's
    favicon -- and they are the same picture at different sizes, so the
    pixels cannot say which one a file is. The sizes can: four frames is
    the favicon and ten is the shell's icon, and the sizes are the
    artefact's contract with whatever consumes it.

    A file that is neither is refused rather than guessed at, because a
    check pointed at the wrong asset is a check that proves nothing.
    """
    sizes = _ico_sizes(target)
    if sizes is None:
        return None
    for asset in ASSETS.values():
        if asset.sizes is None:
            continue
        if sizes == frozenset((size, size) for size in asset.sizes):
            return asset
    return None


def _asset_for(target: pathlib.Path) -> _Asset | None:
    """The asset a `--check` path names, or None when it names none.

    A path that is exactly one of the committed artefacts names that asset.
    Anything else is read by its shape: a `.png` is the wordmark, and an
    `.ico` is whichever of the two icons it holds by the sizes it packs.
    A path that is neither is refused rather than guessed at, because a
    check pointed at the wrong asset is a check that proves nothing.
    """
    for asset in ASSETS.values():
        if target == asset.target or target.resolve() == asset.target.resolve():
            return asset
    suffix = target.suffix.lower()
    if suffix == ".ico":
        return _icon_asset_for(target)
    if suffix == ".png":
        return ASSETS["wordmark"]
    return None


def _check_asset(asset: _Asset, source: pathlib.Path, target: pathlib.Path) -> int:
    """Check one asset, by name."""
    if asset.sizes is not None:
        return check_icon(source, target, asset.sizes, asset.name)
    return check_wordmark(source, target)


def _build_asset(asset: _Asset, source: pathlib.Path, target: pathlib.Path) -> int:
    """Build one asset, and report what was written."""
    if asset.sizes is not None:
        sizes = build_icon(source, target, asset.sizes)
        print(f"wrote {target} ({target.stat().st_size} bytes) with {len(sizes)} sizes")

        # Read it back, because a write that reports success and produces a
        # file nobody can open is worse than one that fails.
        with Image.open(target) as written:
            found = sorted(written.info.get("sizes", []))
        if found != sorted(sizes):
            print(f"the icon does not contain the sizes it should: {found}", file=sys.stderr)
            return 1
        print(f"verified: {target} opens and contains {len(found)} sizes")
        return 0

    size = build_wordmark(source, target)
    print(f"wrote {target} ({target.stat().st_size} bytes) at {size[0]}x{size[1]}")

    with Image.open(target) as written:
        found = written.size
    if found != size:
        print(f"the wordmark is not the size it was written at: {found}", file=sys.stderr)
        return 1
    print(f"verified: {target} opens and is {size[0]}x{size[1]}")
    return 0


def _check_all(target: pathlib.Path | None) -> int:
    """Check every asset, or the one `target` names."""
    if target is None:
        status = 0
        for asset in ASSETS.values():
            if not asset.source.exists():
                print(f"no such source: {asset.source}", file=sys.stderr)
                status = 1
                continue
            status = max(status, _check_asset(asset, asset.source, asset.target))
        return status

    asset = _asset_for(target)
    if asset is None:
        print(
            f"--check cannot tell which asset {target} names: an .ico is read "
            "by the sizes it packs -- the favicon's four (16, 32, 48, 64) or "
            "the shell icon's ten -- and a .png is the wordmark.",
            file=sys.stderr,
        )
        return 1
    if not asset.source.exists():
        print(f"no such source: {asset.source}", file=sys.stderr)
        return 1
    return _check_asset(asset, asset.source, target)


def _build_all() -> int:
    """Build every asset from the checked-in artwork."""
    status = 0
    for asset in ASSETS.values():
        if not asset.source.exists():
            print(f"no such source: {asset.source}", file=sys.stderr)
            status = 1
            continue
        status = max(status, _build_asset(asset, asset.source, asset.target))
    return status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="make_assets.py",
        description="Build the Windows icon, the web favicon and the wordmark from the artwork.",
        epilog=(
            "without --check: [ASSET [SOURCE [TARGET]]] -- the asset to build "
            "('icon', 'favicon' or 'wordmark'), the artwork to derive it from "
            "and the file to write. with --check: [PATH] -- the artefact to "
            "compare against one regenerated from the checked-in artwork."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        metavar="PATH",
        help=(
            "the asset to build and the paths to build it from, or under "
            "--check the artefact to compare; they all default to the "
            "checked-in ones"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "compare an existing artefact against one regenerated from the "
            "artwork, and write nothing"
        ),
    )
    args = parser.parse_args(argv[1:])

    paths: list[pathlib.Path] = list(args.paths)
    check: bool = args.check

    if check:
        # One path, and it is the artefact. Naming the artwork as well would
        # let the check be pointed at an artefact built from something else,
        # and a match against the wrong source is not a check of the tree.
        if len(paths) > 1:
            parser.error(
                "--check takes one path -- the artefact to compare. The "
                "artwork is the checked-in one; run the script without "
                "--check to build an asset from different artwork."
            )
        return _check_all(paths[0] if paths else None)

    if not paths:
        return _build_all()

    if len(paths) > 3:
        parser.error("at most three paths: the asset, the artwork, and the file to write")
    name = paths[0].name
    if name not in ASSETS:
        parser.error(f"unknown asset {name!r}: choose one of {', '.join(ASSETS)}")
    asset = ASSETS[name]
    source = paths[1] if len(paths) > 1 else asset.source
    target = paths[2] if len(paths) > 2 else asset.target
    if not source.exists():
        print(f"no such source: {source}", file=sys.stderr)
        return 1
    return _build_asset(asset, source, target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
