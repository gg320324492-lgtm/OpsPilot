"""The Postgres password is a stated fallback, not a fixed credential.

## Why this file exists

``docker-compose.yml`` used to set ``POSTGRES_PASSWORD: opspilot`` as a fixed
value. Nothing in the file said it was an example, so a deployment inherited a
credential that is written in the repository and in every README that quotes it,
with no signal anywhere that it needed changing.

A1 (``docs/phase2-plan.md``) bound the published ports to ``127.0.0.1``, so the
*network* half of that finding is closed: the port is not reachable from off the
host. What remains is that the value is silently true. A3's fix is to make it
come from the environment with a *documented* development fallback, which turns
"a credential nobody chose" into "a stated default a deployment overrides".

## The shape, and why it is a fallback rather than a refusal

The alternative considered was Compose's ``:?`` required form -- the shape
``OPSPILOT_OPERATOR_TOKEN`` uses, which refuses to render the file at all when
the variable is unset. It was rejected because it would red every documented
entry point on its first command: a fresh clone (``milestones.md`` D8: "a fresh
clone is meant to work"), the README's ``docker compose up``, and both CI jobs
that start this same stack. ``docker-build`` and ``web-e2e`` set
``OPSPILOT_OPERATOR_TOKEN`` and nothing else, so a required password is a stack
that refuses to come up for them.

So the property under test is not "a password must be set". It is the weaker --
and honest -- pair of facts:

* the fallback is **stated** (a literal ``opspilot`` in a ``${VAR:-...}``, which
  a reader can see is an example), not a fixed value dressed as a real one; and
* ``POSTGRES_PASSWORD`` is **documented** in ``.env.example`` and overridable,
  so a deployment changes it from one place.

A fixed ``POSTGRES_PASSWORD: opspilot`` fails the first; a ``:?`` form fails
"a fresh clone works" (D8), which this file asserts both directions of.

## Why this is a regex and not ``docker compose config``

Same reasoning as ``test_compose_settings_are_wired.py``: the pytest tree runs
in jobs that do not all have a Docker daemon, and a guard that skips without one
proves nothing (ADR-0004). Reading the ``${VAR:-...}`` out of the text is the
one YAML shape that is unambiguous without a parser, and it is the shape the
value is written in.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: The value a deployment inherits when it sets nothing. It is a
#: local-development default -- see the module docstring -- and naming it here
#: (rather than reading it out of the file and asserting it equals itself) is
#: what makes "the example credential changed" a visible edit to this guard.
_DEVELOPMENT_FALLBACK = "opspilot"


def _compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


def _postgres_environment() -> dict[str, str]:
    """The ``postgres`` service's ``environment:`` mapping, as ``{KEY: value}``.

    A regex over the *service block*, not the whole file, because the compose
    text names ``postgres`` in more than one place (``@postgres:`` inside a
    ``DATABASE_URL``, the ``postgres-data`` volume). Searching the file for a
    ``POSTGRES_PASSWORD`` line would find the right one today and the wrong service
    the moment a second service gained one.
    """
    text = _compose_text()
    match = re.search(r"^  postgres:\s*$", text, re.M)
    assert match is not None, (
        "docker-compose.yml declares no `postgres` service. This guard reads its "
        "environment block; if the service was renamed, update this guard rather "
        "than letting it pass against nothing."
    )
    remainder = text[match.end() :]
    # The block ends at the next two-space-indented service name.
    end = re.search(r"^  [A-Za-z0-9_-]+:\s*$", remainder, re.M)
    block = remainder[: end.start()] if end is not None else remainder

    values: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = re.match(r"^      ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if entry is not None:
            values[entry.group(1)] = entry.group(2).strip()
    assert values, (
        "the `postgres` service declares no environment entries this guard can "
        "read. It should at minimum set POSTGRES_USER/PASSWORD/DB; if the block's "
        "shape changed, fix this guard rather than letting it pass vacuously."
    )
    return values


def test_the_postgres_password_is_read_from_the_environment() -> None:
    """The password is ``${POSTGRES_PASSWORD:-...}``, so an operator can set it.

    A fixed ``POSTGRES_PASSWORD: opspilot`` is the defect A3 records: a
    deployment inherits it with nothing in the file to say it is an example.
    Reading it from the environment is what lets ``.env`` (gitignored, and where
    a real value belongs) reach the container -- the same contract every other
    setting in this file has.
    """
    rendered = _postgres_environment().get("POSTGRES_PASSWORD", "")
    assert rendered.startswith("${POSTGRES_PASSWORD:-"), (
        f"the postgres service's POSTGRES_PASSWORD renders as {rendered!r}, not "
        f"a ${{POSTGRES_PASSWORD:-...}} read from the environment. A fixed value "
        f"here is a credential committed to the repository that a deployment "
        f"inherits silently; the fix is to read it from the environment with a "
        f"documented fallback. See .env.example."
    )


def test_the_fallback_is_the_documented_development_default() -> None:
    """The fallback is the stated example value, not an empty or invented one.

    This is the half that keeps a fresh clone working (``milestones.md`` D8).
    An *empty* fallback would start Postgres with no password while
    ``DATABASE_URL`` still carries ``opspilot:opspilot`` -- a stack that comes up
    and then fails to authenticate, which is worse than either a stated default
    or a refusal. The fallback must be the value ``.env.example`` documents.
    """
    rendered = _postgres_environment().get("POSTGRES_PASSWORD", "")
    match = re.match(r"^\$\{POSTGRES_PASSWORD:-([^}]*)\}$", rendered)
    assert match is not None, (
        f"POSTGRES_PASSWORD renders as {rendered!r}, which is not the "
        f"${{POSTGRES_PASSWORD:-<default>}} fallback form. If it became the "
        f"required `:?` form, a fresh clone and both CI jobs that start this "
        f"stack stop coming up -- see this file's docstring for why that route "
        f"was rejected."
    )
    assert match.group(1) == _DEVELOPMENT_FALLBACK, (
        f"the postgres fallback password is {match.group(1)!r}, not the "
        f"documented development default {_DEVELOPMENT_FALLBACK!r}. Whatever it "
        f"is, it must match the value .env.example states and the password in "
        f"DATABASE_URL, or the migrate/api/worker services fail to authenticate."
    )


def test_the_password_is_not_a_database_url_alone() -> None:
    """``DATABASE_URL`` alone does not configure Postgres's own password.

    A tempting simplification is to drop ``POSTGRES_PASSWORD`` and rely on the
    ``opspilot:opspilot`` embedded in ``DATABASE_URL``. It does not work:
    ``POSTGRES_PASSWORD`` is what the image uses to *initialise* the database,
    and a mismatch is an authentication failure on the first ``migrate``. This
    asserts the variable is still present so that simplification is a failure
    rather than a stack that will not connect.
    """
    rendered = _postgres_environment()
    assert "POSTGRES_PASSWORD" in rendered, (
        "the postgres service no longer sets POSTGRES_PASSWORD. The image uses "
        "it to initialise the data directory; the password in DATABASE_URL only "
        "lets the app connect. Dropping it does not remove the credential, it "
        "breaks the connection."
    )


def test_the_env_example_documents_the_password() -> None:
    """``.env.example`` names it, so the fallback is documented rather than silent.

    A3's acceptance is that the fallback is *documented*. A reader who opens
    ``.env.example`` -- the file the project's own comments call the place every
    value is explained -- must find the variable and be told the value is a
    development one, not a secret.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assignments = re.findall(r"^POSTGRES_PASSWORD=(.*)$", text, re.M)
    assert assignments, (
        ".env.example does not set POSTGRES_PASSWORD, so the fallback in "
        "docker-compose.yml is undocumented -- a silent default is exactly the "
        "finding this guard exists for. Add the variable and a note that the "
        "shipped value is for local development."
    )
    assert [a.strip() for a in assignments] == [_DEVELOPMENT_FALLBACK], (
        f".env.example sets POSTGRES_PASSWORD to {assignments!r}. It must be the "
        f"single development default {_DEVELOPMENT_FALLBACK!r} that "
        f"docker-compose.yml falls back to (and that DATABASE_URL matches) -- a "
        f"real secret must never be committed here."
    )
