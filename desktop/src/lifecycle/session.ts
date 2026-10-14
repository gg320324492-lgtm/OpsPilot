/**
 * Start the backend, start the dashboard, and own exactly what was started.
 *
 * ## The rule this file exists to enforce
 *
 * **Quit stops what this app started, and touches nothing else.** A developer
 * with the stack already up — a `next dev`, a walkthrough, a demo someone else
 * is watching — must not have it killed because they opened a window. So every
 * resource this app starts carries an `Ownership`, and `stop()` consults it
 * before doing anything at all:
 *
 *   * `"started-by-us"`  → `stop()` stops it.
 *   * `"already-running"` → `stop()` runs **no command whatsoever** and says
 *     why it did nothing.
 *
 * The distinction is decided once, at the top of `startBackend`, by asking the
 * API whether it is already serving `/ready`. Not by `docker compose ps`, and
 * not by "did our `up` command change anything": a stack brought up outside
 * this repository's compose project is still a stack this app did not start,
 * and `docker compose ps` would not see it, so a `ps`-based decision would
 * report "not running", start a second one, and fail to bind :8000.
 *
 * ## Why readiness is polled and never slept
 *
 * `stack.ts` records what a fixed sleep costs — CI run 38202887580 sat at
 * `RECEIVED` for ninety seconds with nobody watching the process that should
 * have moved it. Here the audience is an operator looking at a window, which is
 * worse: a blank window during a sleep is indistinguishable from a hung one.
 *
 * ## Why this file imports no Electron
 *
 * It is the separation that makes the lifecycle testable without launching a
 * window. Every side effect arrives through `SessionDeps`, every message leaves
 * through `emit`, and nothing here has an import of `electron`,
 * `node:child_process` or `node:fs`. `desktop/e2e/` drives all of it from a
 * table of answers.
 */

import {
  assessDocker,
  composeDownArgs,
  composeUpArgs,
  COMPOSE_SERVICES,
  describeOutcome,
} from "./compose";
import { StartupError, toUserFacing } from "./errors";
import { assertPortFree, isHttpReady, waitForHttp, waitForPortFree } from "./probe";
import type { LogSource, Ownership, Progress, SessionDeps, StartedProcess, TeardownTarget } from "./types";

/**
 * How a progress event leaves the lifecycle.
 *
 * Takes the event *without* its sequence number: `seq` belongs to the transport
 * that appends events to a history (`main.ts`'s `emit`, and the test
 * collector), not to the code deciding what happened. A producer that could set
 * `seq` would be able to set it wrongly, and a duplicate would render as a
 * duplicated line in the operator's log.
 */
export type Emit = (progress: Omit<Progress, "seq">) => void;

/** A backend that is up, and a claim on whether this app may stop it. */
export interface BackendHandle {
  readonly ownership: Ownership;
  readonly endpoint: string;
  /** Stop it if this app started it; do nothing, and say so, if it did not. */
  stop(): Promise<void>;
}

export interface DashboardHandle {
  readonly url: string;
  /** Always stops: the port was required to be free, so this app owns whatever now holds it. */
  stop(): Promise<void>;
}

export interface Application {
  readonly backend: BackendHandle;
  readonly dashboard: DashboardHandle;
  /** The URL the window should load once `startApplication` resolves. */
  readonly url: string;
  /** Stop both, in order, tolerating a teardown that fails. Never throws. */
  stop(): Promise<void>;
}

/** `apiUrl` plus the readiness path the API's own healthcheck keys on. */
function readyUrl(deps: SessionDeps): string {
  return `${deps.apiUrl}/ready`;
}

/**
 * An authenticated read the API guards with the operator token.
 *
 * `/ready` is deliberately unauthenticated — it has to answer for a Compose
 * healthcheck that holds no credential — which is exactly why it cannot answer
 * the question below. `GET /api/runs` is the cheapest route that does.
 */
