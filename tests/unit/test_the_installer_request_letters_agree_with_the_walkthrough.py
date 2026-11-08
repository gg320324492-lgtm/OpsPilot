"""The two request letters must describe the walk-through's world, not their own.

## Why this file exists

``docs/installer-verification.request.md`` and its Chinese sibling are the only
documents in the repository written to somebody who has never seen the
project. They are a letter, so they read as a letter — prose, an explanation,
a list of what to bring — and they quote the walk-through's facts loosely, in
whatever words fit the sentence:

- "About 6 GB of free disk";
- "the walk-through allows 5 to 15 minutes";
- "the app builds three container images from source";
- "the last section asks for the output of the two commands".

Every one of those is a **claim about what will happen on the reader's
machine**, and every one of them can go stale on its own: the walk-through's
"what you need" table changes, the compose file grows a fourth built image,
§7 asks for a third command. Nothing held the letter to the walk-through, so
the two could disagree and a stranger would be the one to find out.

The failure mode is worse here than in an internal document. A reader who is
told to allow 15 minutes and finds a 40-minute build may simply walk away; a
reader who is told "6 GB" and finds the disk full has already installed
half of what was asked for.

## What this file asserts

For each of the facts the letters quote, that the walk-through and the letter
say the same thing, and — where the fact is about the tree rather than about
the prose — that the tree agrees with both:

1. the free-disk figure;
2. the first-launch time window;
3. the number of container images the first launch builds, checked against the
   compose file's ``build:`` blocks;
4. the installer's file name;
5. the recording's container format, and that neither letter names a recording
   file the walk-through does not;
6. the number of commands the walk-through's last section asks for;
7. the Windows version;
8. the credential the reader is told not to paste.

And that both letters point at walk-through sections that exist.

## What it does not do

It does not compare the prose, and it does not translate anything. The wording
of the two letters is allowed to differ — that is the point of a translation —
and what has to match is the set of things a reader compares against their
screen.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The walk-through — the authority the letters point at.
WALKTHROUGH = REPO_ROOT / "docs" / "installer-verification.md"

#: The two letters.
LETTER_EN = REPO_ROOT / "docs" / "installer-verification.request.md"
LETTER_ZH = REPO_ROOT / "docs" / "installer-verification.request.zh-CN.md"

#: The compose file the first launch builds its images from.
COMPOSE = REPO_ROOT / "docker-compose.yml"

#: The screenshots the walk-through says it has.
SCREENSHOTS = REPO_ROOT / "docs" / "assets" / "screenshots"


def _text(path: pathlib.Path) -> str:
    """A document's text."""
    return path.read_text(encoding="utf-8")


def _heading_line(heading: str) -> str:
    """The walk-through's heading line for a section, by its title.

    The walk-through numbers its top-level sections ("## 7. Reporting") and
    leaves its subsections unnumbered ("### Record the walk-through"), so a
    lookup has to accept both shapes.
    """
    lines = _text(WALKTHROUGH).splitlines()
    pattern = re.compile(rf"^#{{2,3}}\s+(?:\d+\.\s+)?{re.escape(heading)}\s*$")
    for index, line in enumerate(lines):
        if pattern.match(line):
            return str(index)
    raise AssertionError(f"the walk-through has no section {heading!r}")


def _section(heading: str) -> str:
    """The body of one section of the walk-through."""
    lines = _text(WALKTHROUGH).splitlines()
    start = int(_heading_line(heading))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def _built_services() -> list[str]:
    """The compose services that carry a ``build:`` block."""
    lines = _text(COMPOSE).splitlines()
    built: list[str] = []
    for index, line in enumerate(lines):
        if not re.match(r"^\s{4}build:\s*$", line):
            continue
        for earlier in range(index - 1, -1, -1):
            match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[earlier])
            if match:
                built.append(match.group(1))
                break
    return built


# ---------------------------------------------------------------------------
# The sections the letters point at
# ---------------------------------------------------------------------------


