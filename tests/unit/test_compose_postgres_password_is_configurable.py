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

## The second half: the password is *linked*, not merely present

The first half of this file is about the value. This half is about the
**linkage**, and it is a separate defect that a check of the first kind cannot
see.

The defect it exists for: ``docker-compose.yml`` set
``POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-opspilot}`` on the ``postgres``
service, and every service's ``DATABASE_URL`` carried **its own literal
password**::

    DATABASE_URL: postgresql+psycopg://opspilot:opspilot@postgres:5432/opspilot

Those two are the same credential, and nothing tied them together. A
deployment that set ``POSTGRES_PASSWORD`` to something else got a database
*initialised* with the new password and an application connecting with the old
one -- so every ``migrate``/``api``/``worker`` authentication failed, and the
only remedy the README could offer was to "change the password in
``DATABASE_URL`` to match", which meant hand-editing a committed file. The
variable was *present* and *overridable*; the deployment was still broken.

The fix is the obvious one, and the property under test is therefore the
**linkage** rather than the presence of the variable:

* every service that declares a ``DATABASE_URL`` must take its password from
  ``POSTGRES_PASSWORD`` -- the same variable, by the same expression -- so
  setting one changes the other and they cannot drift; and
* the *host* of that URL must **not** be templated, because the host names
  the ``postgres`` service on the Compose network while a host ``.env``'s
  ``localhost:5432`` is the operator's own machine. That asymmetry is
  deliberate, and a future edit that templated the host would be a regression
  of a different kind.

So this file asserts a *relationship* between two lines of a YAML file, for
**every** service that has a ``DATABASE_URL`` -- not for one of them. A
service that kept a literal password would be exactly the defect, one service
deep.

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
from urllib.parse import urlsplit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: The value a deployment inherits when it sets nothing. It is a
#: local-development default -- see the module docstring -- and naming it here
#: (rather than reading it out of the file and asserting it equals itself) is
#: what makes "the example credential changed" a visible edit to this guard.
_DEVELOPMENT_FALLBACK = "opspilot"

#: The services that talk to the database, and so must each carry a
#: ``DATABASE_URL`` whose password comes from ``POSTGRES_PASSWORD``.
#:
#: Named rather than derived, because a guard that enumerates the services it
#: finds cannot tell "the worker stopped connecting" from "the worker was never
#: looked at": a ``DATABASE_URL`` deleted from one of them would leave the
#: per-service assertions below passing against the two that remained. The
#: set is asserted to be *present* rather than to be *exactly* this, so a new
#: service that connects to the database is covered by the same rules without
#: needing this list edited first.
_DATABASE_SERVICES = ("api", "migrate", "worker")


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


def _service_blocks() -> dict[str, str]:
    """Every service's block of text, keyed by service name.

    A regex over the *services section*, not the whole file. The compose text
    names services in more than one place -- ``postgres`` in a ``DATABASE_URL``,
    ``postgres`` and ``migrate`` as ``depends_on`` keys, ``api`` inside
    ``API_INTERNAL_BASE_URL`` -- and a scan of the whole file would find
    block boundaries that are not services at all.

    The service name is the only key at exactly two spaces of indentation
    (a service's own keys are at four, and the file's top-level keys are at
    zero), which is what makes the pattern unambiguous without a YAML parser.
    """
    text = _compose_text()
    start = re.search(r"^services:\s*$", text, re.M)
    assert start is not None, (
        "docker-compose.yml declares no `services:` section. This guard reads the "
        "service blocks out of it; if the file's shape changed, update this guard "
        "rather than letting it pass against nothing."
    )
    remainder = text[start.end() :]
    # The section ends at the next top-level key -- `volumes:`.
    end = re.search(r"^[A-Za-z0-9_-]+:\s*$", remainder, re.M)
    section = remainder[: end.start()] if end is not None else remainder

    matches = list(re.finditer(r"^  ([A-Za-z0-9_.-]+):\s*$", section, re.M))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks[match.group(1)] = section[match.end() : stop]
    assert blocks, (
        "docker-compose.yml's services section names no service this guard can "
        "read. If the file's indentation changed, fix this guard rather than "
        "letting it pass vacuously."
    )
    return blocks