const AUTHENTICATED_PROBE_PATH = "/api/runs";

/**
 * Is a backend that is already running holding a *different* operator token?
 *
 * ## The failure this exists to prevent
 *
 * `/ready` answering does not mean this app and that API agree about anything.
 * It means a process is listening. A stack left over from a previous install,
 * or one an operator started by hand, keeps whatever token it was started with,
 * and this app would adopt it, hand its own token to the dashboard, and produce
 * the worst-shaped failure in this application: **a dashboard that renders
 * perfectly and refuses every approval**, because the Route Handlers proxy with
 * one token and the API checks another. `lifecycle/token.ts` calls this out by
 * name — "a 200 page that looks healthy" — and nothing about it names its cause.
 *
 * ## Why it is asked only of a backend this app did not start
 *
 * A stack this app brings up gets its token from `deps.env`, which is the same
 * object the dashboard is started from, so they agree by construction and the
 * question has no answer to give. The one case where it can be wrong is the one
 * case where this app cannot know, and that is the only case asked.
 *
 * ## Why any answer other than 401/403 means "no mismatch"
 *
 * A probe that throws, or answers 404 because this API predates the route, is
 * not evidence of a disagreement. Reporting a mismatch on a failure to *ask*
 * would replace one confusing failure with a more confusing one.
 */
async function tokenDisagrees(deps: SessionDeps): Promise<boolean> {
  const token = deps.env.OPSPILOT_OPERATOR_TOKEN;
  if (token === undefined || token.trim() === "") {
    return false;
  }
  try {
    const { status } = await deps.authorizedHttp(`${deps.apiUrl}${AUTHENTICATED_PROBE_PATH}`, token);
    return status === 401 || status === 403;
  } catch {
    return false;
  }
}

function tokenDisagreement(deps: SessionDeps, endpoint: string) {
  // `--env-file` is not decoration. `docker-compose.yml` gives
  // `OPSPILOT_OPERATOR_TOKEN` the `:?` form, so Compose refuses to render the
  // project without it — `down` included. An installed machine has exactly one
  // file holding that value and this is it, so the command printed here is the
  // one that runs. In a checkout the repository's own `.env` sits beside the
  // compose file and Compose finds it by itself.
  const envFile = deps.composeEnvFile;
  const composeArgs = envFile === undefined ? "" : `--env-file "${envFile}" `;
  return {
    title: "The backend already running does not accept this app's operator token",
    detail:
      `The API on ${endpoint} answered its readiness check, so this app adopted it instead of ` +
      "starting its own. It then refused an authenticated read with the operator token this " +
      "app is about to give its dashboard. The two halves disagree about the token, so every " +
      "approval would be refused while the dashboard still rendered normally — which is a " +
      "healthy-looking screen that cannot do its one job.",
    remedy:
      "This app will not stop a stack it did not start, so stop that one and start this app " +
      "again:\n\n" +
      `    cd "${deps.repoRoot}" && docker compose ${composeArgs}down --remove-orphans\n\n` +
      "That leaves your runs and approvals in place — only the containers are removed. If you " +
      "started the stack by hand, it is running with a different OPSPILOT_OPERATOR_TOKEN; " +
      "export the same value this app is using before launching it, or remove the stack.",
  };
}

/** The URL the window loads, and the origin it is served from. Loopback only. */
export function dashboardUrl(deps: SessionDeps): string {
  return `http://127.0.0.1:${deps.dashboardPort}`;
}

/**
 * Bring up postgres, migrate, api and worker — or adopt the ones already up.
 *
 * Ordering is the point of this function, and it is the same ordering the
 * acceptance implies:
 *
 *   1. **Is Docker usable at all?** Before anything is started, so that a
 *      machine without Docker gets the message naming Docker rather than a
 *      readiness poll that runs out its clock against a `docker compose` that
 *      never ran.
 *   2. **Is the API already serving?** Before `up`, so the answer to "may I
 *      stop this on quit?" exists before there is anything to stop.
 *   3. **`compose up`.** Then
 *   4. **poll `/ready`.** `--wait` already waits for the healthchecked
 *      services, but it is satisfied by "the container started" for the
 *      worker, which has no healthcheck at all (`docker-compose.yml` says why),
 *      and `/ready` is the question the dashboard's own data depends on.
 */
