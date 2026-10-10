"""Every setting the code reads must reach the container that reads it.

## Why this file exists

``docker-compose.yml`` passes configuration into ``api`` and ``worker`` through
explicit ``${VAR:-default}`` entries. Nothing forces that list to stay in step
with ``src/opspilot/settings.py``: a setting can be added to ``Settings``,
documented in ``.env.example``, read correctly by the code -- and never named in
the compose file at all. The container then runs ``settings.py``'s default while
the operator's ``.env`` says something else, with no error anywhere.

That is not hypothetical. It is the third recurrence of one defect shape:

1. **M10** -- ``MCP_*_COMMAND`` was declared in ``settings.py``, documented in
   ``.env.example``, and read by nothing at all.
2. The follow-up pass wired ``MCP_*_COMMAND`` into the production path and
   corrected the docs/code disagreement, but did not add it to compose.
3. **This one** -- ``MODEL_TIMEOUT_SECONDS`` and ``MAX_STEPS`` were declared,
   documented, and read by the worker (``worker/__main__.py``,
   ``agents/runtime.py``) but absent from both compose services. A real
   end-to-end run had its planner burn all 24 steps on read calls because the
   ``MAX_STEPS`` written to ``.env`` never reached the process, and the run
   failed ``max_steps_exceeded`` for a reason the operator had already tried to
   configure away.

A green test suite is not evidence a setting is reachable. Every other guard in
this repository tests what the code *does* with a value; this one tests whether
the deployment can *supply* one. Nothing else fails when compose and settings
disagree, because compose is data, not code.

## What is asserted

For every environment variable name ``Settings`` declares, the name must appear
as a key in ``docker-compose.yml``'s ``environment`` block for **both** the
``api`` and the ``worker`` service.

Both, not either, and the reason is the one the compose file itself argues:
``api/__main__.py`` and ``worker/__main__.py`` each construct the same
``Settings`` object, so a variable present for one service and absent for the
other is a deployment whose two halves disagree about their own configuration.
It happens to be harmless today for the worker-only knobs (``MAX_STEPS`` reaches
``RunContext.max_steps`` through the worker alone) and it is exactly the shape
the next wiring bug takes. Requiring both services makes the invariant checkable
from one place rather than from a per-service allowlist nobody maintains.

``migrate`` is deliberately excluded: it builds a ``Settings`` object but reads
exactly one field from it (``DATABASE_URL``, via ``migrations/env.py``), and
passing it the operator's full configuration would widen what a one-shot schema
migration can be affected by. It is still checked for ``DATABASE_URL`` below.

The failure message names **every** missing name rather than the first one,
because the defect is almost never a single omission -- the twelve this file was
written for were missing together.

## Why the YAML is parsed with a regex

``PyYAML`` is installed in the development virtualenv and is **not** declared in
``pyproject.toml`` -- not in ``dependencies`` and not in the ``dev`` extra. A
test that imports it passes here and fails in a clean CI environment, which is
the "a dependency that only the author's machine happens to have is a dependency
the package does not really have" defect this repository has already recorded
once (``pyproject.toml``, on ``pgvector``).

So this file reads the compose text directly. The ``environment:`` blocks in
``docker-compose.yml`` are flat ``KEY: value`` mappings at a fixed indent, which
is the one YAML shape that is unambiguous to read without a parser -- and a
change that breaks that shape (list syntax, anchors, a merge key) makes this
guard fail loudly rather than silently skipping a block.

## Parsing the ``:-`` fallback

The second assertion is that where compose supplies ``${VAR:-fallback}``, the
fallback equals ``settings.py``'s default. Without it, compose becomes a second
source of truth for the same setting: an operator who reads ``docker-compose.yml``
to learn the shipped value is told one number and ``settings.py`` uses another,
and changing one silently leaves the other behind.

The exceptions are named, with their reasons, rather than skipped:

``DATABASE_URL``
    Not templated in any service. It names the ``postgres`` service on the
    compose network, which a host ``.env`` value cannot express; templating it
    would let a valid ``localhost:5432`` break every container.

``OPSPILOT_OPERATOR_TOKEN``
    The ``:?`` required form, with no default at all, which is stricter than
    matching a default would be.

``MCP_*_COMMAND``
    ``settings.py``'s default is a ``default_factory`` computing
    ``<this interpreter> -m mcp_servers.<name>.server``. Compose cannot
    reproduce that at render time, and it must not *substitute* for it: any path
    written into a ``${VAR:-...}`` fallback is the compose host's interpreter,
    not the container's, and would make the variable unoverridable at the same
    time. The fallback is therefore blank -- which is not a degraded value,
    because ``mcp_gateway.py::_command_for`` rebuilds the command from ``""``
    (an empty line in ``.env`` arrives as ``""`` rather than as unset). A
    separate test asserts the blank specifically.

``OPSPILOT_FAKE_SCENARIO``
    Deliberately non-empty in both services, where ``settings.py``'s default is
    empty. The worker's reason is the shipped one: a ``MODEL_PROVIDER=fake``
    worker with no scenario dies at ``classifying`` with
    ``UnmatchedFixtureError``. The api's reason is a pre-existing guard --
    ``test_compose_default_provider_has_fixtures.py`` reads the *first*
    ``${OPSPILOT_FAKE_SCENARIO:-...}`` in the file and fails on an empty one, so
    an empty default in the api would make it report a working stack as broken.
    Both are recorded as ``(service, variable)`` entries rather than as
    "these settings are exempt", so adding the variable to a third service
    without a decision fails this test.

``WORKER_ID`` (worker)
    Deliberately non-empty -- settings.py's default is empty, which falls back
    to ``worker-<pid>`` and says nothing about which host claimed a run.
"""

