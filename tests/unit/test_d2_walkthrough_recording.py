"""The D2 walk-through recording, and the two sides of the promise about it.

## Why this file exists

§P2-D2 is the one acceptance criterion this project still owes, and the walk
that closes it will be recorded: a stranger, on a clean Windows machine,
installing the packaged installer and taking the golden path through the
operator's screens. That recording is going to become a binary committed to
this repository, beside `docs/assets/golden-path.gif` and the ten screenshots,
with the README embedding it.

Every other binary committed here has a guard. `scripts/make_assets.py
--check` compares the committed icon, favicon, apple-touch icon and wordmark
against the artwork that generates them;
`tests/unit/test_desktop_icon_is_current.py` says what that check must catch
and proves it is not empty; `desktop/e2e/packaged-posture-icon.spec.ts` checks
the icon inside the package. A recording joins that set the moment it is
committed, and a binary is the one kind of change a diff does not show: an
oversized file, a file under the wrong name, a WebM wearing an `.mp4` name --
none of them is visible in a review, and all of them are exactly the drift a
guard exists to catch.

## What the guard holds together

Two halves of one promise, asserted in both directions, because either half
alone is worthless.

* **A reference without a file.** The README pointing at a recording the tree
  does not have is the defect this repository treats as a defect wherever it
  appears: *a document reading as evidence of something the tree does not
  say.* The README's own rule about the GitHub Release is the same rule -- "this
  README will not link to a download that does not exist" -- and a video is a
  download.
* **A file without a reference.** A recording committed and never pointed at is
  worse than neither: the repository would hold a binary nobody can find, and
  no document would claim it. That is how a ten-megabyte file ends up in
  history with nothing explaining it.

The tree as it stands today is the "neither" state: the README describes the
recording that is going to be made, and the tree does not have it.

## What "references" means here, and what it does not

The README is allowed to *name* the recording before it exists. The
convention has to be written down somewhere, or the person who runs the
walk-through does not know what to produce. So this file draws the line where
the repository draws it everywhere else -- `desktop/e2e/error-text.spec.ts`
checks the *quoted* strings against the ones the application shows and leaves
prose alone: a path in prose, in backticks, or in a fenced block *names* a
file, and a path the README *renders* -- an HTML `src`/`href`, or a Markdown
link or image target -- *references* it.

That distinction is what lets the README say "it will be committed as
`docs/assets/d2-walkthrough.mp4`" today and still be green, while a README
that embedded a `<video>` pointing at a file the tree does not have is not.

## The expectations

Every value below is written out here rather than imported from the tree. A
test that reads its expectation from the thing it checks passes whatever that
thing happens to say today, and proves nothing.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

#: The stem the recording's file names share. Hand-written, for the reason the
#: file's docstring gives.
RECORDING_STEM = "d2-walkthrough"

#: The recording itself: the MP4 the README embeds.
PRIMARY_NAME = "d2-walkthrough.mp4"

#: The optional companion: the WebM the MP4 was transcoded from, kept beside
#: it as a second `<source>` for a browser that prefers it.
SOURCE_NAME = "d2-walkthrough.webm"

#: Where the two live, relative to the repository root -- the same directory
#: as the GIF and the ten screenshots.
PRIMARY_PATH = f"docs/assets/{PRIMARY_NAME}"
SOURCE_PATH = f"docs/assets/{SOURCE_NAME}"

#: The only two paths a recording may occupy. A recording under any other
#: name is not the recording, and two files cannot both be the D2
#: walk-through.
AGREED_RECORDING_PATHS = frozenset({PRIMARY_PATH, SOURCE_PATH})

#: The ceiling on a committed recording, in bytes. The file lives in this
#: repository's history, so every clone pays for it, and an install-and-walk
#: recording fits in ten mebibytes at a modest bitrate.
MAX_RECORDING_BYTES = 10 * 1024 * 1024

#: A rendered reference: an HTML attribute, or a Markdown link or image
#: target. A path in prose matches neither.
_HTML_ATTRIBUTE = re.compile(
    r"""(?:src|href|data-src|poster)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_MARKDOWN_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)")

#: Fenced blocks and inline code spans are not rendered, so a path inside one
#: is a name, not a reference. The closing run of backticks has to be the
#: same length as the opening one, so an unbalanced backtick in the document
#: cannot swallow the text after it.
_FENCED_BLOCK = re.compile(r"^(?:```|~~~).*?^(?:```|~~~)", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"(?P<fence>`+)(?P<body>[^`\n]*?)(?P=fence)")