export async function startBackend(deps: SessionDeps, emit: Emit): Promise<BackendHandle> {
  emit({ kind: "checking-docker", note: "Checking that Docker is available…" });

  const problem = await assessDocker(deps);
  if (problem !== null) {
    emit({ kind: "docker-missing", note: problem.title, problem });
    throw new StartupError(problem);
  }

  const endpoint = deps.apiUrl;
  if (await isHttpReady(readyUrl(deps), deps.http)) {
    if (await tokenDisagrees(deps)) {
      const problem = tokenDisagreement(deps, endpoint);
      emit({ kind: "failed", note: problem.title, problem });
      throw new StartupError(problem);
    }
    const why = `the API was already answering on ${endpoint} when the app started`;
    emit({ kind: "backend-already-running", note: `Using the backend already running on ${endpoint}.`, endpoint });
    return {
      ownership: "already-running",
      endpoint,
      // Not `async`: there is nothing to await, and an `async` arrow here would
      // say there were. It returns a resolved promise because the teardown it
      // stands in for never runs.
      stop: () => {
        emit({
          kind: "teardown-skipped",
          target: "backend",
          note: "Leaving the backend running: this app did not start it.",
          why,
        });
        return Promise.resolve();
      },
    };
  }

  emit({
    kind: "backend-starting",
    services: COMPOSE_SERVICES,
    note: `Starting ${COMPOSE_SERVICES.join(", ")} with Docker Compose…`,
  });

  const log = (source: LogSource) => (line: string) => {
    const trimmed = line.trim();
    if (trimmed !== "") {
      emit({ kind: "log", source, note: trimmed, line: trimmed });
    }
  };
  const up = await deps.runner.run(deps.docker, composeUpArgs(), {
    cwd: deps.repoRoot,
    env: deps.env,
    onOutput: log("compose"),
  });
  if (!up.ok) {
    const detail = describeOutcome("`docker compose up`", up);
    emit({ kind: "failed", note: "The backend stack did not start.", problem: composeFailure(detail) });
    throw new StartupError(composeFailure(detail));
  }

  await waitForHttp(readyUrl(deps), deps.http, deps.clock, {
    timeoutMs: deps.backendReadyTimeoutMs,
    onAttempt: (reason) => emit({ kind: "log", source: "compose", note: `waiting: ${reason}` }),
  });

  emit({
    kind: "backend-ready",
    endpoint,
    ownership: "started-by-us",
    note: `Backend ready on ${endpoint}.`,
  });

  let stopped = false;
  return {
    ownership: "started-by-us",
    endpoint,
    stop: async () => {
      if (stopped) {
        return;
      }
      stopped = true;
      emit({ kind: "teardown", target: "backend", note: "Stopping the backend stack…" });
      const down = await deps.runner.run(deps.docker, composeDownArgs(), {
        cwd: deps.repoRoot,
        env: deps.env,
        timeoutMs: deps.teardownTimeoutMs,
      });
      emit({
        kind: down.ok ? "teardown-done" : "failed",
        target: down.ok ? "backend" : undefined,
        note: down.ok
          ? "Backend stack stopped. Your runs and approvals are kept — only the containers were removed."
          : describeOutcome("`docker compose down`", down),
      });
      if (!down.ok) {
        throw new StartupError({
          title: "The backend stack did not stop",
          detail: describeOutcome("`docker compose down`", down),
          remedy:
            "Run `docker compose down` in the repository by hand. Nothing this app started should " +
            "still be running, and the next launch would otherwise adopt the containers it finds.",
        });
      }
    },
  };
}

