"""The published host ports are genuinely configurable, end to end.

## Why this file exists

``e8e7f6a`` made the three published ports configurable
(``OPSPILOT_API_HOST_PORT``, ``OPSPILOT_WEB_HOST_PORT``,
``OPSPILOT_PG_HOST_PORT``, each templated as
``127.0.0.1:${OPSPILOT_*_HOST_PORT:-default}:<container port>``) because the
host ports 8000/3000/5432 are frequently taken by other stacks on a developer
machine. A second stranger pass verified by hand that
``OPSPILOT_API_HOST_PORT=18000`` works. Nothing in the repository guards
that: ``docker-compose.yml`` is data, and the unit guards over it
(``test_compose_settings_are_wired.py``) read *text*. They prove the template
is spelled correctly; they cannot prove that Compose actually publishes on
the substituted port and that the stack is reachable there. This file is that
proof: it stands the whole stack up on non-default high host ports and
asserts reachability on exactly those ports.

## What is asserted

* The stack comes up (``postgres``, ``migrate``, ``api``, ``worker``,
  ``web``) under a **unique project name** so it cannot collide with an
  already-running ``opspilot`` project on the same daemon.
* ``postgres`` and ``api`` report ``healthy`` (both carry compose
  healthchecks); ``web`` reports running (it deliberately has no healthcheck
  to probe -- see the worker's comment in ``docker-compose.yml``);
  ``migrate`` has run once and exited.
* ``GET /health`` answers 200 on the *overridden* API host port and the
  dashboard answers 200 on the *overridden* web host port.
* No container of this project publishes on the **default** ports
  (8000/3000/5432) -- the override displaced them; it did not add a second
  publishing. The check is scoped to this project's containers, because an
  unrelated process may legitimately hold a default port.
* Teardown removes every container, network and named volume the project
  created; nothing of this project is left.

## Skip discipline (ADR-0004)

Without a working Docker daemon this test cannot run, and that is the normal
outcome on a developer machine without Docker. It skips -- naming what is
missing -- rather than failing, the same posture
``tests/integration/test_worker_claim_postgres.py`` takes for a missing
PostgreSQL. Where Docker exists the assertions run unchanged.

## Port selection and contention

The high ports are **probed for availability, not fixed**: a machine may
already have something on 18000 (the machine this was written on does), and a
fixed choice would flake exactly where the test is most needed. The probe is
a bind attempt on ``127.0.0.1`` -- the same interface compose binds --
without ``SO_REUSEADDR``, because on Windows that flag would let the probe
succeed against a port that is already listening.

A probe cannot be airtight. Between "this port looks free" and "compose
binds it", another process -- another pytest run of this very test, a stray
container -- can take the port, and a just-closed probe socket can leave the
port briefly unavailable on Windows. Holding the probe open does not help:
compose could not bind the port the test itself holds. So each attempt uses
its **own project name** (test process id + attempt number, after sweeping
away any leftover project of an earlier run) and treats a compose bind
failure that names one of its chosen ports as "this candidate lost the
race": tear the attempt down and **retry with the next candidate**. Only
when every candidate has lost does the test fail, carrying the daemon's
last error. A failure that does not name a chosen port is a real failure
(a build error, a bad env value) and is reported immediately.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import urllib.error
import urllib.request

import pytest

#: The container-internal ports are fixed by the Dockerfiles; only the host
#: side moves. These defaults are what an override must displace.
_DEFAULT_HOST_PORTS = {"api": 8000, "web": 3000, "postgres": 5432}
_CONTAINER_PORTS = {"api": "8000", "web": "3000", "postgres": "5432"}

#: Candidate high host ports, in preference order. 18000/13000 are the pair
#: the manual verification used; 15432 keeps PostgreSQL recognisably
#: "5432-ish". Four candidates per service: one for the first attempt and
#: three spares for the contention retries below.
_CANDIDATE_PORTS = {
    "api": (18000, 18001, 28000, 28001),
    "web": (13000, 13001, 23000, 23001),
    "postgres": (15432, 15433, 25432, 25433),
}

#: Prefix of this test's compose projects. Each attempt appends the test
#: process id and the attempt number; anything sharing the prefix belongs to
#: a run of this test and is fair game for the pre-run sweep.
_PROJECT_PREFIX = "opspilot-port-verify"

#: How long `up --wait` may take. The first run on a cold cache builds four
#: images (uv sync, a Node build), so this is generous on purpose.
_WAIT_TIMEOUT = "600"


# ---------------------------------------------------------------------------
# environment probes -- each skip names what is missing
# ---------------------------------------------------------------------------


def _docker_unavailable_reason() -> str | None:
    """``None`` when Docker + Compose are usable, else why not."""
    try:
        # S603: the argv is a fixed literal -- no test or caller input
        # reaches this call. S607: "docker" is a fixed argv element, not a
        # PATH lookup on user input.
        probe = subprocess.run(
            ["docker", "compose", "version"],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return "docker CLI not found on PATH; install Docker to run this test (ADR-0004 posture)"
    except subprocess.TimeoutExpired:
        return "docker compose version timed out; the daemon may be hung"
    if probe.returncode != 0:
        return f"docker compose is unavailable: {(probe.stderr or probe.stdout).strip()}"
    # Same fixed-argv reasoning as the probe above.
    info = subprocess.run(
        ["docker", "info"],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if info.returncode != 0:
        return "docker daemon is not reachable (is Docker Desktop running?)"
    return None


def _port_is_free(port: int) -> bool:
    """True when 127.0.0.1:<port> can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _pick_free_port(service: str, banned: set[int]) -> int:
    """Choose an unused, not-yet-tried high host port for ``service``."""
    unusable: list[int] = []
    for candidate in _CANDIDATE_PORTS[service]:
        if candidate in banned or not _port_is_free(candidate):
            unusable.append(candidate)
            continue
        return candidate
    pytest.skip(
        f"no free high port for {service} among {_CANDIDATE_PORTS[service]} "
        f"(unusable now: {unusable}); free one to run this test"
    )