def _rendered_text(markdown: str) -> str:
    """The parts of a Markdown document a reader sees as links, not as code."""
    return _INLINE_CODE.sub("", _FENCED_BLOCK.sub("", markdown))


def _normalise(path: str) -> str:
    """A reference path as the tree spells it."""
    cleaned = path.strip().split("#", 1)[0].split("?", 1)[0]
    cleaned = cleaned.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def _is_a_recording_path(path: str) -> bool:
    """Whether a path names the D2 walk-through recording.

    Judged on the file name's stem rather than on the whole path, so a
    recording committed anywhere else -- `web/videos/d2-walkthrough.mp4` -- is
    caught as well as one committed here.
    """
    name = _normalise(path).rsplit("/", 1)[-1]
    return name.startswith(RECORDING_STEM)


def _referenced_paths(markdown: str) -> list[str]:
    """The paths the document *renders* a reference to."""
    rendered = _rendered_text(markdown)
    found = [match.group(1) for match in _HTML_ATTRIBUTE.finditer(rendered)]
    found.extend(match.group(1) for match in _MARKDOWN_TARGET.finditer(rendered))
    return found


def _readme_text(root: pathlib.Path) -> str:
    return (root / "README.md").read_text(encoding="utf-8")


def _reference_problems(root: pathlib.Path) -> list[str]:
    """Problems with what the README points at.

    A rendered reference to a recording the tree does not have is a document
    claiming something the tree does not say. A rendered reference to a path
    that is not one of the agreed ones is a reference to something that is not
    the D2 walk-through recording.
    """
    referenced = {
        _normalise(path)
        for path in _referenced_paths(_readme_text(root))
        if _is_a_recording_path(path)
    }
    problems: list[str] = []
    for path in sorted(referenced):
        if path not in AGREED_RECORDING_PATHS:
            problems.append(
                f"the README references {path}, which is not one of the agreed recording "
                f"paths ({', '.join(sorted(AGREED_RECORDING_PATHS))})"
            )
            continue
        if not (root / path).is_file():
            problems.append(f"the README references {path}, but the tree has no such file")
    return problems


def _orphan_problems(root: pathlib.Path) -> list[str]:
    """Problems with what the tree has and the README does not point at."""
    referenced = {
        _normalise(path)
        for path in _referenced_paths(_readme_text(root))
        if _is_a_recording_path(path)
    }
    problems: list[str] = []
    for path in sorted(AGREED_RECORDING_PATHS):
        if path in referenced:
            continue
        if (root / path).is_file():
            problems.append(f"{path} is committed, but the README does not reference it")
    return problems


def _naming_problems(root: pathlib.Path) -> list[str]:
    """Problems with the name, and the presence, of a committed recording."""
    problems: list[str] = []
    assets = root / "docs" / "assets"
    if assets.is_dir():
        for entry in sorted(assets.iterdir()):
            if not entry.is_file() or not entry.name.startswith(RECORDING_STEM):
                continue
            relative = f"docs/assets/{entry.name}"
            if relative in AGREED_RECORDING_PATHS:
                continue
            problems.append(
                f"{relative} is a recording under a name that was not agreed on "
                f"({', '.join(sorted(AGREED_RECORDING_PATHS))})"
            )
    if (root / SOURCE_PATH).is_file() and not (root / PRIMARY_PATH).is_file():
        problems.append(f"{SOURCE_PATH} is committed without {PRIMARY_PATH}")
    return problems