def test_every_section_the_letters_point_at_exists() -> None:
    """A letter that points at a section the walk-through does not have is broken.

    This is the mechanical version of "the walk-through's *Where to get the
    installer* section has the exact path": the section has to be there.
    """
    for heading in (
        "Where to get the installer",
        "Record the walk-through",
        "Reporting",
    ):
        _heading_line(heading)


# ---------------------------------------------------------------------------
# The facts the letters quote
# ---------------------------------------------------------------------------


def test_the_free_disk_figure_agrees() -> None:
    """The disk figure the letters quote is the walk-through's figure."""
    needs = _section("Before you start")
    match = re.search(r"Free disk\s*\|\s*~(\d+)\s*GB", needs)
    assert match is not None, (
        "the walk-through's 'what you need' table no longer states a free-disk "
        "figure; the letters still quote one"
    )
    gigabytes = match.group(1)
    for letter in (LETTER_EN, LETTER_ZH):
        assert f"{gigabytes} GB" in _text(letter), (
            f"{letter.name} does not quote the walk-through's {gigabytes} GB free-disk figure"
        )


def test_the_first_launch_time_window_agrees() -> None:
    """The letters' time window is the walk-through's time window."""
    needs = _section("Before you start")
    match = re.search(
        r"Time\s*\|\s*first launch (\d+)[\u2013-](\d+) minutes",
        needs,
    )
    assert match is not None, (
        "the walk-through's 'what you need' table no longer states the "
        "first-launch time window; the letters still quote one"
    )
    low, high = match.groups()
    assert f"{low} to {high} minutes" in _text(LETTER_EN), (
        f"the English letter does not quote the walk-through's {low}-{high} minute window"
    )
    assert f"{low} 到 {high} 分钟" in _text(LETTER_ZH), (
        f"the Chinese letter does not quote the walk-through's {low}-{high} minute window"
    )


def test_the_number_of_images_built_agrees() -> None:
    """The letters' "three container images" is what the compose file builds.

    The walk-through is explicit about which of the compose file's ``build:``
    blocks the first launch builds: ``migrate``, ``api`` and ``worker``. The
    fourth service, ``web``, has a build block too and is never built by the
    app. So the number the letters give to a stranger is three, and this
    checks it against the file rather than against the prose.
    """
    built = _built_services()
    assert built == ["migrate", "api", "worker", "web"], (
        f"the compose file's build blocks moved: {built}; the walk-through "
        "names migrate, api and worker as the three the first launch builds"
    )
    assert "three container images from source" in _text(LETTER_EN), (
        "the English letter no longer says the first launch builds three "
        "container images from source"
    )
    assert "三个容器镜像" in _text(LETTER_ZH), (
        "the Chinese letter no longer says the first launch builds three "
        "container images from source"
    )
    assert "three container images" in _text(WALKTHROUGH), (
        "the walk-through no longer states that three images are built from source"
    )


def test_the_installer_file_name_agrees() -> None:
    """All three documents name the same installer file."""
    name = "OpsPilot-Setup-<version>-x64.exe"
    for document in (WALKTHROUGH, LETTER_EN, LETTER_ZH):
        assert name in _text(document), f"{document.name} does not name the installer {name}"


def test_the_recording_format_agrees_and_neither_letter_names_a_file() -> None:
    """MP4 is the format, and the letters defer to the walk-through on the name.

    The walk-through fixes the recording's file name in advance and checks it
    with a test. A letter that named a *different* file would send the reader
    to produce something the repository would refuse — so the letters must not
    name one at all, and this asserts both that they do not and that the
    walk-through names exactly one.
    """
    assert "MP4" in _text(LETTER_EN) and "MP4" in _text(LETTER_ZH), (
        "the letters no longer say the recording is an MP4"
    )
    for letter in (LETTER_EN, LETTER_ZH):
        assert not re.search(r"d2-walkthrough\.\w+", _text(letter)), (
            f"{letter.name} names a recording file; the walk-through owns that "
            "name, and a second one would send the reader to the wrong file"
        )
    named = set(re.findall(r"docs/assets/d2-walkthrough\.\w+", _text(WALKTHROUGH)))
    assert named == {"docs/assets/d2-walkthrough.mp4", "docs/assets/d2-walkthrough.webm"}, (
        f"the walk-through now names {sorted(named)}; it names exactly two — "
        "the MP4 the project expects and the WebM fallback for a recorder "
        "that writes nothing else — and a third would be a name the reader "
        "could pick by mistake"
    )
    recording = _section("Record the walk-through")
    assert "The file the project expects is" in recording, (
        "the walk-through no longer says which of the two recording names is "
        "the one the project expects"
    )


