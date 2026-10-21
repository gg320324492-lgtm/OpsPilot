"""``.env.example`` and the settings that read it must name the same things.

## Why this file exists

``.env.example`` is the committed template a reader copies to ``.env``. It is
**not** a secret file: it carries placeholder values and is in the repository,
so reading it is safe -- and the values below are never printed, only the
variable *names*.

Two claims met here, and neither was checked:

- ``settings.py`` said "Every variable named in ``.env.example`` is read here
  and nowhere else". One is not: ``POSTGRES_PASSWORD`` is the Compose
  stack's database password, read by ``docker-compose.yml``.
- the template's own header said "Every value below is read by
  ``src/opspilot/settings.py``" -- the same claim, the other direction, and
  the same one exception.

Both now say "every value except ``POSTGRES_PASSWORD``", and this file is
what holds them to it: a setting that is not in the template is a setting a
reader cannot configure, and a template entry that is not a setting is a
variable a reader sets and nothing reads.

## What this file asserts

1. **Every setting is in the template**, by its environment-variable name.
2. **Every template entry is a setting**, or is one of the named exceptions,
   and the exception list is explicit rather than a pattern.
3. **Every value in the template is accepted by the model**, so a reader who
   copies the file gets a process that starts. This is the one that catches a
   placeholder that was renamed into something the model rejects.
"""

from __future__ import annotations

import pathlib

import pytest
from pydantic import ValidationError

from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / ".env.example"

#: Template entries that are **not** settings, and why. Each one is a
#: deliberate exception with a reason, so adding a fourth means writing the
#: reason down.
NOT_A_SETTING: dict[str, str] = {
    "POSTGRES_PASSWORD": (
        "the Compose stack's database password: read by docker-compose.yml for "
        "the postgres service and for the DATABASE_URL it hands the api and "
        "the worker, and by Postgres itself when it initialises a data "
        "directory. OpsPilot's own process never reads it."
    ),
}


def _template() -> dict[str, str]:
    """The template's variables and their values."""
    values: dict[str, str] = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _aliases() -> dict[str, str]:
    """Every setting's environment-variable name, mapped to its field name."""
    aliases: dict[str, str] = {}
    for field, info in Settings.model_fields.items():
        alias = info.alias or info.validation_alias
        if alias:
            aliases[alias] = field
    return aliases


def test_every_setting_is_in_the_template() -> None:
    """A setting the template does not name is a setting nobody can set."""
    template = _template()
    missing = sorted(set(_aliases()) - set(template))
    assert not missing, (
        f"these settings are not in .env.example: {missing}. A reader who copies "
        "the template cannot configure them, and the default they fall back to "
        "is not the one they wanted."
    )


def test_every_template_entry_is_a_setting_or_a_named_exception() -> None:
    """A template entry nothing reads is a variable a reader sets for nothing."""
    template = _template()
    aliases = _aliases()
    unknown = sorted(set(template) - set(aliases) - set(NOT_A_SETTING))
    assert not unknown, (
        f".env.example names {unknown}, which is neither a setting nor one of "
        f"the declared exceptions ({sorted(NOT_A_SETTING)}). A variable in the "
        "template that nothing reads is a setting the reader believes they made."
    )


def test_every_declared_exception_is_still_in_the_template() -> None:
    """The exception list is not a place where entries go to die.

    A ``POSTGRES_PASSWORD`` that left the template would leave the exception
    naming a variable that is no longer there, and the reader would look for
    it in the wrong place.
    """
    template = _template()
    gone = sorted(set(NOT_A_SETTING) - set(template))
    assert not gone, (
        f"these are declared as template exceptions but are not in the template "
        f"any more: {gone}. Either they were removed -- in which case the "
        "exception goes too -- or they were renamed."
    )


def test_every_value_in_the_template_is_accepted_by_the_model() -> None:
    """A copied template must produce a process that starts.

    The values are placeholders and are never printed: the assertion names the
    variable, not what it was set to.
    """
    template = _template()
    aliases = _aliases()
    rejected: list[str] = []
    for key, value in template.items():
        field = aliases.get(key)
        if field is None or not value:
            continue
        try:
            Settings.model_validate({field: value})
        except ValidationError as exc:
            rejected.append(f"{key} ({exc.error_count()} error(s))")
    assert not rejected, (
        f"these template values are rejected by the settings model: {rejected}. "
        "A reader who copies the file and fills in the rest gets a process "
        "that refuses to start."
    )


@pytest.mark.parametrize("name", sorted(NOT_A_SETTING))
def test_every_exception_says_why(name: str) -> None:
    """An exception without a reason is an exception that will be forgotten."""
    reason = NOT_A_SETTING[name]
    assert len(reason) > 40, (
        f"the exception for {name} has no explanation. An exception list whose "
        "entries do not say why is a list that grows."
    )
