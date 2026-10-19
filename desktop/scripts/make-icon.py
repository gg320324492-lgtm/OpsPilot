"""Build the Windows icon the shell and the installer ship, from the artwork.

Why this is a script and not a hand-made `.ico`
------------------------------------------------
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
taskbar asks for at the three DPI scales Windows ships with; 32 and 48 are the
shell's small and medium views; 64 and 96 are Explorer's large and extra-large
tiles; 128 and 256 are what the modern shell asks for when it renders the icon
big. A subset would render, and would render worse.

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

Needs
-----
Pillow, which is in the repository's `dev` extra: this is a build-time tool
for a build-time asset, and nothing that ships imports it.

Why there is a `--check`
-----------------------
The icon is a committed artefact, and a committed artefact is exactly the kind
of file that drifts. The artwork changes, the icon does not, and nothing says
so, because a binary is not something anyone reads in a diff. The build is a
one-time act, and one-time acts need a second, cheaper act to notice when
they have been forgotten.

So the script has a second mode. `--check` regenerates the icon into a
temporary directory and compares it, byte for byte, with the one in the tree,
and reports which sizes moved and how far apart the two files are.

It deliberately does not write the icon it is checking. A check that repairs
the tree it is checking is not a check: it would make the failure invisible
in CI, where the working tree comes back clean, and it would destroy the only
record of what drifted. `--check` reports and stops; the developer runs the
build and looks at the difference themselves.

Run
---
`python desktop/scripts/make-icon.py [source.png] [target.ico]`

Both arguments are optional. The defaults are the checked-in artwork and the
committed icon that the packaging configuration points at.

`python desktop/scripts/make-icon.py --check` compares the committed icon
against the artwork rather than writing it. The one optional argument is the
icon to check, defaulting to the committed one; the artwork is the checked-in
source, because the whole point is to ask whether the artefact matches the
source the tree owns. (A check mode that let you name the source too would let
you point it at a stale icon and call the match a pass -- which is the thing
it exists to prevent.)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, UnidentifiedImageError

#: The sizes Windows may ask for. See the module docstring for why each.
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

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

#: The checked-in artwork, and the icon the packaging configuration points at.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "docs" / "assets" / "icon-source.png"
DEFAULT_TARGET = REPO_ROOT / "desktop" / "assets" / "icon.ico"


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


def build_ico(source: pathlib.Path, target: pathlib.Path) -> list[tuple[int, int]]:
    """Write `source` into `target` as a multi-size ICO, and return the sizes."""
    artwork = _crop_to_artwork(_cut_ground(Image.open(source)))
    target.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(size, size) for size in SIZES]
    # The frames are scaled here rather than left to the ICO writer, which
    # would scale them on each channel on its own and halo the badge's edge.
    # The writer takes them as `append_images`, and the first one is the
    # image being saved.
    frames = [_resize(artwork, size) for size in sizes]
    frames[-1].save(
        target,
        format="ICO",
        sizes=sizes,
        append_images=frames[:-1],
    )
    return sizes


def _frames(path: pathlib.Path) -> dict[tuple[int, int], bytes]:
    """The decoded pixels of every frame in an ICO, keyed by the frame's size."""
    with Image.open(path) as image:
        directory = image.ico
        sizes = sorted(image.info.get("sizes", []))
        return {size: directory.getimage(size).tobytes() for size in sizes}


def _format_sizes(sizes: list[tuple[int, int]]) -> str:
    """Frame sizes as ``16x16, 20x20``, for a message a person can read."""
    return ", ".join(f"{width}x{height}" for width, height in sizes)