def test_the_number_of_commands_the_last_section_asks_for_agrees() -> None:
    """The letters promise the output of "the two commands" §7 asks for.

    This is the one fact the letters are most likely to get wrong, because
    §7 is a numbered list a reader counts, and it also mentions ``docker
    version`` in a later item. The letters' claim is about the pair.
    """
    reporting = _section("Reporting")
    item = re.search(r"^2\.\s*(.*?)(?=^\d+\.)", reporting, re.M | re.S)
    assert item is not None, "the walk-through's §7 no longer has a second item"
    commands = re.findall(r"`([^`]+)`", item.group(1))
    commands = [command for command in commands if "docker" in command]
    assert len(commands) == 2, (
        f"the walk-through's §7 now asks for {len(commands)} commands "
        f"({commands}); the letters promise two"
    )
    assert "the two commands" in _text(LETTER_EN), (
        "the English letter no longer says the last section asks for two commands"
    )
    assert "两条命令" in _text(LETTER_ZH), (
        "the Chinese letter no longer says the last section asks for two commands"
    )


def test_the_windows_version_agrees() -> None:
    """Both letters quote the walk-through's Windows requirement."""
    needs = _section("Before you start")
    assert re.search(r"Windows\s*\|\s*10 or 11, x64", needs), (
        "the walk-through's 'what you need' table no longer states the Windows "
        "requirement; the letters still quote one"
    )
    assert "10 or 11" in _text(LETTER_EN), "the English letter no longer says Windows 10 or 11"
    assert "10 或 11" in _text(LETTER_ZH), "the Chinese letter no longer says Windows 10 or 11"


def test_both_letters_tell_the_reader_not_to_paste_the_credential() -> None:
    """The redaction instruction is in both letters and names the right thing.

    This is the one instruction that protects the reader as well as the
    project: the app writes a real operator token into a file on the reader's
    machine, and a report that quotes the window log verbatim could quote it.
    """
    for letter in (LETTER_EN, LETTER_ZH):
        text = _text(letter)
        assert "operator token" in text, (
            f"{letter.name} no longer tells the reader not to paste the operator token"
        )
        assert ".env" in text, f"{letter.name} no longer tells the reader not to paste a .env file"
        assert "[redacted]" in text, (
            f"{letter.name} no longer says what to do if a line carries one"
        )


def test_the_screenshot_count_the_walkthrough_quotes_is_the_real_one() -> None:
    """The walk-through says how many screenshots it has; the tree agrees.

    The letters say "the repository has screenshots of the app" without a
    number, so this is not a letter claim — it is the walk-through's, and the
    walk-through is the one the reader will check against the repository.
    """
    recording = _section("Record the walk-through")
    match = re.search(r"The (\w+) screenshots in this repository", recording)
    assert match is not None, "the walk-through no longer says how many screenshots it has"
    words = {
        "ten": 10,
        "nine": 9,
        "eight": 8,
        "seven": 7,
        "six": 6,
        "five": 5,
        "eleven": 11,
        "twelve": 12,
    }
    word = match.group(1)
    assert word in words, f"the walk-through says '{word} screenshots', which is not a number"
    actual = len(list(SCREENSHOTS.glob("*.png")))
    assert actual == words[word], (
        f"the walk-through says {word} ({words[word]}) screenshots; the repository has {actual}"
    )
