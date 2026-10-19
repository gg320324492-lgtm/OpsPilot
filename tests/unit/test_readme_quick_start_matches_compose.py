"""The README's quick start must be one that works.

## Why this file exists

The README is the one document a stranger reads first, and its quick start is
the one thing they do first. A quick start that cannot work is worse than no
quick start: it is a first impression of a broken project, and it is the
thing a reviewer tries before reading anything else.

This repository's quick start was wrong in a way that took a closing audit to
find. ``docker-compose.yml`` uses the ``:?`` form for
``OPSPILOT_OPERATOR_TOKEN``:

    OPSPILOT_OPERATOR_TOKEN: ${OPSPILOT_OPERATOR_TOKEN:?OPSPILOT_OPERATOR_TOKEN must be set in the environment or a .env file}

The ``:?`` form is deliberate and good: an unset token stops Compose from
rendering the project at all, so a deployment that would have an API refusing
every write never starts in the first place. But the README said the opposite
— "**.env** is optional for a first look — compose falls back to
local-development defaults so a fresh clone comes up with no configuration at
all" — and the quick start did not copy ``.env.example`` or mention the token
at all.

So the README and the compose file each asserted a different truth about the
same command, and only one of them was right. That is the defect class this
repository treats as a defect wherever it appears: **a document reading as
evidence of something the tree does not say.**

## What is asserted

The README and the compose file must agree about whether ``.env`` is needed,
because the two of them are what a reader follows together.

The check is static, and it reads the *compose file* rather than running it —
running Compose needs a Docker daemon, and this suite is the part of the test
suite that must run anywhere. The static fact is enough: the ``:?`` form is
visible in the file, and it is the only thing that decides whether Compose
will render the project.

## Why the assertion is about the *shape* of the README, not a phrase

Pinning an exact sentence would make this test fail the next time somebody
rewords the paragraph, and a test that fails on a reword is a test that gets
deleted. So what is asserted is the **claim**: that the README says ``.env``
is required, and does not say it is optional. The wording is free; the
promise is not.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
README = REPO_ROOT / "README.md"

# The variable whose absence stops Compose rendering the project. Named here
# because the test's claim is about *this* variable: the compose file is the
# authority on which settings are required, and this one is required.
REQUIRED_VARIABLE = "OPSPILOT_OPERATOR_TOKEN"


def _compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


def _required_in_compose() -> list[str]:
    """The variables Compose refuses to render the project without.

    The ``:?`` form is the one that makes a variable required: ``${VAR:?why}``
    fails the interpolation when ``VAR`` is unset or empty. ``:-`` supplies a
    default and ``-`` supplies an empty one, and neither stops the project
    from rendering.

    Read out of the file as text rather than out of the parsed YAML, because
    the *form* is the thing being detected — a parsed YAML value is a string
    either way, and the distinction between "required" and "has a default"
    only exists in the source.
    """
    return sorted(
        {
            match.group(1)
            for match in re.finditer(r"\$\{([A-Z0-9_]+):\?", _compose_text())
        }
    )


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_the_compose_file_really_does_require_the_token() -> None:
    """The premise of the rest of this file, asserted so it cannot rot.

    If the compose file ever stops requiring the token, this file's whole
    premise is gone — and the README's claim would become true again, at
    which point this test would be asserting a falsehood for a reason nobody
    remembers.
    """
    required = _required_in_compose()
    assert REQUIRED_VARIABLE in required, (
        f"the compose file no longer requires {REQUIRED_VARIABLE} in the `:?` form; "
        f"it requires {required}. If that is deliberate, this file and the README's "
        f"quick start must both be revisited — a required setting is a promise."
    )


def test_the_readme_does_not_claim_the_env_file_is_optional() -> None:
    """The claim that was wrong.

    "`.env` is optional" is a specific sentence, and the check is on the
    *claim* rather than on the exact words: any sentence telling a reader
    that no configuration file is needed is the same defect, however it is
    phrased.
    """
    readme = _readme_text()
    for phrase in (
        "`.env` is optional",
        "env is optional",
        "optional for a first look",
    ):
        assert phrase not in readme, (
            f"the README still says \"{phrase}\", but {REQUIRED_VARIABLE} is required "
            f"in the compose file's `:?` form, so `docker compose up` refuses to "
            f"render the project without it. A reader who follows the quick start "
            f"hits that refusal on the first command."
        )


def test_the_readme_says_the_env_file_is_required() -> None:
    """The claim that replaced it, in the same place.

    A reader must be told what to do, not only what not to do. The README has
    to say the ``.env`` file is needed.
    """
    readme = _readme_text()
    assert "`.env` is required" in readme, (
        "the README must say that `.env` is required, and why: the compose file "
        "refuses to render the project without a token, and a reader who is not "
        "told that will only find out from a refusal."
    )


def test_the_readme_quick_start_copies_the_env_example() -> None:
    """The quick start must contain the step that makes it work.

    "``.env`` is required" in prose is not enough: the reader is going to
    follow the commands, and the commands have to include the copy.
    """
    readme = _readme_text()
    assert "cp .env.example .env" in readme, (
        "the quick start must copy `.env.example` to `.env` — it is the step "
        "that produces the file the reader is told is required, and without it "
        "the instructions do not add up."
    )


def test_the_readme_quick_start_names_the_variable_to_set() -> None:
    """The reader must be told *which* setting to set, not just to configure."""
    readme = _readme_text()
    assert REQUIRED_VARIABLE in readme, (
        f"the README must name {REQUIRED_VARIABLE}: it is the one setting the "
        f"compose file refuses to render the project without, so a reader who "
        f"is not told its name cannot satisfy the requirement."
    )


@pytest.mark.parametrize("variable", _required_in_compose())
def test_every_required_variable_is_named_in_the_readme(variable: str) -> None:
    """Whichever variables Compose requires, the README has to name them.

    The token is the one that bites today, but the rule is the general one:
    a setting that stops the project from rendering is a setting a reader
    must be told about, and the set of them is read from the compose file
    rather than maintained by hand.
    """
    readme = _readme_text()
    assert variable in readme, (
        f"the compose file requires {variable} (the `:?` form), so the README "
        f"must name it — a required setting the reader is not told about is a "
        f"first command that fails."
    )