def _bind_conflict(stderr: str, ports: dict[str, int]) -> bool:
    """True when compose failed because one of ``ports`` could not be bound.

    The daemon phrases this several ways depending on where the bind happens
    ("port is already allocated", "ports are not available", the raw
    "Only one usage of each socket address" from the OS). Requiring one of
    those phrases *and* one of the chosen port numbers keeps a genuine
    failure (a build error, a bad env value) from being misread as a lost
    port race and retried.
    """
    lowered = stderr.lower()
    named = any(str(port) in stderr for port in ports.values())
    return named and (
        "port is already allocated" in lowered
        or "ports are not available" in lowered
        or "only one usage" in lowered
    )


def _sweep_leftover_projects() -> None:
    """Tear down any compose project a previous run of this test left.

    Best effort: if the listing cannot be read, the per-attempt project
    names below still keep this run isolated.
    """
    try:
        # S607: fixed argv, fixed binary name.
        listing = subprocess.run(
            ["docker", "compose", "ls", "--all", "--format", "json"],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if listing.returncode != 0 or not listing.stdout.strip():
        return
    try:
        entries = json.loads(listing.stdout)
    except json.JSONDecodeError:
        return
    for entry in entries:
        name = str(entry.get("Name", ""))
        if not name.startswith(_PROJECT_PREFIX):
            continue
        try:
            subprocess.run(  # noqa: S603
                ["docker", "compose", "-p", name, "down", "-v", "-t", "1"],  # noqa: S607
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue


# ---------------------------------------------------------------------------
# reachability helpers
# ---------------------------------------------------------------------------


def _http_status(url: str, timeout: float = 10.0) -> int | None:
    """HTTP status of ``url``, or ``None`` when nothing answers."""
    try:
        # S310: the scheme is audited -- only the ``http://127.0.0.1:<port>``
        # URLs this file constructs reach here, and the port is one the test
        # just chose; nothing caller-supplied is opened.
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as error:  # a status is still a status
        return error.code
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# the test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_stack_serves_on_overridden_high_host_ports(
    tmp_path: pathlib.Path, repo_root: pathlib.Path
) -> None:
    """Ports set through the environment are the ports the stack answers on."""
    unavailable = _docker_unavailable_reason()
    if unavailable:
        pytest.skip(unavailable)
    _sweep_leftover_projects()

    # The overrides travel in a throwaway env file inside tmp_path -- the same
    # channel an operator's .env uses, which is the mechanism under test.
    # `--env-file` replaces the repository's own .env for interpolation, and
    # the three variables are scrubbed from the inherited process environment
    # so neither can quietly override the file being tested.
    process_env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "OPSPILOT_API_HOST_PORT",
            "OPSPILOT_WEB_HOST_PORT",
            "OPSPILOT_PG_HOST_PORT",
        }
    }

    def compose(
        project: str, env_file: pathlib.Path, *args: str, timeout: float = 180.0
    ) -> subprocess.CompletedProcess[str]:
        # S603: the argv is built from this file's own constants plus the
        # subcommand literals at each call site; ``args`` carries only fixed
        # strings like "up"/"ps" defined below. S607: "docker" is a fixed
        # argv element, not a PATH lookup on user input.
        return subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "compose",
                "-p",
                project,
                "--env-file",
                str(env_file),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(repo_root),
            env=process_env,
        )

    def write_env_file(ports: dict[str, int], attempt: int) -> pathlib.Path:
        env_file = tmp_path / f"port-override-{attempt}.env"
        env_file.write_text(
            "\n".join(
                [
                    "OPSPILOT_OPERATOR_TOKEN=port-verify-token",
                    f"OPSPILOT_API_HOST_PORT={ports['api']}",
                    f"OPSPILOT_WEB_HOST_PORT={ports['web']}",
                    f"OPSPILOT_PG_HOST_PORT={ports['postgres']}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return env_file

    started: subprocess.CompletedProcess[str] | None = None
    chosen: dict[str, int] = {}
    project = _PROJECT_PREFIX
    env_file = tmp_path / "port-override.env"
    last_failure = ""
    banned: set[int] = set()
    attempts = len(next(iter(_CANDIDATE_PORTS.values())))
    for attempt in range(attempts):
        # A fresh project per attempt: reusing one project after a failed up
        # leaves the daemon referencing containers the teardown removed.
        project = f"{_PROJECT_PREFIX}-{os.getpid()}-{attempt}"
        chosen = {service: _pick_free_port(service, banned) for service in _CANDIDATE_PORTS}
        banned.update(chosen.values())
        env_file = write_env_file(chosen, attempt)
        started = compose(
            project,
            env_file,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            _WAIT_TIMEOUT,
            timeout=float(_WAIT_TIMEOUT) + 120.0,
        )
        if started.returncode == 0:
            break
        last_failure = f"stdout: {started.stdout}\nstderr: {started.stderr}"
        lost_race = _bind_conflict(started.stderr, chosen)
        # Clear this attempt's half-started project before retrying.
        compose(project, env_file, "down", "-v", "-t", "1", timeout=180.0)
        if not lost_race:
            break
    assert started is not None and started.returncode == 0, (
        f"docker compose up failed with the overridden ports {chosen}:\n{last_failure}"
    )

    def teardown() -> None:
        # down -v: containers, networks and the named volumes this project
        # created. The data here is throwaway; -t 1 keeps the shutdown quick.
        compose(project, env_file, "down", "-v", "-t", "1", timeout=180.0)

    try:
        # --wait returns when healthy services are healthy and one-shot
        # services exited 0; assert the states explicitly so a failure names
        # the service rather than "up timed out". web has no healthcheck by
        # design, so running is its ready state. `--all` is required to see
        # `migrate`, which is meant to have exited; plain `ps` hides it.
        ps = compose(project, env_file, "ps", "--all", "--format", "json")
        assert ps.returncode == 0, ps.stderr
        entries: list[dict[str, object]] = []
        payload = ps.stdout.strip()
        if payload.startswith("["):
            entries = json.loads(payload)
        else:
            entries = [json.loads(line) for line in payload.splitlines() if line.strip()]
        states: dict[str, tuple[str, str]] = {}
        for entry in entries:
            states[str(entry.get("Service", ""))] = (
                str(entry.get("State", "")),
                str(entry.get("Health", "")),
            )
        for service in ("postgres", "api"):
            state, health = states.get(service, ("missing", "missing"))
            assert state == "running" and health == "healthy", (
                f"{service} is not healthy: state={state!r} health={health!r}; all states: {states}"
            )
        web_state, _ = states.get("web", ("missing", ""))
        assert web_state == "running", (
            f"web is not running: state={web_state!r}; all states: {states}"
        )
        migrate_state = states.get("migrate", ("missing", ""))[0]
        assert migrate_state == "exited", (
            f"migrate should have run once and exited, state={migrate_state!r}"
        )

        # The overridden endpoints answer.
        assert _http_status(f"http://127.0.0.1:{chosen['api']}/health") == 200, (
            f"GET /health did not answer 200 on the overridden API port {chosen['api']}"
        )
        assert _http_status(f"http://127.0.0.1:{chosen['web']}/") == 200, (
            f"the dashboard did not answer 200 on the overridden web port {chosen['web']}"
        )

        # No container of THIS project publishes on a default port. Scoped to
        # the project on purpose: another process (or the developer's own
        # stack) may legitimately hold 8000/3000/5432; what must not happen is
        # this project holding one despite the override.
        ported = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Ports}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert ported.returncode == 0, ported.stderr
        published_text = " ".join(ported.stdout.split())
        for service, default_port in _DEFAULT_HOST_PORTS.items():
            assert f"127.0.0.1:{chosen[service]}" in published_text, (
                f"{service} is not published on the overridden port "
                f"{chosen[service]}; published: {published_text!r}"
            )
            assert f":{default_port}->" not in published_text, (
                f"{service} is still published on its default host port "
                f"{default_port} despite the override; published: {published_text!r}"
            )
    finally:
        teardown()

    # Nothing of this project survived teardown: no containers in any state,
    # and no live port mappings.
    remaining = compose(project, env_file, "ps", "-a", "--format", "json")
    assert remaining.returncode == 0, remaining.stderr
    leftover = [line for line in remaining.stdout.splitlines() if line.strip()]
    assert not leftover, f"containers survived teardown: {leftover}"

    for service, container_port in _CONTAINER_PORTS.items():
        mapping = compose(project, env_file, "port", service, container_port)
        assert mapping.returncode != 0 or not mapping.stdout.strip(), (
            f"{service} still has a port mapping after teardown: {mapping.stdout.strip()!r}"
        )
