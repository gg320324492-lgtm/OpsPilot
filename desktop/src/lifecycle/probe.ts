/**
 * Waiting for a thing to be up — and refusing to wait for the wrong thing.
 *
 * ## Why a poller and not a sleep
 *
 * `stack.ts`'s module docstring records what a fixed sleep costs: on CI run
 * 38202887580 a run sat at `RECEIVED` for ninety seconds because nothing was
 * watching the worker, and the log that would have explained it was not
 * captured. A desktop app has the same exposure and a worse audience — nobody
 * is watching a CI log while a window sits blank. So readiness here is a poll on
 * `/ready`, never a constant, and the poll's clock is injected (`Clock` in
 * `types.ts`) so a test can exercise a five-minute deadline without spending
 * five minutes.
 *
 * ## Why the poller takes a liveness check
 *
 * `waitForHttp` also answers "not yet" from things that are not "not yet": a
 * connection refused because the child died, for instance. `stack.ts` solved the
 * same problem by handing the poller the `ChildProcess` it spawned, and the
 * comment there is worth repeating because the failure it prevents is quiet: a
 * readiness probe that can be satisfied by an *unrelated* listener is not a
 * readiness probe. Here the caller passes `isAlive`, which is the same idea
 * expressed as a predicate, so the poller stays independent of how the process
 * was started.
 */

import type { Clock, HttpProbe, PortProbe } from "./types";

import { StartupError } from "./errors";

/** The interval between readiness attempts. Matches the e2e harness's 500 ms. */
export const DEFAULT_POLL_INTERVAL_MS = 500;

export interface WaitOptions {
  readonly timeoutMs: number;
  readonly intervalMs?: number;
  /** `false` abandons the wait at once — the process being waited on has died. */
  readonly isAlive?: () => boolean;
  /** Called on every failed attempt, with the reason. */
  readonly onAttempt?: (reason: string) => void;
}

/**
 * Poll `url` until it answers 2xx or 3xx, or the deadline passes.
 *
 * `2xx`/`3xx` and not `200`, because a Next.js dashboard may answer a redirect
 * (a locale or a trailing-slash normalisation) while being perfectly ready to
 * render. It is not `any` status either: a 500 from the dashboard is a broken
 * dashboard, and waiting out the deadline on one would report "slow" where the
 * truth is "wrong".
 */
export async function waitForHttp(
  url: string,
  http: HttpProbe,
  clock: Clock,
  options: WaitOptions,
): Promise<void> {
  const interval = options.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const deadline = clock.now() + options.timeoutMs;
  let lastReason = "it was never asked";
  for (;;) {
    if (options.isAlive !== undefined && !options.isAlive()) {
      throw new StartupError({
        title: "A process stopped before it was ready",
        detail:
          `Waiting for ${url} was abandoned because the process this wait was watching had ` +
          "already exited. A process that dies at start-up is a different failure from one that " +
          "is merely slow, and reporting it as slow would send whoever reads the window looking " +
          "in the wrong place.",
        remedy:
          "Read the last output lines shown above: they are what that process said as it died. " +
          "Then start the app again.",
      });
    }
    try {
      const response = await http(url);
      if (response.status >= 200 && response.status < 400) {
        return;
      }
      lastReason = `${url} answered ${response.status}`;
    } catch (error) {
      lastReason = error instanceof Error ? error.message : String(error);
    }
    options.onAttempt?.(lastReason);
    if (clock.now() >= deadline) {
      throw new StartupError({
        title: "Something never became ready",
        detail: `${url} was still not ready after ${Math.round(options.timeoutMs / 1000)}s. Last answer: ${lastReason}`,
        remedy:
          "Quit and start the app again. If it repeats, bring the stack up by hand and watch " +
          "what it prints: `docker compose up -d --wait postgres migrate api worker`.",
      });
    }
    await clock.sleep(interval);
  }
}

/**
 * Is the API already answering `/ready`?
 *
 * `true` means a stack is up and serving — not merely that something holds the
 * port. `/ready` is the endpoint `docker-compose.yml` keys the api's own
 * healthcheck on, because `/health` answers from a process that is up but may
 * be pointed at an unmigrated database; asking the stricter question means
 * "already running" and "usable" are the same claim.
 *
 * Any failure — refused, timed out, a 503 — answers `false`. That is the safe
 * direction: the alternative is a stack this app believes is running, did not
 * start, and will therefore leave alone on quit.
 */
export async function isHttpReady(url: string, http: HttpProbe): Promise<boolean> {
  try {
    const response = await http(url);
    return response.status >= 200 && response.status < 400;
  } catch {
    return false;
  }
}

/**
 * Wait until nothing is listening on `port`, or the deadline passes.
 *
 * Teardown's contract is "the port is free again", not "a signal was sent" —
 * `stack.ts::stopDashboard` makes the same point at length, for the same
 * reason: a kill signal reaches the process before the kernel closes its
 * listener, and a start-up that begins in that window binds against a port
 * another server still holds.
 */
export async function waitForPortFree(
  port: number,
  portBusy: PortProbe,
  clock: Clock,
  timeoutMs: number,
  intervalMs = 250,
): Promise<boolean> {
  const deadline = clock.now() + timeoutMs;
  for (;;) {
    if (!(await portBusy(port))) {
      return true;
    }
    if (clock.now() >= deadline) {
      return false;
    }
    await clock.sleep(intervalMs);
  }
}

/**
 * Refuse to bind a port something already holds.
 *
 * The dashboard cannot adopt an existing listener even in principle: the
 * readiness probe would then be answering for a server this app does not own,
 * `stop()` would kill a process it never started, and the operator would be
 * shown a dashboard pointing at a backend that may not exist. Every one of
 * those is worse than a refusal that names the port and says what to do.
 */
export async function assertPortFree(
  port: number,
  portBusy: PortProbe,
  subject: string,
): Promise<void> {
  if (!(await portBusy(port))) {
    return;
  }
  throw new StartupError({
    title: `Something is already listening on port ${port}`,
    detail:
      `${subject} needs port ${port}, and something else already holds it. Continuing would mean ` +
      "showing a dashboard this app did not start and cannot vouch for, and quitting would stop " +
      "a process that was already running before the app opened.",
    remedy:
      `Close whatever is using port ${port} — a \`next dev\`, a browser walkthrough left over from ` +
      `\`npm run e2e\`, or an earlier OpsPilot window — and start the app again. To run it on ` +
      "another port, set OPSPILOT_DESKTOP_PORT.",
  });
}