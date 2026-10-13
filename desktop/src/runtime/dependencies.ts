/**
 * Everything real, in one place: the filesystem, the sockets, the clock, and
 * the paths the repository actually lives at.
 *
 * ## The one thing this module is allowed to know
 *
 * Where the repository is. `web/e2e/stack.ts::findRepoRoot` walks up from the
 * working directory looking for `docker-compose.yml`, and this does the same,
 * because that is the only definition of "the repository root" this repository
 * has and a second one would be a second thing to keep correct. Everything
 * else about the layout — the `web/` directory, the standalone server — is
 * derived from that one answer.
 *
 * ## Why the ports are what they are
 *
 * The dashboard binds `3210`, which is neither of the two ports already in use
 * in this repository: `3000` is what `docker-compose.yml` publishes for its
 * `web` service, and `3100` is the host-run dashboard the e2e suite spawns
 * (`dashboard-port.ts` explains at length that those two used to collide and
 * made `npm run e2e` fail deterministically on Windows). A third server that
 * took either of them would re-create that failure for anyone who runs the
 * desktop app and the walkthrough on the same machine — which is every machine
 * that develops this project.
 */

import { cpSync, existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import path from "node:path";

import type { Clock, HttpProbe, PortProbe, SessionDeps } from "../lifecycle/types";

import { nodeCommandRunner } from "./node-runner";

/** The nearest ancestor of the working directory holding `docker-compose.yml`. */
export function findRepoRoot(): string {
  let dir = process.cwd();
  for (;;) {
    if (existsSync(path.join(dir, "docker-compose.yml"))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error(
        "No docker-compose.yml was found in this directory or any parent of it, so there is " +
          "no stack for this app to start. Start the app from inside the repository.",
      );
    }
    dir = parent;
  }
}

/**
 * The loopback families a server may have bound.
 *
 * Both are probed, and the reason is `dashboard-port.ts`'s: the CI failure that
 * produced that file was a server bound to `::`, which a probe of `127.0.0.1`
 * alone reports as free on some platforms. A pre-flight check that can be wrong
 * in the direction of "nothing is there" converts a loud failure into a silent
 * one.
 */
const LOOPBACK_HOSTS = ["127.0.0.1", "::1"] as const;

/** `true` if something accepted a connection on `port` at `host`. */
function connectOnce(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ port, host });
    // Settle exactly once: removing the listeners before destroying the socket
    // stops the error this teardown itself provokes from resolving a second
    // time.
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

export const isPortBusy: PortProbe = async (port) => {
  for (const host of LOOPBACK_HOSTS) {
    if (await connectOnce(port, host)) {
      return true;
    }
  }
  return false;
};

/**
 * Does this URL answer 2xx or 3xx?
 *
 * `redirect: "manual"` so the answer is the status the server actually sent
 * rather than the status of whatever it redirected to — the same choice
 * `stack.ts::waitForHttp` makes.
 */
export const httpProbe: HttpProbe = async (url) => {
  const response = await fetch(url, { redirect: "manual" });
  return { status: response.status };
};

export const systemClock: Clock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

/**
 * Read one variable out of a `.env`-shaped file.
 *
 * A deliberately small parser: `KEY=VALUE`, `#` comments, optional surrounding
 * quotes, and nothing else. `web/.env.local` and the repository's `.env` are
 * written by a human for this project; a full dotenv implementation would
 * handle syntax that is not there and introduce a dependency whose failure
 * modes are not ours.
 *
 * Exported because the *decision* about which of two files wins lives in
 * `lifecycle/token.ts` and is tested there — this is only the reading.
 */
export function readEnvValue(file: string, key: string): string | undefined {
  if (!existsSync(file)) {
    return undefined;
  }
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) {
      continue;
    }
    const separator = line.indexOf("=");
    if (separator === -1 || line.slice(0, separator).trim() !== key) {
      continue;
    }
    let value = line.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
      (value.startsWith("'") && value.endsWith("'") && value.length > 1)
    ) {
      value = value.slice(1, -1);
    }
    return value;
  }
  return undefined;
}

/**
 * The port the desktop dashboard binds.
 *
 * `OPSPILOT_DESKTOP_PORT`, parsed strictly — `dashboard-port.ts::parsePort`'s
 * argument applies unchanged: `Number("")` is `0` and `Number("3000abc")` is
 * `NaN`, both of which would sail through a falsy check and arrive at the
 * server as a port it cannot bind. An operator who mistypes this should be told
 * at start-up, not by an `EADDRINUSE` three minutes later.
 */
