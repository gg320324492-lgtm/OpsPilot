"""Start, reset and stop the disposable demo stack for the three browser demo paths.

Why this exists
---------------
``docs/phase3-plan.md`` §M2-04 asks for three browser demonstration paths
(approve / reject / model-failure), each with seed data and start/reset
instructions, and -- the hard constraint -- **never resetting by touching the
existing user-data volumes**. This script is the operator-facing half of that:
it brings the demo stack up on its OWN compose project, with its OWN volumes,
and `reset` destroys exactly those volumes and nothing else.

The compose project name is ``opspilot-demo-test`` by default. The desktop
application's stack uses ``opspilot`` and never runs this script, and the
browser e2e suite uses ``opspilot-e2e``; the names are disjoint on purpose, so
``down -v`` here cannot reach data either of them owns. The ports are disjoint
too (api 18000, dashboard 3101, postgres 15432), so the demo can run beside a
running desktop stack.

Seed data
---------
No seeding step is needed, and that is the point: the MCP servers seed
themselves from the committed ``mcp_servers/*/seed.json`` the first time they
touch their store on a fresh volume, so a `reset` (which removes the volumes)
reproduces the demo exactly. The fake model provider replays the committed
``duplicate_charge`` / ``already_refunded`` fixtures. Everything the three
paths show -- the duplicate charge TX-88219, invoice INV-2026-384, the refund
policies -- is committed data, not something this script creates.

What it does
------------
* ``up``      -- start postgres + migrate + api + worker + web, wait for
                readiness, reindex the knowledge base, and print where to click.
* ``reset``   -- `down -v` (removing THIS project's containers AND volumes) and
                then `up`, so the next run starts from the committed seed.
* ``down``    -- stop the stack and remove its volumes.
* ``status``  -- show what is running for this project.

The knowledge reindex is part of `up` because the API does not ingest at
start-up; a fresh Postgres holds zero chunks and the first run would abstain
instead of reaching the approval gate. The browser paths in
``docs/demo-paths.md`` click the same button themselves; this script does it
over the API so the stack is demo-ready the moment `up` returns.

Usage (from the repository root, with the project venv)::

    .venv/Scripts/python.exe scripts/demo_paths.py up
    .venv/Scripts/python.exe scripts/demo_paths.py reset
    .venv/Scripts/python.exe scripts/demo_paths.py down
    .venv/Scripts/python.exe scripts/demo_paths.py status

"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The demo stack's compose project. Disjoint from `opspilot` (the desktop
#: application) and `opspilot-e2e` (the browser e2e suite) by design -- see the
#: module docstring. Overridable, but an override that collides with either of
#: those names would put this script's `down -v` on someone else's volumes, so
#: the collision is refused rather than warned about.
DEFAULT_PROJECT = "opspilot-demo-test"
RESERVED_PROJECTS = ("opspilot", "opspilot-e2e")

#: The demo stack's published ports. Disjoint from the desktop defaults (8000 /
#: 3000 / 5432) and from the e2e suite's (8000 / 3100) for the same reason.
DEFAULT_API_PORT = 18000
DEFAULT_WEB_PORT = 3101
DEFAULT_PG_PORT = 15432

#: The token the stack starts with. This is a LOCAL DEMO credential, printed to
#: the operator's own terminal; it is not a secret and not committed anywhere.
DEMO_TOKEN = "demo-paths-disposable-token"

#: How long `up` waits for /ready before giving up (the migrate + api boot).
READY_TIMEOUT_SECONDS = 300.0


def _compose(
    project: str, args: list[str], *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one `docker compose` command against the demo project."""
    return subprocess.run(
        ["docker", "compose", "-p", project, *args],
        cwd=REPO_ROOT,
        capture_output=capture,
        text=True,
        timeout=900,
    )


class DemoConfigError(SystemExit):
    """The demo stack cannot run because of something the operator controls.

    ``SystemExit`` with a message, so ``python scripts/demo_paths.py`` prints
    the reason and exits non-zero without a traceback. The message is carried
    at the raise site because each of these faults is a one-off configuration
    value an operator changes, not a condition the code branches on -- the
    same reasoning ``docs/pyproject.toml`` records for ``WorkerConfigError``.
    """


