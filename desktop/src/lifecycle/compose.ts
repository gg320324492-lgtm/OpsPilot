/**
 * `docker compose`, as command lines — and the three ways Docker can be absent.
 *
 * ## The four services, and the one that is deliberately missing
 *
 * `postgres migrate api worker`, exactly the subset `web/e2e/stack.ts` brings
 * up for the browser walkthrough, and for the same reason: those are the
 * services the dashboard needs to be a dashboard. The compose file also
 * declares a `web` service, and this app does **not** start it — the window
 * serves the dashboard itself, from the standalone build on the host, so a
 * second dashboard container would be a second server competing for the same
 * port with nothing to show for it.
 *
 * ## Two deliberate divergences from `web/e2e/stack.ts`
 *
 * The e2e harness is the reference for *how* to drive Compose here — same
 * service list, same `--wait --wait-timeout` shape, same `down` at the end — but
 * two of its flags are answers to questions an ephemeral CI runner asks and a
 * desktop app must not copy. Both are called out at the functions that use them.
 */

import type { CommandOutcome, DockerProblem, RunOptions, SessionDeps } from "./types";

import { composePluginMissing, dockerNotInstalled, dockerNotRunning, excerpt } from "./errors";

/**
 * The services this shell starts. Closed, and read in one place, so "what
 * does the desktop app run" has a single answer a test can assert against.
 */
export const COMPOSE_SERVICES = ["postgres", "migrate", "api", "worker"] as const;

/**
 * How long `compose up --wait` may take before Compose gives up.
 *
 * The same 300 seconds the e2e harness allows, for the same reason: this is a
 * cold `docker compose up` that may include image pulls on a machine that has
 * never run the stack, and a deadline shorter than the slowest honest pull
 * turns a slow start into a reported failure.
 */
export const COMPOSE_UP_TIMEOUT_SECONDS = 300;

/**
 * `docker compose up -d --wait …` for the four services.
 *
 * **No `--build`, unlike `stack.ts`.** The e2e harness builds because it is
 * testing the built images — a stale image would mean the fix under test was
 * not the code running. A desktop launch is not that: it should bring up the
 * images it already has, on a schedule an operator can predict. Rebuilding
 * Python and Next images on every launch would turn a ten-second start into an
 * open-ended one and make "the app started" untestable as a claim.
 */
export function composeUpArgs(
  services: readonly string[] = COMPOSE_SERVICES,
  waitTimeoutSeconds: number = COMPOSE_UP_TIMEOUT_SECONDS,
): string[] {
  return [
    "compose",
    "up",
    "-d",
    "--wait",
    "--wait-timeout",
    String(waitTimeoutSeconds),
    ...services,
  ];
}

/**
 * `docker compose down` for the same project.
 *
 * **No `-v`, and that is the whole point of the difference from `stack.ts`.**
 *
 * `compose down -v` removes the named volumes, which for a CI run is correct —
 * the next run wants an empty database, and the e2e harness says so where it
 * does it. For a desktop app it would mean that quitting the window deletes the
 * operator's runs, their approvals and their refund ledger, and reopening
 * starts from nothing. So this stops and removes the containers and leaves the
 * volumes, which is what "quit the app" should mean for something whose data is
 * the point.
 *
 * `--remove-orphans` is kept: a container from an earlier, differently-shaped
 * run of this same project is not something the operator asked to keep.
 */
export function composeDownArgs(options: { readonly removeVolumes?: boolean } = {}): string[] {
  return ["compose", "down", ...(options.removeVolumes === true ? ["-v"] : []), "--remove-orphans"];
}

/** `docker compose ps -a`, for the teardown's diagnostics. */
export function composePsArgs(): string[] {
  return ["compose", "ps", "-a"];
}

/**
 * How long a `docker info` may hang before it is called unreachable.
 *
 * Docker Desktop holds its CLI lock while it is starting, and a wedged engine
 * can leave `docker info` blocked rather than failing. Without a deadline the
 * operator's first sight of this app would be a window that never resolves,
 * which is the failure mode D1 exists to remove.
 */
export const DOCKER_PROBE_TIMEOUT_MS = 20_000;

/**
 * Decide whether Docker can be used at all, and say precisely what is missing
 * if it cannot.
 *
 * Returns `null` when Docker is usable. The three problems are kept apart
 * because they have three different remedies, and "Docker isn't working" is
 * none of them:
 *
 *   * **not installed** — `docker` is not on PATH (`ENOENT`). Install it.
 *   * **not running** — the CLI answered and the daemon did not. Start it.
 *   * **no Compose plugin** — the daemon answered and `docker compose` did not.
 *     Add the plugin.
 *
 * `docker info` rather than `docker --version` is deliberate and is
 * `stack.ts`'s reason as much as this function's: a CLI on PATH says nothing
 * about whether a daemon is reachable, and the bound-but-dead state turns every
 * later Compose call into a timeout rather than an error.
 *
 * Nothing is started and nothing is retried while this runs. The checks are
 * cheap, they fail fast, and a start-up path that keeps trying against a
 * missing prerequisite is the "silent failure with a spinner" the acceptance
 * forbids.
 */
export async function assessDocker(deps: SessionDeps): Promise<DockerProblem | null> {
  const options: RunOptions = { cwd: deps.repoRoot, env: deps.env, timeoutMs: DOCKER_PROBE_TIMEOUT_MS };

  const info = await deps.runner.run(deps.docker, ["info"], options);
  if (info.spawnError !== undefined) {
    return dockerNotInstalled(deps.docker, info.spawnError.code);
  }
  if (!info.ok) {
    return dockerNotRunning(info.stderr === "" ? info.stdout : info.stderr);
  }

  const compose = await deps.runner.run(deps.docker, ["compose", "version"], options);
  if (compose.spawnError !== undefined) {
    return dockerNotInstalled(deps.docker, compose.spawnError.code);
  }
  if (!compose.ok) {
    return composePluginMissing(compose.stderr === "" ? compose.stdout : compose.stderr);
  }

  return null;
}

/**
 * The one-line account of a command that failed, for a message and a log.
 *
 * Compose's own words are the diagnosis — which service failed its health
 * check, which port was taken — so this keeps them rather than summarising.
 * `stack.ts` reaches the same conclusion by making the caller read the compose
 * output directly; here the output is also going to a window, so it is quoted
 * once and put in the message.
 */
export function describeOutcome(action: string, outcome: CommandOutcome): string {
  if (outcome.timedOut) {
    return `${action} did not finish within its deadline. Last output:\n${excerpt(outcome.stdout + outcome.stderr)}`;
  }
  if (outcome.spawnError !== undefined) {
    return `${action} could not be started: ${outcome.spawnError.message}`;
  }
  return (
    `${action} exited with code ${String(outcome.status)}.\n` +
    `Last output:\n${excerpt(outcome.stdout + outcome.stderr)}`
  );
}