def _is_an_mp4(data: bytes) -> bool:
    """Whether the bytes are an MP4 container.

    The first box of an ISO base media file is `ftyp`, which carries the
    file's major brand. Checked on the bytes rather than on the file's name:
    the point is that a file called `.mp4` is an MP4.
    """
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _is_a_webm(data: bytes) -> bool:
    """Whether the bytes are a WebM container.

    A WebM file starts with the EBML magic and names its doctype `webm`
    inside the header.
    """
    return len(data) >= 8 and data[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in data[:256]


def _format_problems(root: pathlib.Path) -> list[str]:
    """Problems with the container a committed recording is in.

    Checked on the bytes rather than on the file's name, because a renamed
    file is exactly the drift a guard is for: the README promises MP4 and the
    tree would be carrying a WebM.
    """
    problems: list[str] = []
    for path in sorted(AGREED_RECORDING_PATHS):
        file = root / path
        if not file.is_file():
            continue
        data = file.read_bytes()
        if path.endswith(".mp4"):
            if not _is_an_mp4(data):
                problems.append(f"{path} is not an MP4 container")
            elif b"avc1" not in data:
                problems.append(f"{path} carries no H.264 video track")
        elif not _is_a_webm(data):
            problems.append(f"{path} is not a WebM container")
    return problems


def _size_problems(root: pathlib.Path) -> list[str]:
    """Problems with how big a committed recording is."""
    problems: list[str] = []
    for path in sorted(AGREED_RECORDING_PATHS):
        file = root / path
        if not file.is_file():
            continue
        size = file.stat().st_size
        if size > MAX_RECORDING_BYTES:
            problems.append(f"{path} is {size} bytes, over the {MAX_RECORDING_BYTES}-byte ceiling")
    return problems


def _fake_tree(root: pathlib.Path, readme: str = "") -> pathlib.Path:
    """A tree with a README and an assets directory, and nothing else."""
    (root / "docs" / "assets").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(readme, encoding="utf-8")
    return root


def _write_fake_mp4(path: pathlib.Path, size: int = 64) -> None:
    """A file that is an MP4 container with an H.264 sample entry."""
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomavc1"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"\x00" * max(0, size - len(header)))


def _write_fake_webm(path: pathlib.Path, size: int = 64) -> None:
    """A file that is a WebM container."""
    header = b"\x1a\x45\xdf\xa3\x42\x82\x84webm\x42\x83\x85\x01"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"\x00" * max(0, size - len(header)))


def _readme_with_a_recording_reference() -> str:
    """A README that renders the recording the way the real one will."""
    return (
        "# A readme\n\n"
        "The walk-through's recording will be committed as "
        "`docs/assets/d2-walkthrough.mp4`.\n\n"
        '<video controls width="768">\n'
        '  <source src="docs/assets/d2-walkthrough.mp4" type="video/mp4">\n'
        "</video>\n"
    )


def test_the_readme_does_not_reference_a_recording_the_tree_does_not_have() -> None:
    """The README may not point at a recording that is not in the tree.

    This is the rule the README sets for itself about the GitHub Release --
    "this README will not link to a download that does not exist" -- applied
    to the video.
    """
    problems = _reference_problems(REPO_ROOT)

    assert problems == [], (
        "the README points at a recording the tree does not have:\n\n" + "\n".join(problems)
    )


def test_a_committed_recording_is_referenced_by_the_readme() -> None:
    """A recording in the tree, with nothing pointing at it, is a failure.

    The other half of the same promise. A binary nobody can find is how a
    ten-megabyte file ends up in this repository's history with nothing
    explaining it.
    """
    problems = _orphan_problems(REPO_ROOT)

    assert problems == [], "the tree has a recording the README does not point at:\n\n" + "\n".join(
        problems
    )


def test_a_committed_recording_is_the_agreed_name() -> None:
    """A recording is called the agreed name, and lives in the agreed place."""
    problems = _naming_problems(REPO_ROOT)

    assert problems == [], (
        "a recording is committed under a name that was not agreed on:\n\n" + "\n".join(problems)
    )


def test_a_committed_recording_is_the_agreed_format() -> None:
    """A recording is in the container its name claims, with H.264 in the MP4.

    Checked on the bytes rather than on the extension: a WebM renamed to
    `.mp4` is exactly the drift a guard is for.
    """
    problems = _format_problems(REPO_ROOT)

    assert problems == [], (
        "a committed recording is not in the format the convention agreed on:\n\n"
        + "\n".join(problems)
    )


def test_a_committed_recording_is_within_the_size_ceiling() -> None:
    """A committed recording is small enough to live in this repository."""
    problems = _size_problems(REPO_ROOT)

    assert problems == [], "a committed recording is over the size ceiling:\n\n" + "\n".join(
        problems
    )


def test_the_readme_names_the_recording_the_walkthrough_will_produce() -> None:
    """The convention is written down where the person running the walk will read it.

    The recording does not exist yet, and the README has to say what will be
    committed and what it will be called -- otherwise the convention exists
    only in this file, and the person who has to satisfy it has never heard
    of it.
    """
    text = README.read_text(encoding="utf-8")

    assert PRIMARY_NAME in text, (
        f"the README does not name {PRIMARY_NAME}, so it does not say what the "
        "D2 walk-through's recording will be called"
    )
    assert SOURCE_NAME in text, (
        f"the README does not name {SOURCE_NAME}, so it does not say what the "
        "recording's source file will be called"
    )