function composeFailure(detail: string) {
  return {
    title: "The backend stack did not start",
    detail,
    remedy:
      "Read the output above — Compose names the service that failed and usually why. Then run " +
      "`docker compose up -d --wait postgres migrate api worker` by hand to watch it happen, and " +
      "start the app again.",
  };
}

/**
 * Serve the dashboard from the built standalone server, on loopback.
 *
 * ## Why the standalone server and not a file:// build
 *
 * `web/next.config.ts` sets `output: "standalone"`, and the app is Server
 * Components plus Route Handlers — a static export would forfeit exactly the
 * design that keeps the operator token server-side (`web/README.md`). The
 * repository has already solved "how is the standalone server run" in
 * `web/e2e/stack.ts`: copy `public/` and `.next/static` into the standalone tree,
 * then `node .next/standalone/server.js`. This is that, unchanged, and the two
 * comments below are the only additions.
 *
 * ## `HOSTNAME=127.0.0.1` is load-bearing, not tidiness
 *
 * The generated `server.js` reads `const hostname = process.env.HOSTNAME ||
 * '0.0.0.0'`. Left unset, a dashboard this app spawns would bind every
 * interface the machine has — reintroducing on the desktop the very LAN exposure
 * that P2-A1 closed on the compose-published ports, on a port nothing here
 * publishes through Docker. Setting it is what keeps the desktop build from
 * widening the P0, which D3 will check.
 */
export async function startDashboard(deps: SessionDeps, emit: Emit): Promise<DashboardHandle> {
  const url = dashboardUrl(deps);
  await assertPortFree(deps.dashboardPort, deps.portBusy, "The OpsPilot dashboard");

  const standalone = `${deps.webDir}/.next/standalone`;
  const serverJs = `${standalone}/server.js`;
  if (!deps.exists(serverJs)) {
    throw new StartupError({
      title: "The dashboard has not been built",
      detail: `${serverJs} does not exist, so there is nothing to serve.`,
      remedy:
        deps.packaged === true
          ? "That file ships inside the installed app, so its absence means the install is " +
            "incomplete rather than unbuilt. Reinstall it from the same installer."
          : "Run `npm run build` in web/ — which writes this file — then start the app again. " +
            "The desktop package's own `npm run build` does not build the dashboard; it only " +
            "compiles the shell.",
    });
  }

  // The minimal server does not copy `public/` or `.next/static` (see
  // `output.md` in next/dist/docs), so without this the dashboard renders
  // unstyled and every asset 404s. `stack.ts` copies them unconditionally; this
  // skips a copy that is already made, which is the same result for less churn.
  deps.copyDir?.(`${deps.webDir}/public`, `${standalone}/public`);
  deps.copyDir?.(`${deps.webDir}/.next/static`, `${standalone}/.next/static`);

  emit({ kind: "dashboard-starting", url, note: `Starting the dashboard on ${url}…` });

  let exited = false;
  const child: StartedProcess = deps.runner.start(deps.node, [serverJs], {
    cwd: deps.webDir,
    env: dashboardEnv(deps),
    onOutput: (line) => {
      const trimmed = line.trim();
      if (trimmed !== "") {
        emit({ kind: "log", source: "dashboard", note: trimmed });
      }
    },
  });
  void child.exited.then(() => {
    exited = true;
  });

  await waitForHttp(`${url}/`, deps.http, deps.clock, {
    timeoutMs: deps.dashboardReadyTimeoutMs,
    isAlive: () => !exited,
    onAttempt: (reason) => emit({ kind: "log", source: "dashboard", note: `waiting: ${reason}` }),
  });

  emit({ kind: "dashboard-ready", url, note: `Dashboard ready on ${url}.` });

  let stopped = false;
  return {
    url,
    stop: async () => {
      if (stopped) {
        return;
      }
      stopped = true;
      emit({ kind: "teardown", target: "dashboard", note: "Stopping the dashboard…" });
      child?.kill();
      const freed = await waitForPortFree(
        deps.dashboardPort,
        deps.portBusy,
        deps.clock,
        deps.teardownTimeoutMs,
      );
      emit({
        kind: freed ? "teardown-done" : "failed",
        target: freed ? "dashboard" : undefined,
        note: freed
          ? "Dashboard stopped."
          : `The dashboard was signalled, but port ${deps.dashboardPort} is still answering. ` +
            "Something other than this app may now hold it; stop it by hand before starting the app again.",
      });
    },
  };
}