export function resolveDashboardPort(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === "") {
    return DEFAULT_DASHBOARD_PORT;
  }
  const port = Number(raw.trim());
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(
      `OPSPILOT_DESKTOP_PORT="${raw}" is not a TCP port between 1 and 65535. Set it to a free ` +
        `port, or unset it to use ${DEFAULT_DASHBOARD_PORT}.`,
    );
  }
  return port;
}

/** See the module docstring: neither 3000 nor 3100 is free in this repository. */
export const DEFAULT_DASHBOARD_PORT = 3210;

/** The API's published URL. Loopback, matching the ports `docker-compose.yml` binds. */
export const DEFAULT_API_URL = "http://127.0.0.1:8000";

/** The default `docker` executable, overridable for a non-standard install. */
export const DEFAULT_DOCKER = "docker";

export interface BuildDependenciesOptions {
  /** Overridable so a test — or an operator with an unusual install — can say. */
  readonly repoRoot?: string;
  readonly env?: NodeJS.ProcessEnv;
  /** The Node runtime used to serve the dashboard. Defaults to Electron's own. */
  readonly node?: string;
  readonly docker?: string;
  readonly apiUrl?: string;
  readonly dashboardPort?: number;
}

/**
 * Assemble the real `SessionDeps`.
 *
 * The CORS allowance is the one piece of derivation here worth reading twice.
 * The API installs a strict origin allowlist from `OPSPILOT_CORS_ORIGINS`,
 * defaulting to `http://localhost:3000` — the address `npm run dev` uses. This
 * window is served from `http://127.0.0.1:3210`, and the dashboard's *reads* go
 * straight from the browser to `http://127.0.0.1:8000`, which is cross-origin,
 * so without an allowance for this window's own origin every read in the app
 * would be refused and the dashboard would render an error where its data
 * should be.
 *
 * The allowance is appended, never substituted, and it is two loopback origins
 * on the one port this app chose — strictly narrower than the LAN surface that
 * P2-A1 removed, and unreachable without the loopback binding above. It is the
 * desktop build naming its own window to the API, which is the same
 * relationship `localhost:3000` has to `npm run dev`.
 */
export function buildDependencies(options: BuildDependenciesOptions = {}): SessionDeps {
  const repoRoot = options.repoRoot ?? findRepoRoot();
  const webDir = path.join(repoRoot, "web");
  const apiUrl = options.apiUrl ?? DEFAULT_API_URL;
  const dashboardPort = options.dashboardPort ?? resolveDashboardPort(process.env.OPSPILOT_DESKTOP_PORT);
  const docker = options.docker ?? DEFAULT_DOCKER;
  const inherited = options.env ?? process.env;

  const windowOrigin = `http://127.0.0.1:${dashboardPort}`;
  const env: NodeJS.ProcessEnv = {
    ...inherited,
    OPSPILOT_CORS_ORIGINS: withCorsOrigin(inherited.OPSPILOT_CORS_ORIGINS, windowOrigin),
  };

  return {
    runner: nodeCommandRunner,
    clock: systemClock,
    http: httpProbe,
    portBusy: isPortBusy,
    exists: existsSync,
    repoRoot,
    webDir,
    apiUrl,
    dashboardPort,
    env,
    docker,
    // `ELECTRON_RUN_AS_NODE` makes the Electron binary behave as a plain Node
    // runtime, which is how a packaged app gets the runtime the standalone
    // server needs without shipping a second copy of Node. It is set by
    // `dashboardEnv` in the lifecycle, because it belongs to "how the dashboard
    // is served" rather than to "what the shell is".
    node: options.node ?? process.execPath,
    copyDir: (from, to) => {
      if (existsSync(from)) {
        cpSync(from, to, { recursive: true });
      }
    },
    backendReadyTimeoutMs: 300_000,
    dashboardReadyTimeoutMs: 120_000,
    teardownTimeoutMs: 60_000,
  };
}

/**
 * Add `origin` to a comma-separated CORS list without dropping what was there.
 *
 * An unset or blank value is treated as "unset" rather than as a list of one
 * empty string, because `OPSPILOT_CORS_ORIGINS=` is a plausible thing to find in
 * a copied `.env` and `["", origin]` is not a list the API can serve.
 */
export function withCorsOrigin(existing: string | undefined, origin: string): string {
  const present = (existing ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry !== "");
  return present.includes(origin) ? present.join(",") : [...present, origin].join(",");
}