def test_a_name_in_prose_is_not_a_reference(tmp_path: pathlib.Path) -> None:
    """Naming the recording in prose is not pointing at it.

    The README has to be able to say what the recording will be called before
    the recording exists -- that is the convention, and the person who runs
    the walk-through reads it. Only a *rendered* reference counts, or the
    README could not describe a file that is not in the tree yet.
    """
    root = _fake_tree(
        tmp_path,
        readme="# A readme\n\nIt will be committed as `docs/assets/d2-walkthrough.mp4`.\n",
    )

    assert _reference_problems(root) == [], (
        "a path named in prose was treated as a reference to a file, so the "
        "README cannot describe the recording before it exists"
    )
    assert _orphan_problems(root) == [], (
        "a path named in prose was treated as a reference to a file, so the "
        "README cannot describe the recording before it exists"
    )


def test_the_guard_is_not_empty_for_a_recording_the_readme_does_not_reference(
    tmp_path: pathlib.Path,
) -> None:
    """A committed recording nobody can find is reported, not waved through.

    The recording is written into a temporary directory, so the real
    `docs/assets/` is never touched. A guard that proved itself by committing
    a stray binary to this repository would be trading a false green for a
    false red.
    """
    root = _fake_tree(tmp_path)
    _write_fake_mp4(root / PRIMARY_PATH)

    problems = _orphan_problems(root)

    assert problems, (
        "a recording committed into the tree, which the README does not "
        "reference, was not reported -- so the guard is empty"
    )
    assert PRIMARY_PATH in problems[0], (
        f"the orphan was reported without naming the file: {problems[0]!r}"
    )


def test_the_guard_is_not_empty_for_a_readme_pointing_at_a_recording_that_is_not_there(
    tmp_path: pathlib.Path,
) -> None:
    """A README that points at a recording the tree lacks is reported.

    This is the defect the repository treats as a defect everywhere: a
    document reading as evidence of something the tree does not say.
    """
    root = _fake_tree(tmp_path, readme=_readme_with_a_recording_reference())

    problems = _reference_problems(root)

    assert problems, (
        "the README referenced a recording the tree does not have, and it was "
        "not reported -- so the guard is empty"
    )
    assert PRIMARY_PATH in problems[0], (
        f"the dangling reference was reported without naming the file: {problems[0]!r}"
    )


def test_the_guard_is_not_empty_for_a_recording_under_an_unagreed_name(
    tmp_path: pathlib.Path,
) -> None:
    """A recording committed under a name that was not agreed on is reported."""
    root = _fake_tree(tmp_path, readme=_readme_with_a_recording_reference())
    _write_fake_mp4(root / "docs" / "assets" / "d2-walkthrough-final.mp4")

    problems = _naming_problems(root)

    assert problems, (
        "a recording under a name that was not agreed on was not reported -- so the guard is empty"
    )
    assert "d2-walkthrough-final.mp4" in problems[0], (
        f"the unagreed name was reported without naming the file: {problems[0]!r}"
    )


def test_the_guard_is_not_empty_for_a_recording_that_is_not_the_format_it_claims(
    tmp_path: pathlib.Path,
) -> None:
    """A WebM wearing an `.mp4` name is reported.

    The name and the bytes are checked separately on purpose: a file called
    `.mp4` that is not one is the drift a diff cannot show.
    """
    root = _fake_tree(tmp_path, readme=_readme_with_a_recording_reference())
    _write_fake_webm(root / PRIMARY_PATH)

    problems = _format_problems(root)

    assert problems, "a WebM carrying an .mp4 name was not reported -- so the guard is empty"
    assert PRIMARY_PATH in problems[0], (
        f"the wrong format was reported without naming the file: {problems[0]!r}"
    )


def test_the_guard_is_not_empty_for_a_recording_over_the_size_ceiling(
    tmp_path: pathlib.Path,
) -> None:
    """A recording over the size ceiling is reported."""
    root = _fake_tree(tmp_path, readme=_readme_with_a_recording_reference())
    _write_fake_mp4(root / PRIMARY_PATH, size=MAX_RECORDING_BYTES + 1)

    problems = _size_problems(root)

    assert problems, "a recording over the size ceiling was not reported -- so the guard is empty"
    assert PRIMARY_PATH in problems[0], (
        f"the oversized recording was reported without naming the file: {problems[0]!r}"
    )