/**
 * The environment the dashboard server runs with.
 *
 * Four things are set explicitly and everything else is inherited:
 *
 *   * `HOSTNAME` — loopback, for the reason above.
 *   * `PORT` — the one this app chose.
 *   * `NEXT_PUBLIC_API_BASE_URL` — the API's *published* URL. The split in
 *     `client.ts::getApiBaseUrl` reads the internal name only for server-side
 *     fetches inside a container; this dashboard runs on the host, so the
 *     published address is the correct one for both sides, exactly as
 *     `stack.ts` sets it.
 *   * `ELECTRON_RUN_AS_NODE` — makes the Electron binary run as a plain Node
 *     runtime, which is how the app serves its own dashboard without shipping a
 *     second copy of Node. Set here rather than in `dependencies.ts` because it
 *     belongs to "how the dashboard is served", which is this module's subject.
 *
 * `OPSPILOT_OPERATOR_TOKEN` arrives through `deps.env`, already resolved by
 * `resolveOperatorToken`. It is never passed to the renderer: the dashboard's
 * own server reads it, and the browser bundle still does not contain it.
 */
function dashboardEnv(deps: SessionDeps): NodeJS.ProcessEnv {
  return {
    ...deps.env,
    ELECTRON_RUN_AS_NODE: "1",
    HOSTNAME: "127.0.0.1",
    PORT: String(deps.dashboardPort),
    NEXT_PUBLIC_API_BASE_URL: deps.apiUrl,
  };
}

/**
 * Start everything, reporting progress, and guarantee the window gets a
 * terminal event.
 *
 * `startApplication` exists so that `main.ts` has exactly one shape to handle:
 * resolve, or throw — with a `failed` progress already emitted in the second
 * case. A caller that had to remember to emit the failure is a caller that
 * would eventually show a spinner that never resolves, which is the specific
 * outcome D1's acceptance rules out.
 */
export async function startApplication(deps: SessionDeps, emit: Emit): Promise<Application> {
  const backend = await startBackend(deps, emit);
  const dashboard = await startDashboard(deps, emit);

  return {
    backend,
    dashboard,
    url: dashboard.url,
    stop: async () => {
      // The dashboard first: it is the child process, and it is the thing most
      // likely to be mid-request. Stopping it before the API goes away means no
      // request is ever served by a dashboard whose backend has just vanished.
      await stopQuietly(() => dashboard.stop(), emit);
      await stopQuietly(() => backend.stop(), emit);
    },
  };
}

/**
 * Run one teardown step without letting its failure hide the other's.
 *
 * Teardown runs on quit, twice, and the failure that matters is the one the
 * operator would otherwise never see. Each step is reported through `emit`
 * before it throws; swallowing here keeps the second step from being skipped
 * by the first one's exception, which is the ordering that would leave
 * containers running after a window that reported a clean quit.
 */
async function stopQuietly(step: () => Promise<void>, emit: Emit): Promise<void> {
  try {
    await step();
  } catch (error) {
    const problem = toUserFacing(error);
    emit({ kind: "failed", note: problem.title, problem });
  }
}

/** Re-exported so `main.ts` can name what it is tearing down in one word. */
export type { TeardownTarget };