def _check_project(project: str) -> None:
    """Refuse a project name that would put `down -v` on someone else's volumes."""
    if project in RESERVED_PROJECTS:
        raise DemoConfigError(
            f"project name {project!r} is reserved for a stack with real data; "
            f"the demo stack must use its own name (default: {DEFAULT_PROJECT!r})"
        )


def _wait_for_ready(api_port: int) -> None:
    """Block until the API's /ready answers 200, or fail with what it said last."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last = "never answered"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{api_port}/ready", timeout=5) as resp:
                if resp.status == 200:
                    return
                last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except OSError as exc:
            last = str(exc)
        time.sleep(1.0)
    raise DemoConfigError(
        f"the API did not become ready within {READY_TIMEOUT_SECONDS:.0f}s (last: {last}). "
        "See `docker compose -p <project> logs api` for the cause."
    )


def _reindex(api_port: int) -> None:
    """Reindex the knowledge base over the API, exactly as the dashboard's button does."""
    req = urllib.request.Request(
        f"http://localhost:{api_port}/api/knowledge/reindex", method="POST"
    )
    req.add_header("Authorization", f"Bearer {DEMO_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode())
    print(f"knowledge reindexed: {json.dumps(payload)[:200]}")


def _env(project: str, api_port: int, web_port: int, pg_port: int) -> dict[str, str]:
    """The environment every compose call runs with (imports .env when present)."""
    import os

    env = dict(os.environ)
    env.update(
        {
            "COMPOSE_PROJECT_NAME": project,
            "OPSPILOT_API_HOST_PORT": str(api_port),
            "OPSPILOT_WEB_HOST_PORT": str(web_port),
            "OPSPILOT_PG_HOST_PORT": str(pg_port),
            "OPSPILOT_OPERATOR_TOKEN": DEMO_TOKEN,
            "MODEL_PROVIDER": "fake",
            "OPSPILOT_FAKE_SCENARIO": "duplicate_charge",
            "EMBEDDING_PROVIDER": "local",
        }
    )
    return env


def _compose_with_env(
    project: str, args: list[str], env: dict[str, str], *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one `docker compose` command with the demo environment applied."""
    return subprocess.run(
        ["docker", "compose", "-p", project, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=capture,
        text=True,
        timeout=900,
    )


def cmd_up(project: str, api_port: int, web_port: int, pg_port: int) -> None:
    """Bring the demo stack up and make it demo-ready."""
    _check_project(project)
    env = _env(project, api_port, web_port, pg_port)

    # A previous run's containers and volumes would make this run start with a
    # database that already holds runs and an MCP store that already holds the
    # refund. Reset-first is what makes "the demo starts from the committed
    # seed" true every time; `down -v` here reaches only this project's
    # volumes, which is the property the reserved-name check protects.
    print(f"resetting project {project!r} (containers and volumes)...")
    down = _compose_with_env(project, ["down", "-v", "--remove-orphans"], env)
    if down.returncode != 0:
        print(down.stdout + down.stderr, file=sys.stderr)

    print("starting postgres, migrate, api, worker, web ...")
    up = _compose_with_env(
        project,
        [
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "300",
            "--build",
            "postgres",
            "migrate",
            "api",
            "worker",
            "web",
        ],
        env,
    )
    if up.returncode != 0:
        print(up.stdout + up.stderr, file=sys.stderr)
        raise DemoConfigError("the demo stack did not come up (see the output above).")

    _wait_for_ready(api_port)
    _reindex(api_port)

    print()
    print("the demo stack is ready:")
    print(f"  dashboard   http://localhost:{web_port}")
    print(f"  api         http://localhost:{api_port}")
    print(f"  project     {project}")
    print(f"  token       {DEMO_TOKEN}")
    print()
    print("walk the paths (docs/demo-paths.md):")
    print(f"  A approve        http://localhost:{web_port}/tickets")
    print(f"  B reject         http://localhost:{web_port}/tickets  (then Reject)")
    print("  C model failure  scripts/demo_paths.py inject-429 / restore")
    print()
    print("reset when done:  .venv/Scripts/python.exe scripts/demo_paths.py reset")
    print("or tear it down:  .venv/Scripts/python.exe scripts/demo_paths.py down")


def cmd_reset(project: str, api_port: int, web_port: int, pg_port: int) -> None:
    """Destroy this project's volumes and start again from the committed seed."""
    _check_project(project)
    cmd_up(project, api_port, web_port, pg_port)


def cmd_down(project: str) -> None:
    """Stop the demo stack and remove its volumes."""
    _check_project(project)
    env = _env(project, DEFAULT_API_PORT, DEFAULT_WEB_PORT, DEFAULT_PG_PORT)
    down = _compose_with_env(project, ["down", "-v", "--remove-orphans"], env)
    if down.returncode != 0:
        print(down.stdout + down.stderr, file=sys.stderr)
        raise DemoConfigError("the demo stack did not go down cleanly.")
    print(f"project {project!r}: containers and volumes removed.")


def cmd_status(project: str) -> None:
    """Show what is running for this project."""
    result = _compose(project, ["ps", "-a"], capture=True)
    print(result.stdout or "(no output -- the stack is not running)")
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def cmd_inject_429(project: str) -> None:
    """Start the throwaway 429 server for path C and print the worker overrides.

    The container is plain `docker compose run` (not a service), reachable from
    the worker by name on the project network. It answers every POST with a
    429 and an Anthropic-shaped error body, which the provider SDK turns into
    RateLimitError; the SDK is configured max_retries=0, so the first model
    call of a run fails it.
    """
    _check_project(project)
    name = f"{project}-429"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    program = "\n".join(
        [
            "import json",
            "from http.server import BaseHTTPRequestHandler, HTTPServer",
            "class H(BaseHTTPRequestHandler):",
            "    def do_POST(self):",
            "        body = json.dumps({'type':'error','error':{'type':'rate_limit_error',",
            "            'message':'demo injection: provider rate limited'}}).encode()",
            "        self.send_response(429)",
            "        self.send_header('content-type','application/json')",
            "        self.send_header('content-length',str(len(body)))",
            "        self.end_headers()",
            "        self.wfile.write(body)",
            "    def do_GET(self):",
            "        self.send_response(429); self.end_headers()",
            "    def log_message(self,*a): pass",
            "HTTPServer(('0.0.0.0',8000),H).serve_forever()",
        ]
    )
    started = _compose(
        project,
        ["run", "-d", "--no-deps", "--name", name, "api", "python", "-c", program],
        capture=True,
    )
    if started.returncode != 0:
        print(started.stdout + started.stderr, file=sys.stderr)
        raise DemoConfigError("the 429 injection container did not start.")
    print(f"429 server running as {name!r} on the {project!r} network.")
    print()
    print("recreate the worker with these overrides (path C):")
    print("  docker compose -p", project, "up -d --force-recreate worker \\")
    print("    -e MODEL_PROVIDER=anthropic \\")
    print("    -e ANTHROPIC_API_KEY=demo-throwaway-key \\")
    print(f"    -e ANTHROPIC_BASE_URL=http://{name}:8000")
    print()
    print("then submit a ticket at the dashboard and watch the run FAIL with")
    print("model_unavailable. Remove the server and restore the worker:")
    print("  docker rm -f", name)
    print("  docker compose -p", project, "up -d --force-recreate worker")


def main() -> None:
    """Parse argv and dispatch."""
    parser = argparse.ArgumentParser(
        description="Start/reset the disposable demo stack for the three browser demo paths."
    )
    parser.add_argument(
        "command",
        choices=["up", "reset", "down", "status", "inject-429"],
        help="up=reset+start+reindex; reset=down -v then up; down=remove volumes; "
        "status=show containers; inject-429=start the path-C 429 server",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="compose project name")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--pg-port", type=int, default=DEFAULT_PG_PORT)
    args = parser.parse_args()

    if args.command in ("up", "reset"):
        cmd_reset(args.project, args.api_port, args.web_port, args.pg_port)
    elif args.command == "down":
        cmd_down(args.project)
    elif args.command == "status":
        cmd_status(args.project)
    elif args.command == "inject-429":
        cmd_inject_429(args.project)


if __name__ == "__main__":
    main()