def _drift_lines(target: pathlib.Path, candidate: pathlib.Path) -> list[str]:
    """Lines naming what differs between the committed icon and the fresh one.

    The byte comparison says *that* the two differ; these lines say *what*
    differs. A person told only that two binaries are not equal has learned
    nothing they can act on, and "the 16-pixel frame moved" is the difference
    between a five-minute fix and an afternoon of comparing files.
    """
    try:
        committed = _frames(target)
    except (OSError, SyntaxError, ValueError, AttributeError) as exc:
        return [f"the committed icon could not be read as an ICO: {exc}"]
    try:
        regenerated = _frames(candidate)
    except (OSError, SyntaxError, ValueError, AttributeError) as exc:
        return [f"the regenerated icon could not be read as an ICO: {exc}"]

    lines: list[str] = []
    only_committed = sorted(set(committed) - set(regenerated))
    only_regenerated = sorted(set(regenerated) - set(committed))
    if only_committed:
        lines.append(f"sizes only in the committed icon: {_format_sizes(only_committed)}")
    if only_regenerated:
        lines.append(f"sizes only in the regenerated icon: {_format_sizes(only_regenerated)}")
    shared = set(committed) & set(regenerated)
    differing = sorted(size for size in shared if committed[size] != regenerated[size])
    if differing:
        lines.append(f"sizes whose pixels differ: {_format_sizes(differing)}")
    if not lines:
        lines.append("the same sizes and the same pixels, but the bytes differ")
    return lines


def check_ico(source: pathlib.Path, target: pathlib.Path) -> int:
    """Compare the committed icon with a freshly generated one.

    The regeneration happens in a temporary directory, and the file being
    checked is never written to: a check that repairs the tree it is checking
    is not a check. The temporary directory goes away with the `with` block,
    including on the failure path, so a failed check leaves nothing behind
    either.
    """
    if not target.exists():
        print(f"no such icon: {target}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="opspilot-icon-") as scratch:
        candidate = pathlib.Path(scratch) / target.name
        try:
            build_ico(source, candidate)
        except UnidentifiedImageError:
            print(
                f"the artwork could not be read as an image: {source}",
                file=sys.stderr,
            )
            return 1
        committed = target.read_bytes()
        regenerated = candidate.read_bytes()

        if committed == regenerated:
            print(
                f"the committed icon is current: {target} "
                f"({len(committed)} bytes, {len(SIZES)} sizes)"
            )
            return 0

        print(f"the committed icon is stale: {target}", file=sys.stderr)
        print(f"  committed:   {len(committed)} bytes", file=sys.stderr)
        print(f"  regenerated: {len(regenerated)} bytes", file=sys.stderr)
        for line in _drift_lines(target, candidate):
            print(f"  {line}", file=sys.stderr)
        print(
            "\nRun:  python desktop/scripts/make-icon.py\n"
            "Then commit the regenerated icon together with the artwork change.",
            file=sys.stderr,
        )
        return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="make-icon.py",
        description="Build the Windows icon the shell and the installer ship.",
        epilog=(
            "without --check: [SOURCE [TARGET]] -- the artwork to resample and "
            "the .ico to write. with --check: [TARGET] -- the .ico to compare "
            "against one regenerated from the checked-in artwork."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        metavar="PATH",
        help=(
            "the artwork and the .ico to write, or under --check the .ico to "
            "compare; both default to the checked-in ones"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare an existing .ico against one regenerated from the artwork, and write nothing",
    )
    args = parser.parse_args(argv[1:])

    paths: list[pathlib.Path] = list(args.paths)
    check: bool = args.check

    if check:
        # One path, and it is the icon. Naming the artwork as well would let
        # the check be pointed at an icon built from something else, and a
        # match against the wrong source is not a check of the tree.
        if len(paths) > 1:
            parser.error(
                "--check takes one path -- the .ico to compare. The artwork is "
                "the checked-in one; run the script without --check to build "
                "an icon from different artwork."
            )
        source = DEFAULT_SOURCE
        target = paths[0] if paths else DEFAULT_TARGET
    else:
        if len(paths) > 2:
            parser.error("at most two paths: the artwork, and the .ico to write")
        source = paths[0] if paths else DEFAULT_SOURCE
        target = paths[1] if len(paths) > 1 else DEFAULT_TARGET

    if not source.exists():
        print(f"no such source: {source}", file=sys.stderr)
        return 1

    if check:
        return check_ico(source, target)

    sizes = build_ico(source, target)
    print(f"wrote {target} ({target.stat().st_size} bytes) with {len(sizes)} sizes")

    # Read it back, because a write that reports success and produces a file
    # nobody can open is worse than one that fails.
    with Image.open(target) as written:
        found = sorted(written.info.get("sizes", []))
    if found != sorted(sizes):
        print(f"the icon does not contain the sizes it should: {found}", file=sys.stderr)
        return 1
    print(f"verified: {target} opens and contains {len(found)} sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