from __future__ import annotations

import pathlib
import re

from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# The services that construct a ``Settings`` object and run the system.
_SETTINGS_SERVICES = ("api", "worker")

# ``${VAR:-fallback}`` forms that must carry a BLANK fallback, as
# ``{variable: reason}``. These are the settings whose ``default_factory``
# computes a per-process value compose cannot reproduce, and ``.env.example``
# documents the blank line for each: "Leave these unset to use the default."
_MUST_BE_BLANK: dict[str, str] = {
    "MCP_CRM_COMMAND": "`default_factory` builds the interpreter command",
    "MCP_BILLING_COMMAND": "`default_factory` builds the interpreter command",
    "MCP_ISSUES_COMMAND": "`default_factory` builds the interpreter command",
}

# Settings exempt from the value comparison for a reason other than a
# ``default_factory``: ``variable -> reason``. The token has no fallback at all,
# which the templated test below checks separately.
_NO_COMPARABLE_DEFAULT: dict[str, str] = {
    "OPSPILOT_OPERATOR_TOKEN": "the `:?` required form, which has no fallback",
    **_MUST_BE_BLANK,
}

# Compose values that are fixed rather than templated, and why. Each is a
# deployment decision that ``settings.py``'s default cannot express; the
# reasoning is in this file's docstring.
_LITERAL_BY_DESIGN = frozenset({"DATABASE_URL"})

# ``${VAR:-fallback}`` forms whose fallback is not ``settings.py``'s default,
# as ``(service, variable)``. Each is a deployment decision the default cannot
# express, and each is one entry rather than one *setting*, so the set cannot
# quietly grow: ``OPSPILOT_FAKE_SCENARIO`` is asserted equal in `api` and
# exempt in `worker`.
_DELIBERATE_DEFAULTS: dict[tuple[str, str], str] = {
    ("api", "OPSPILOT_FAKE_SCENARIO"): (
        "duplicate_charge -- matches the worker's. The api builds no provider, "
        "so this is inert there, but "
        "tests/unit/test_compose_default_provider_has_fixtures.py reads the "
        "FIRST ${OPSPILOT_FAKE_SCENARIO:-...} in the file and fails on an empty "
        "one. An empty default in the api would make that guard report a "
        "working stack as broken."
    ),
    ("worker", "OPSPILOT_FAKE_SCENARIO"): (
        "duplicate_charge -- a MODEL_PROVIDER=fake worker with no scenario dies "
        "at `classifying` with UnmatchedFixtureError, so this default is what "
        "makes `docker compose up` complete a run on a fresh clone. Guarded by "
        "tests/unit/test_compose_default_provider_has_fixtures.py."
    ),
    ("worker", "WORKER_ID"): (
        "worker-compose -- settings.py's default is empty, which falls back to "
        "the pid (`worker-1234`), and that says nothing about which host "
        "claimed a run. A named value makes a claimed run attributable."
    ),
}