def _environment_of(block: str) -> dict[str, str]:
    """A service's ``environment:`` mapping, as ``{KEY: value}``.

    Scoped to the ``environment:`` block rather than to the whole service
    block, because a service block contains other four-space keys whose
    children are also six-space ``KEY: value`` lines -- ``depends_on:`` has a
    ``postgres:`` key at that indent, which is a dependency and not an
    environment variable.
    """
    start = re.search(r"^    environment:\s*$", block, re.M)
    if start is None:
        return {}
    remainder = block[start.end() :]
    # The mapping ends at the next key at or above the `environment:` indent.
    end = re.search(r"^ {0,4}\S", remainder, re.M)
    entries = remainder[: end.start()] if end is not None else remainder

    values: dict[str, str] = {}
    for line in entries.splitlines():
        entry = re.match(r"^      ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if entry is not None:
            values[entry.group(1)] = entry.group(2).strip()
    return values


def _database_urls() -> dict[str, str]:
    """Every service's ``DATABASE_URL``, keyed by service name.

    Only the services that declare one: ``postgres`` initialises the database
    and ``web`` serves the dashboard, and neither of them connects to it.
    """
    urls: dict[str, str] = {}
    for service, block in _service_blocks().items():
        value = _environment_of(block).get("DATABASE_URL")
        if value is not None:
            urls[service] = value
    return urls


def _password_of(url: str) -> str:
    """The password in a ``DATABASE_URL``.

    Read with ``urlsplit`` rather than by splitting the string on ``:`` and
    ``@``: the password is itself a ``${...}`` expression, and a hand-rolled
    split is exactly the kind of thing that gets the *wrong* component when
    the value it is splitting grows a colon. ``urlsplit`` splits the userinfo
    at the *last* ``@`` and the password at the *first* ``:`` after it, which
    is the shape these URLs have.
    """
    return urlsplit(url).password or ""


def _authority_of(url: str) -> str:
    """The ``host[:port]`` of a URL, with the userinfo removed."""
    return urlsplit(url).netloc.rpartition("@")[2]


def _host_of(url: str) -> str:
    """The host a ``DATABASE_URL`` names, lowercased."""
    return urlsplit(url).hostname or ""


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


# ---------------------------------------------------------------------------
# The linkage: the password in every DATABASE_URL IS the POSTGRES_PASSWORD
# expression, so the two cannot drift apart.
# ---------------------------------------------------------------------------


def test_every_database_service_declares_a_database_url() -> None:
    """The three services that connect are all *looked at*.

    A guard that enumerates the services it finds cannot tell "the worker
    stopped connecting" from "the worker was never examined": a
    ``DATABASE_URL`` deleted from one of them would leave the per-service
    assertions below passing against the two that remained, and a green run
    would mean less than it says. The set is asserted to be *present* rather
    than to be *exactly* these, so a new service that talks to the database
    is covered by the same rules without this list having to be edited first.
    """
    urls = _database_urls()
    missing = [service for service in _DATABASE_SERVICES if service not in urls]
    assert not missing, (
        f"the {missing} service(s) declare no DATABASE_URL this guard can read. "
        f"Every service that connects to the database must carry one whose "
        f"password comes from POSTGRES_PASSWORD; a service this guard cannot "
        f"see is a service it cannot hold to that rule. The services it did "
        f"find: {sorted(urls)}."
    )


def test_every_database_url_takes_its_password_from_postgres_password() -> None:
    """Every service's password is the ``${POSTGRES_PASSWORD:-...}`` expression.

    A literal password in a ``DATABASE_URL`` is the defect: the database is
    initialised with ``POSTGRES_PASSWORD`` and the application connects with
    whatever the URL says, so the two are separate credentials that happen to
    agree today. A deployment that sets the variable then gets a stack whose
    every ``migrate``/``api``/``worker`` authentication fails -- which is why
    the README used to say to hand-edit the URL in this file to match.
    """
    urls = _database_urls()
    assert urls, (
        "no service in docker-compose.yml declares a DATABASE_URL. Either the "
        "file's shape changed or the services that connect to the database were "
        "removed; either way, this guard is now asserting nothing."
    )
    for service, url in sorted(urls.items()):
        password = _password_of(url)
        assert re.fullmatch(r"\$\{POSTGRES_PASSWORD:-[^}]*\}", password) is not None, (
            f"the DATABASE_URL of the {service!r} service has the password "
            f"{password!r}, which is not a ${{POSTGRES_PASSWORD:-<default>}} "
            f"expression. The URL is {url!r}. A literal password here is a "
            f"second credential that has to be kept in step with "
            f"POSTGRES_PASSWORD by hand, and a deployment that changed only the "
            f"variable would get a stack that refuses every connection. The "
            f"password must come from the same variable the postgres service "
            f"is initialised with."
        )


def test_every_database_url_uses_the_postgres_services_own_password() -> None:
    """The expression is the *same* one, character for character.

    The test above asserts the shape; this asserts the linkage. The two lines
    must be the identical expression -- ``${POSTGRES_PASSWORD:-<fallback>}``
    with the same fallback -- so that changing the fallback in one place is a
    visible failure in the other rather than a silent divergence. A stack
    where the database is initialised with one fallback and the application
    connects with another fails its first authentication, which is the same
    outage as the literal password above and just as silent from a distance.
    """
    rendered = _postgres_environment().get("POSTGRES_PASSWORD", "")
    urls = _database_urls()
    assert urls, (
        "no service in docker-compose.yml declares a DATABASE_URL, so there is "
        "nothing to compare the postgres service's password against."
    )
    for service, url in sorted(urls.items()):
        password = _password_of(url)
        assert password == rendered, (
            f"the DATABASE_URL of the {service!r} service takes its password "
            f"from {password!r}, but the postgres service is initialised with "
            f"{rendered!r}. They are the same credential and they must be the "
            f"same expression: a deployment that changed the fallback in one "
            f"place and not the other gets a database the application cannot "
            f"authenticate against."
        )


def test_every_database_url_hosts_a_literal_not_a_template() -> None:
    """The host is not templated, and that is deliberate.

    The password is templated because it is the operator's to choose. The
    host is not, because it names the ``postgres`` service on the Compose
    network -- and a host ``.env``'s ``localhost:5432`` is the operator's own
    machine, not this container. Templating the host would let a perfectly
    valid host value silently break every container in the stack, so the
    compose topology decides the host and only the password is read from the
    environment.
    """
    urls = _database_urls()
    assert urls, (
        "no service in docker-compose.yml declares a DATABASE_URL, so there is no host to check."
    )
    for service, url in sorted(urls.items()):
        authority = _authority_of(url)
        assert "$" not in authority, (
            f"the DATABASE_URL of the {service!r} service has a templated host "
            f"({authority!r}); the URL is {url!r}. The host names the postgres "
            f"service on the Compose network and must stay literal: an "
            f"operator's .env holds `localhost:5432`, which is their machine "
            f"and not this container, and templating the host would let that "
            f"value silently break every service. Only the password is "
            f"templated."
        )


def test_every_database_url_hosts_the_postgres_service_on_the_network() -> None:
    """The literal host names a service this compose file declares.

    The half of the host rule that a "no ``$``" check cannot see: a host
    written as a literal is not automatically the right literal. ``localhost``
    would pass the templating check and still break the stack, because
    ``localhost`` inside a container is that container. Naming a service
    declared in the same file is the property that actually holds.
    """
    services = _service_blocks()
    urls = _database_urls()
    assert urls, (
        "no service in docker-compose.yml declares a DATABASE_URL, so there is no host to check."
    )
    for service, url in sorted(urls.items()):
        host = _host_of(url)
        assert host in services, (
            f"the DATABASE_URL of the {service!r} service names the host "
            f"{host!r}, which is not a service docker-compose.yml declares "
            f"(it declares {sorted(services)}). The host must be the postgres "
            f"service on the Compose network; `localhost` inside a container is "
            f"that container, not the database."
        )
