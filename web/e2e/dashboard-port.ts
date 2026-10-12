/**
 * Where the host-run dashboard listens: ONE definition, read by both the
 * Playwright runner (`playwright.config.ts`'s `use.baseURL`) and the worker that
 * actually spawns the server (`stack.ts`).
 *
 * WHY A SHARED MODULE AND NOT A LITERAL IN EACH FILE
 * --------------------------------------------------
 * The runner and the worker are different processes, so they cannot see each
 * other's state; the only thing they can agree on is a value both compute the
 * same way. A literal `3000` written in two places is a second source of truth
 * wearing a constant's clothes: the day one of them is changed to make room for
 * a developer's `npm run dev`, the dashboard serves on one port while the browser
 * is told to look on the other, and the walkthrough fails at `page.goto` with a
 * connection error that names neither cause. Deriving both from one function is
 * what makes `E2E_PORT` a real knob rather than a suggestion.
 *
 * `E2E_BASE_URL` wins over `E2E_PORT`, because a full URL is the more specific
 * instruction -- and when it is given, the PORT handed to the server is read back
 * *out of it*, so the two halves cannot disagree. That is the whole point: a
 * base URL and the port the server binds are the same fact stated twice, and
 * resolving one from the other is what stops them drifting.
 *
 * WHY THE DEFAULTS DIFFER, AND WHY THAT IS NOT COSMETIC
 * -----------------------------------------------------
 * There are TWO dashboards in this suite and they are two different servers:
 * the host-run one `stack.ts` spawns as `node .next/standalone/server.js`, and
 * the containerised compose `web` service `web-service.spec.ts` starts. Both
 * used to default to 3000, and that made `npm run e2e` fail deterministically
 * on Windows (verified): Playwright runs the files in order, so the host
 * dashboard takes 3000 first, teardown kills it, and then the compose service
 * tries to publish the same 3000. Docker Desktop's port forwarder will not take
 * a port a host process has just held -- `startContainerisedWebOrSkip`'s probe
 * then fails for its full timeout on a container compose reported as `Healthy`,
 * which names nothing useful.
 *
 * The containerised dashboard keeps 3000 because that is what
 * `docker-compose.yml` publishes and what an operator already has open. The
 * host-run one moves to 3100 so the two never contend, in either order, on any
 * platform. `E2E_PORT` moves it again for a machine that needs to.
 *
 * The other candidate -- picking a free port at random per run -- was rejected
 * because the runner and the worker are separate processes and would each choose
 * a different one, so every relative `page.goto` would need a per-file
 * `test.use` override to reach the port the worker actually started. That is a
 * lot of machinery to guard against a case two fixed defaults close outright.
 */

import { createConnection } from "node:net";

/**
 * The host-run dashboard's port. Deliberately NOT 3000 -- see above.
 *
 * 3100 is as arbitrary as 3000 was. What matters is that it differs from the
 * compose `web` service's published port, so that both can be up in the same
 * session without one of them starving the other.
 */
const DEFAULT_DASHBOARD_PORT = 3100;

/** The loopback families a server may have bound. Both are probed, not guessed. */
const LOOPBACK_HOSTS = ["127.0.0.1", "::1"] as const;

/**
 * Parse a port number, rejecting anything that is not one.
 *
 * `Number()` alone is the wrong tool: `Number("")` is `0`, `Number(" 3000 ")` is
 * `3000`, and `Number("3000abc")` is `NaN` -- all of which would sail through a
 * falsy check and reach the server as a `PORT` it cannot bind. An operator who
 * mistypes the variable should be told, at start-up, not by a `EADDRINUSE` three
 * minutes later.
 */
function parsePort(raw: string, variableName: string): number {
  const port = Number(raw.trim());
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(
      `${variableName}="${raw}" is not a TCP port between 1 and 65535. Set it to a ` +
        `free port (or unset it to use ${DEFAULT_DASHBOARD_PORT}).`,
    );
  }
  return port;
}

/**
 * The port, resolved once.
 *
 * `E2E_BASE_URL` is consulted first and parsed with `URL`, so a malformed value
 * fails here -- loudly, at import time, naming itself -- rather than becoming a
 * port of `NaN` that only surfaces when a browser cannot connect.
 */
export const DASHBOARD_PORT: number = (() => {
  const baseUrl = process.env.E2E_BASE_URL;
  if (baseUrl !== undefined && baseUrl !== "") {
    const parsed = new URL(baseUrl);
    return parsed.port === "" ? DEFAULT_DASHBOARD_PORT : parsePort(parsed.port, "E2E_BASE_URL");
  }
  const pinned = process.env.E2E_PORT;
  if (pinned !== undefined && pinned.trim() !== "") {
    return parsePort(pinned, "E2E_PORT");
  }
  return DEFAULT_DASHBOARD_PORT;
})();

/** The dashboard's URL, as the browser must reach it. */
export const DASHBOARD_URL: string =
  process.env.E2E_BASE_URL !== undefined && process.env.E2E_BASE_URL !== ""
    ? process.env.E2E_BASE_URL
    : `http://localhost:${DASHBOARD_PORT}`;

/**
 * The TCP port a base URL names, or `fallback` when it names none.
 *
 * Used for the containerised dashboard, whose URL arrives already assembled as
 * `WEB_SERVICE_URL` and whose port is what Compose must publish -- so it has to
 * be read out of that URL rather than assumed, or the two could disagree in
 * exactly the way this module exists to prevent.
 */
export function portOfUrl(url: string, fallback: number): number {
  const parsed = new URL(url);
  return parsed.port === "" ? fallback : parsePort(parsed.port, "E2E_WEB_SERVICE_URL");
}

/**
 * Is something already listening on `port`?
 *
 * Both loopback families are probed because the two do not substitute for each
 * other. The CI failure that motivated this file is a server bound to `::`
 * (Node's `listen(port)` default, and the `address: '::'` in the EADDRINUSE
 * report), and a probe of `127.0.0.1` alone answers "free" against some
 * platforms while a process is very much holding the socket. A readiness check
 * that can be wrong in the direction of "nothing there" is worse than no check,
 * because it converts a loud failure into a silent one.
 */
export async function isPortBusy(port: number): Promise<boolean> {
  for (const host of LOOPBACK_HOSTS) {
    if (await connectOnce(port, host)) {
      return true;
    }
  }
  return false;
}

/** One connection attempt; `true` if something accepted it. */
function connectOnce(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ port, host });
    // Settle exactly once: `removeAllListeners` before `destroy` stops the
    // error the teardown itself provokes from resolving a second time.
    const settle = (busy: boolean): void => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(busy);
    };
    socket.setTimeout(1_000, () => settle(false));
    socket.once("connect", () => settle(true));
    socket.once("error", () => settle(false));
  });
}

/**
 * Wait until nothing is listening on `port`, or the deadline passes.
 *
 * Teardown's contract is not "a kill signal was sent" but "the port is free
 * again": a signal that a process has not yet acted on leaves a window in which
 * the next run's server dies with EADDRINUSE, which is the failure this exists
 * to prevent.
 *
 * @returns `true` if the port is free, `false` if it is still held.
 */
export async function waitForPortFree(port: number, timeoutMs = 15_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (!(await isPortBusy(port))) {
      return true;
    }
    if (Date.now() >= deadline) {
      return false;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}