def _service_block(text: str, service: str) -> str:
    """The body of one top-level service, from its name to the next one.

    Services are two-space indented under ``services:``, so a line matching
    ``^  (\\w+):`` either opens one or closes the previous. Returning the slice
    rather than searching within it is what keeps ``MODEL_PROVIDER:`` from being
    found in the wrong service.
    """
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match is None:
            continue
        if match.group(1) == service:
            start = index + 1
            continue
        if start is not None:
            return "\n".join(lines[start:index])
    assert start is not None, (
        f"docker-compose.yml declares no service named {service!r}. This guard "
        f"reads each service's environment block out of the file; if the service "
        f"was renamed or removed, update this guard rather than letting it pass "
        f"vacuously. Services found: "
        f"{re.findall(r'^  ([A-Za-z0-9_-]+):', text, re.M)}"
    )
    return "\n".join(lines[start:])


def _environment(service_block: str) -> dict[str, str]:
    """The ``environment:`` mapping of one service block, as ``{KEY: value}``.

    Keys sit at six spaces under a four-space ``environment:``. Comment lines
    and blank lines are skipped; a line at a lower indent ends the block, which
    is what ``ports:``/``volumes:``/``depends_on:`` do.
    """
    lines = service_block.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if re.match(r"^    environment:\s*$", line)
        )
    except StopIteration:
        assert False, (  # noqa: B011 -- an assert is the assertion
            "a service declares no `environment:` block. Every service that "
            "constructs a Settings object needs one; without it nothing the "
            "operator writes to .env can reach the container."
        )

    values: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith("      "):
            break
        match = re.match(r"^      ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if match is not None:
            values[match.group(1)] = match.group(2).strip()
    return values


def _compose_environments() -> dict[str, dict[str, str]]:
    """``{service: {VAR: rendered-or-literal}}`` for every settings-bearing service."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    return {name: _environment(_service_block(text, name)) for name in _SETTINGS_SERVICES}


def _normalise(value: str) -> str:
    """A compose fallback rendered the way ``pydantic`` would read it.

    ``${MAX_STEPS:-24}`` is the string ``"24"`` to compose and the int ``24`` to
    pydantic, and ``${STORE_FULL_PROMPTS:-true}`` is ``"true"`` where the
    field default is the bool ``True``. Comparing the literal text would make
    this guard fail on a purely cosmetic difference in spelling; comparing
    through ``str()`` of the default keeps the assertion about the *value*.
    """
    return value.strip().strip("\"'").lower()


def _settings_aliases() -> set[str]:
    """Every environment variable name ``Settings`` declares.

    Read through :attr:`~pydantic.fields.FieldInfo.alias` rather than by
    upper-casing the field name, because the alias is the thing being asserted
    about and the two are independent: ``store_full_prompts`` becomes
    ``STORE_FULL_PROMPTS`` today, and a future field with a name and alias that
    differ must be caught rather than assumed to match. A field without an
    explicit alias would be read under its own name by pydantic-settings, so the
    fallback here is the field name -- and it is asserted to be non-empty so a
    nameless field fails this guard instead of being silently skipped.

    ``alias`` is typed ``str | None`` because pydantic allows an unaliased
    field. Every field in this project's ``Settings`` sets one explicitly; the
    narrowing below is what makes that a checked fact rather than an ``or`` that
    quietly yields ``""``.
    """
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        alias = field.alias if field.alias is not None else name
        assert alias, (
            f"Settings.{name} declares no alias and no usable field name. This "
            f"guard maps settings to compose by environment variable name; a "
            f"field it cannot name cannot be checked, which is how a setting "
            f"becomes invisible to the deployment."
        )
        names.add(alias)
    return names


def _settings_defaults() -> dict[str, object]:
    """``{env name: settings.py's default}``.

    Fields with a ``default_factory`` are excluded: their value is computed per
    process (it embeds ``sys.executable``) and compose has no way to reproduce
    it. ``MCP_*_COMMAND`` are the only three, and ``mcp_gateway.py`` rebuilds
    them from a blank string, so the exclusion loses nothing.
    """
    return {
        alias: field.default
        for alias, field in (
            (f.alias if f.alias is not None else n, f) for n, f in Settings.model_fields.items()
        )
        if field.default is not None and field.default.__class__.__name__ != "PydanticUndefinedType"
    }


def test_every_setting_the_code_reads_is_passed_to_every_service() -> None:
    """No ``Settings`` field is invisible to the deployment."""
    environments = _compose_environments()
    aliases = _settings_aliases()

    missing: dict[str, list[str]] = {}
    for service, values in environments.items():
        absent = sorted(aliases - values.keys())
        if absent:
            missing[service] = absent

    assert not missing, (
        "These environment variables are read by src/opspilot/settings.py but "
        "are not passed by docker-compose.yml, so a value written to .env never "
        f"reaches the container: {missing}.\n"
        "Add each one to the service's environment block as `${VAR:-<the default "
        "settings.py uses>}` -- an empty `:-` for a setting whose default is "
        "empty -- so compose is not a second source of truth. Every alias "
        f"settings.py declares ({len(aliases)} of them) must appear in api AND "
        "worker: both construct the same Settings object, and a value present "
        "for one and absent from the other is a deployment whose halves "
        "disagree. See tests/unit/test_compose_settings_are_wired.py."
    )


def test_compose_fallbacks_equal_the_settings_defaults() -> None:
    """Where compose supplies a fallback, it is the fallback ``settings.py`` has.

    This is the assertion that keeps compose from quietly becoming a second
    source of truth. A ``${VAR:-x}`` that disagrees with the field default means
    an operator reading one file is told a different number from the one the
    process will use, and moving the default in ``settings.py`` leaves the
    compose copy behind without failing anything.
    """
    defaults = _settings_defaults()
    disagreements: list[str] = []
    stale_exemptions: list[str] = []

    for service, values in _compose_environments().items():
        for variable, rendered in values.items():
            if variable in _LITERAL_BY_DESIGN or variable in _NO_COMPARABLE_DEFAULT:
                continue
            if (service, variable) in _DELIBERATE_DEFAULTS:
                continue
            match = re.match(r"^\$\{" + re.escape(variable) + r":-([^}]*)\}$", rendered)
            if match is None:
                # `${VAR:?...}` (required, no default) or a fixed literal.
                continue
            fallback = _normalise(match.group(1))
            expected = _normalise(str(defaults[variable]))
            if fallback != expected:
                disagreements.append(
                    f"{service}: {variable} compose defaults to {fallback!r}, "
                    f"settings.py defaults to {expected!r}"
                )

    # An exemption that no longer describes a disagreement is worse than no
    # exemption: it looks like a reviewed decision and stops being one. If a
    # deliberate default is ever aligned with settings.py, delete its entry
    # rather than leaving a stale one behind.
    for service, variable in _DELIBERATE_DEFAULTS:
        rendered = _compose_environments()[service].get(variable, "")
        match = re.match(r"^\$\{" + re.escape(variable) + r":-([^}]*)\}$", rendered)
        if match is not None and _normalise(match.group(1)) == _normalise(str(defaults[variable])):
            stale_exemptions.append(f"{service}: {variable}")

    assert not stale_exemptions, (
        f"These entries in _DELIBERATE_DEFAULTS no longer describe a difference: "
        f"{stale_exemptions}. Their compose fallback now equals settings.py's "
        f"default, so the exemption is stale -- delete it, or the next reader "
        f"will read a reviewed decision where there is none."
    )

    assert not disagreements, (
        "docker-compose.yml supplies a fallback that is not the fallback "
        f"settings.py uses: {disagreements}. Compose is a second source of truth "
        "the moment those two disagree: an operator who reads the compose file "
        "to learn the shipped value is told one number and the process uses "
        "another, and changing one leaves the other behind. Use settings.py's "
        "default, delete the fallback so the variable is passed through unset, "
        "or -- if the deployment genuinely needs a different value -- add a "
        "(service, variable) entry to _DELIBERATE_DEFAULTS saying why."
    )


def test_the_per_process_settings_keep_a_blank_fallback() -> None:
    """A setting compose cannot compute must not be given a fallback anyway.

    ``MCP_*_COMMAND``'s ``default_factory`` embeds ``sys.executable``, so its
    value is per-process and the fallback comparison cannot judge it. Compose
    also cannot *write* it: any path hardcoded into a ``${VAR:-...}`` fallback is
    this host's interpreter, not the container's, and the setting would stop
    being overridable at the same time -- an operator's ``.env`` value ignored
    with no error.

    So the rule is the one ``.env.example`` already states ("Leave these unset
    to use the default"): the fallback is blank, and
    ``mcp_gateway.py::_command_for`` rebuilds the command from ``""``. A blank
    line in ``.env`` arrives as ``""`` too, which is the same value -- so blank
    is the shipped behaviour, not a degraded one.
    """
    for service, values in _compose_environments().items():
        for variable in _MUST_BE_BLANK:
            rendered = values.get(variable)
            assert rendered == "${" + variable + ":-}", (
                f"{service}: {variable} renders as {rendered!r}. Its settings.py "
                f"default is built per process from sys.executable, so a fallback "
                f"written into docker-compose.yml would be the compose host's "
                f"interpreter rather than the container's, and would make the "
                f"variable unoverridable. Keep the fallback blank: "
                f"mcp_gateway.py::_command_for rebuilds the command from an empty "
                f"value, which is what .env.example documents."
            )


def test_the_required_token_stays_required_in_every_service() -> None:
    """``OPSPILOT_OPERATOR_TOKEN`` keeps the ``:?`` form, not a ``:-`` default.

    It is excluded from the fallback comparison above (it has no fallback), so
    nothing else here would notice if a well-meaning edit turned a required
    secret into an empty default. The API refuses to start without it
    (``api/auth.py::ensure_operator_token_configured``); a default would move
    that failure from "the API will not start" to "the API starts with a token
    nobody chose".
    """
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    for service in (*_SETTINGS_SERVICES, "web"):
        block = _service_block(text, service)
        rendered = _environment(block).get("OPSPILOT_OPERATOR_TOKEN", "")
        assert rendered.startswith("${OPSPILOT_OPERATOR_TOKEN:?"), (
            f"the {service} service no longer requires OPSPILOT_OPERATOR_TOKEN "
            f"with compose's `:?` form (it renders {rendered!r}). A default here "
            f"would let the service start with an operator token nobody chose."
        )


def test_the_migrate_service_gets_the_database_url_it_migrates() -> None:
    """``migrate`` is excluded from the sweep, but not from the URL.

    It builds a ``Settings`` object and reads one field from it. That field is
    the one whose absence makes the migration a no-op against a database nobody
    meant to migrate, so the exclusion is worth a test of its own rather than
    being left to the sweep's silence.
    """
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    values = _environment(_service_block(text, "migrate"))
    database_url = values.get("DATABASE_URL", "")
    assert database_url.startswith("postgresql+psycopg://"), (
        "the migrate service does not point at the compose Postgres with "
        f"DATABASE_URL (it renders {database_url!r}). migrations/env.py reads "
        "the URL from opspilot.settings, so this is the only place a schema "
        "migration learns which database it is running against."
    )
    assert "@postgres:" in database_url, (
        f"the migrate service's DATABASE_URL does not name the `postgres` "
        f"service on the compose network (it renders {database_url!r}). A host "
        f"address here migrates the wrong database, or nothing."
    )
