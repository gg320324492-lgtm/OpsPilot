/**
 * Bring the real OpsPilot stack up for the browser walkthrough, or skip cleanly.
 *
 * WHAT "THE STACK" IS, AND WHY IT IS ASSEMBLED THIS WAY
 * ----------------------------------------------------
 * The walkthrough drives **api + worker + web against PostgreSQL**, because the
 * golden path needs retrieval and retrieval is PostgreSQL-only in the README /
 * ADR-0004: on SQLite the vector store is `InMemoryVectorStore`, which keeps the
 * embeddings in the *process's* memory, so the API's reindex (one process) and
 * the worker's search (a second) do not see the same vectors and the run
 * abstains instead of refunding. The single-process SQLite path is exactly what
 * `scripts/demo_golden_path.py` and the GIF already cover; this test exists to
 * improve on it, so it must not fall back to it.
 *
 * * postgres, migrate, api, worker  -- started with `docker compose up` on the
 *   repository's own `docker-compose.yml`. This is what makes the retrieval
 *   genuinely cross-process: the reindex runs over HTTP in the *api* container
 *   and the search runs in the *worker* container, two processes sharing one
 *   pgvector database.
 *
 * * web (the Next.js dashboard)  -- started **on the host** as a plain
 *   `node .next/standalone/server.js`, NOT as the compose `web` service.
 *
 *   This walkthrough is about clicking through the golden path, and running the
 *   dashboard on the host keeps the browser and the Server Components pointed at
 *   the same published API port (`http://localhost:8000`), so the walkthrough's
 *   concern is the clicks, not the deployment's URL wiring. The containerised
 *   `web` service is the *other* configuration, and it has its own spec --
 *   `web-service.spec.ts`, which starts the compose `web` service with
 *   `startContainerisedWebOrSkip` below.
 *
 *   The reason the container path once could NOT be used is now fixed, and the
 *   fix is what that spec holds: `docker-compose.yml` gives the `web` container
 *   `NEXT_PUBLIC_API_BASE_URL` (`http://localhost:8000`, the published URL the
 *   *browser* needs) and `API_INTERNAL_BASE_URL=http://api:8000` (the compose
 *   service name the *server* needs). `client.ts::getApiBaseUrl` now reads the
 *   internal name for server-side fetches and the public one for browser
 *   fetches, split by `typeof window`. Before that split it read the public name
 *   for both, and `localhost:8000` inside a container is the container itself,
 *   so every Server-Component read failed with "Cannot reach the API" (verified:
 *   `docker compose exec web node -e "fetch('http://localhost:8000/health')"`
 *   -> fetch failed, while `http://api:8000/health` -> 200). The compose comment
 *   that once claimed this "works because the port is published" -- true only
 *   from the host -- has been corrected with the split.
 *
 * HOW IT SKIPS (ADR-0004 pattern)
 * -------------------------------
 * The repository's Postgres-dependent tests (`tests/integration/test_citations.py`,
 * `tests/unit/test_vector_stores.py`) read `OPSPILOT_DATABASE_URL` and
 * `pytest.skip` with a reason that NAMES the variable when no Postgres is
 * reachable -- a skip is a skip, never read as a pass. This module holds the
 * same property for the browser test:
 *
 *   * `E2E_SKIP_DOCKER` set, or `docker info` failing -> skip, naming Docker;
 *   * the stack NOT coming up after Docker was reachable -> FAIL, never skip.
 *     A machine that said it had Docker and then could not start the stack is a
 *     real failure, and hiding it as a skip is the "green skipped run that
 *     proved nothing" failure ADR-0004's cost section is written to prevent.
 *
 * TEARDOWN OWNS THE PORT, NOT JUST THE CONTAINERS
 * ----------------------------------------------
 * `compose down -v` removes containers and volumes. It has no effect whatever on
 * a `node` process this module started on the host, and a comment here used to
 * claim teardown left "no half-up stack holding ports 3000/8000" while the code
 * below it did exactly that. CI run 38202887580 is the consequence:
 *
 *   * `killChild` sent SIGTERM to the pid it had spawned and stopped there. The
 *     pid then was `npm run start`, a wrapper whose `next start` grandchild owns
 *     the socket; signalling the wrapper orphans the grandchild, and it keeps
 *     holding :3000 for the rest of the runner's life.
 *   * Playwright re-runs `beforeAll` after a failing test in the same file (the
 *     module is re-imported in a fresh worker, so `beforeAll` runs again). The
 *     suite therefore started the stack a second time in one run, and the second
 *     server died with `EADDRINUSE ... port 3000`.
 *
 * Worse than the crash: `waitForHttp` only asks "does something on this port
 * answer 2xx?". A surviving orphan answers 200 -- Next renders the page shell,
 * and the server-side API read failing is a *200 page*, not a refused
 * connection -- so the probe would have been satisfied by the wrong server and
 * the walkthrough would have driven a stale dashboard pointed at a torn-down
 * API. A green run that clicked through nothing.
 *
 * So the port is treated as owned, in three places: nothing is started on an
 * already-busy port (`assertPortFree`), the readiness probe aborts if the child
 * we spawned dies (`waitForHttp`'s `child`), and teardown waits for the port to
 * actually be released rather than assuming a signal was enough
 * (`stopDashboard`). Every one of those fails loudly rather than proceeding.
 *
 * Which port that is, and why it is not 3000, is `dashboard-port.ts`'s subject.
 * The short version: the host-run dashboard here and the compose `web` service
 * below are two servers, and when they shared 3000 they starved each other
 * within a single `npm run e2e`.
 *
 * WHY THE WORKER'S OWN LOGS ARE COLLECTED HERE, IN THIS PROCESS
 * -----------------------------------------------------------
 * CI run 38202887580's `web-e2e` failure was un-diagnosable for a reason that is
 * not obvious and that a CI-side fix alone does not address. The symptom was a
 * run that sat at `RECEIVED` with **zero steps for the full 90 seconds** while
 * the API stayed responsive -- the dashboard rendered, the ticket was created,
 * and no process ever claimed the run. The question that failure poses is
 * exactly one question: *what did the worker say?* And the CI job captured no
 * worker output, so the answer died with the runner.
 *
 * The obvious repair is a `docker compose logs worker` step in the workflow. On
 * its own it would have captured **nothing**, and this is the part worth
 * knowing:
 *
 *   * `afterAll` runs when a test FAILS, exactly as it does when one passes --
 *     that is what makes teardown a teardown. Both starters here end in
 *     `docker compose down -v`, so by the time the workflow's next step runs,
 *     the containers are gone and `compose logs` has nothing left to print.
 *   * `web-e2e/golden-path.spec.ts` is not the only spec, and Playwright runs
 *     files in order: `web-service.spec.ts` starts its own stack and its own
 *     `afterAll` ends in the same `down -v`. Whichever tears down last removes
 *     the containers the earlier one would have been asked about.
 *
 * So the logs are written **from here, before the `down -v`**, into
 * `web/test-results/compose-logs/` -- the directory Playwright already uploads
 * on failure, and which `web/.gitignore` already excludes. The capture is
 * unconditional rather than failure-gated: the code path that most needs it
 * (a failing test) is precisely the one where a conditional written by the same
 * person who just got the failure wrong is worth least, and the files are a few
 * KB of a directory nobody commits. The workflow still gets its own
 * `compose ps -a` / `compose logs` step, because a failure inside `beforeAll`
 * leaves `stack === null`, skips teardown entirely, and leaves live containers
 * that only the workflow can see.
 *
 * `--wait` is why the condition is hard to see in the first place. It returns
 * as soon as every healthchecked service is healthy and every one-shot has
 * exited -- and the worker has **no healthcheck** (docker-compose.yml says why:
 * it serves no HTTP endpoint, so there is nothing to probe). Measured on this
 * machine, `docker compose up -d --wait` returned with the worker container
 * reporting `Up 1 second`: `--wait` is satisfied by "the container started",
 * which is several seconds short of "the process is polling". Nothing in the
 * run distinguishes those two states, which is why a worker that came up and
 * then sat there reads exactly like a worker that is merely slow.
 * `waitForWorkerPolling` below is what closes that gap, by asking the worker
 * itself.
 */

import { type ChildProcess, execFileSync, spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { DASHBOARD_PORT, DASHBOARD_URL, isPortBusy, portOfUrl, waitForPortFree } from "./dashboard-port";

/** The repository root: the nearest ancestor of the cwd holding docker-compose.yml. */
export const REPO_ROOT = findRepoRoot();

function findRepoRoot(): string {
  let dir = process.cwd();
  for (;;) {
    if (existsSync(path.join(dir, "docker-compose.yml"))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      return path.resolve(process.cwd(), "..");
    }
    dir = parent;
  }
}

/** The `web/` directory (this file lives in `web/e2e/`). */
const WEB_DIR = path.join(REPO_ROOT, "web");

/** The `web` service's own published URL. The dashboard, containerised. */
export const WEB_SERVICE_URL = process.env.E2E_WEB_SERVICE_URL ?? "http://localhost:3000";

/**
 * The port Compose must publish for the containerised dashboard.
 *
 * Read out of `WEB_SERVICE_URL` rather than assumed, so the URL this module
 * probes and the port `docker-compose.yml` binds are the same fact. Compose
 * publishes a literal `3000:3000`, so an operator who moves this one with
 * `E2E_WEB_SERVICE_URL` has to move the compose mapping too -- which the
 * pre-flight below turns into a named error instead of a container that never
 * becomes healthy.
 */
export const WEB_SERVICE_PORT = portOfUrl(WEB_SERVICE_URL, DASHBOARD_PORT);

/** A disposable token: only to satisfy Compose's `:?` render guard. Not a secret. */
const THROWAWAY_TOKEN = "e2e-disposable-token-not-a-secret";

/** The deterministic provider the golden path runs on. No key, no network model. */
const PROVIDER_ENV: Record<string, string> = {
  MODEL_PROVIDER: "fake",
  OPSPILOT_FAKE_SCENARIO: "duplicate_charge",
  EMBEDDING_PROVIDER: "local",
  RETRIEVAL_MIN_SCORE: "0.22",
};

/**
 * The dashboard's published URL (the host `server.js`, on `DASHBOARD_PORT`).
 *
 * Re-exported rather than re-derived so `playwright.config.ts`'s `use.baseURL`
 * and the server this module starts cannot disagree about which port the
 * walkthrough is driving -- see `dashboard-port.ts` for why that is one
 * decision and not two.
 */
export const BASE_URL = DASHBOARD_URL;

/** The API's published URL (the compose `api` service, port 8000). */
export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

/**
 * The environment every `docker compose` call here runs with.
 *
 * One definition, because a diagnostic that renders the compose file with a
 * *different* environment than the stack was started with can fail for a reason
 * that has nothing to do with the failure being diagnosed -- `:?` on
 * `OPSPILOT_OPERATOR_TOKEN` refuses to render at all when the variable is
 * unset, which would turn "here are the worker's logs" into an empty file and a
 * second, entirely invented mystery.
 */
function stackEnv(): NodeJS.ProcessEnv {
  return { ...process.env, OPSPILOT_OPERATOR_TOKEN: THROWAWAY_TOKEN, ...PROVIDER_ENV };
}

export type StackHandle =
  | { skip: true; reason: string }
  | { skip: false; teardown: () => void | Promise<void> };

/**
 * Is `docker` usable? Returns a reason string when it is not, else `null`.
 *
 * `docker info` rather than `docker --version`: the CLI being on PATH says
 * nothing about whether a daemon is reachable, and a bound-but-dead daemon would
 * turn every `compose` call into a timeout. `docker info` fails fast.
 */
function dockerUnusableReason(): string | null {
  if (process.env.E2E_SKIP_DOCKER) {
    return "E2E_SKIP_DOCKER is set";
  }
  const probe = spawnSync("docker", ["info"], { stdio: "ignore" });
  if (probe.error || probe.status !== 0) {
    return "no reachable Docker daemon (`docker info` failed); set E2E_SKIP_DOCKER to silence";
  }
  if (!existsSync(path.join(REPO_ROOT, "docker-compose.yml"))) {
    return "no docker-compose.yml at the repository root";
  }
  return null;
}

/**
 * Start the stack: postgres + migrate + api + worker via Compose, web on the
 * host. Call from `test.beforeAll`; on a skip the caller must `test.skip(reason)`.
 */
export async function startStackOrSkip(): Promise<StackHandle> {
  const reason = dockerUnusableReason();
  if (reason !== null) {
    return { skip: true, reason };
  }

  // BEFORE the two minutes of compose and `next build`: if the port is already
  // taken, nothing below can succeed, and every minute spent finding that out is
  // a minute the reader of a red run waits to be told something they could have
  // been told at second one. The message says how to move the run rather than
  // just reporting the symptom, because the common cause is something else on
  // the machine (a `next dev`, a previous run killed before it could tear down)
  // and "EADDRINUSE" alone does not name that.
  await assertPortFree(DASHBOARD_PORT);

  const env = stackEnv();

  // TEAR DOWN FIRST, WITH VOLUMES. `docker compose up` REUSES the named
  // `postgres-data` and `mcp-data` volumes, so a run against a stack a previous
  // session left behind starts with a database that already holds runs and an
  // MCP store that already holds the refund. The golden path then fails for a
  // reason that has nothing to do with the dashboard -- `billing.issue_refund`
  // returns `invalid_state` because TX-88219 was already refunded, the run
  // records the call as `failed`, and the exactly-once assertion is
  // contaminated. This was found the hard way; `down -v` here is what makes
  // "the refund executed exactly once" a claim about THIS run.
  spawnSync("docker", ["compose", "down", "-v", "--remove-orphans"], {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env,
  });

  // Only the four services the dashboard needs. The compose `web` service is
  // deliberately excluded -- see the module docstring for why it cannot reach
  // the API from inside its container.
  const up = spawnSync(
    "docker",
    [
      "compose", "up", "-d", "--wait", "--wait-timeout", "300", "--build",
      "postgres", "migrate", "api", "worker",
    ],
    { cwd: REPO_ROOT, stdio: "inherit", env },
  );
  if (up.error || up.status !== 0) {
    // NOT a skip: Docker was reachable and the stack did not come up. A skip
    // here would be the "green run that proved nothing" ADR-0004 warns about.
    spawnSync("docker", ["compose", "down", "-v"], { cwd: REPO_ROOT, stdio: "inherit", env });
    throw new Error(
      "`docker compose up` did not bring postgres/migrate/api/worker up. Docker " +
        "was reachable, so this is a failure rather than a skip -- see the " +
        "compose output above for the service that did not become healthy.",
    );
  }

  await waitForHttp(`${API_URL}/ready`);

  // `--wait` has returned and the worker is NOT yet known to be polling. See
  // the module docstring: the worker has no healthcheck, so `--wait` is
  // satisfied by "the container started" -- measured at `Up 1 second` on this
  // machine -- which is not the same claim. This asks the worker itself.
  //
  // It runs BEFORE the `next build` below, which is the reason it is cheap to be
  // strict here: the build is going to spend the next minute anyway, so a
  // worker that will never poll is discovered during time the run was spending
  // regardless, rather than ninety seconds into the browser polling for a parked
  // banner the worker was never going to produce.
  await waitForWorkerPolling(env);

  // The dashboard, on the host, built and run against the published API port.
  // On the host there is no separate internal URL, so `client.ts` falls back to
  // `NEXT_PUBLIC_API_BASE_URL` for the server-side path exactly as it does for
  // the browser -- the same value reaches both, and it is the right one here.
  // `PORT` is `DASHBOARD_PORT` rather than a literal, because `BASE_URL` is
  // derived from the same value and a literal here is how the two would drift.
  const webEnv = {
    ...env,
    NEXT_PUBLIC_API_BASE_URL: API_URL,
    PORT: String(DASHBOARD_PORT),
  };
  const build = spawnSync("npm", ["run", "build"], {
    cwd: WEB_DIR,
    stdio: "inherit",
    env: webEnv,
    shell: process.platform === "win32",
  });
  if (build.error || build.status !== 0) {
    spawnSync("docker", ["compose", "down", "-v"], { cwd: REPO_ROOT, stdio: "inherit", env });
    throw new Error("`next build` failed for the dashboard (see output above).");
  }

  // `web/next.config.ts` sets `output: "standalone"`, so the supported way to
  // serve the built app is `node .next/standalone/server.js`, NOT `next start`
  // (`next start` warns "does not work with output: standalone").
  // `node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/output.md`
  // also says the minimal server does not copy `public/` or `.next/static`, so
  // both are copied into the standalone tree first -- without them the dashboard
  // renders unstyled and every asset 404s.
  copyDir(path.join(WEB_DIR, "public"), path.join(WEB_DIR, ".next", "standalone", "public"));
  copyDir(path.join(WEB_DIR, ".next", "static"), path.join(WEB_DIR, ".next", "standalone", ".next", "static"));

  // Spawned as `node server.js` rather than through `npm run start`, and that is
  // load-bearing for teardown, not a stylistic choice: the pid returned here is
  // then the process that owns the listening socket, so signalling it reaches the
  // server. The `npm` wrapper it replaced was a parent of that process, so
  // SIGTERM stopped at the wrapper and orphaned the very thing holding the port.
  const child = spawn(
    process.execPath,
    [path.join(WEB_DIR, ".next", "standalone", "server.js")],
    { cwd: WEB_DIR, stdio: "inherit", env: webEnv },
  );

  // `child` is passed so the probe can tell "not up yet" from "never coming up":
  // if the server dies -- EADDRINUSE being the case that matters here -- the
  // poll aborts immediately with its exit code instead of waiting out the full
  // timeout against whatever else might answer on that port.
  await waitForHttp(`${BASE_URL}/`, 120_000, child);

  return {
    skip: false,
    teardown: async () => {
      // The port first, then the containers. Ordering is not cosmetic: the
      // dashboard is the only thing here that survives `compose down`, and
      // releasing it before returning is what makes a second run in the same
      // session safe.
      await stopDashboard(child, DASHBOARD_PORT);
      // BEFORE `down -v`, which is the whole point -- see the module docstring.
      // After it there is nothing left to ask.
      writeComposeDiagnostics(env);
      spawnSync("docker", ["compose", "down", "-v"], {
        cwd: REPO_ROOT,
        stdio: "inherit",
        env,
      });
    },
  };
}

/**
 * Start the CONTAINERISED dashboard -- the compose `web` service -- or skip.
 *
 * This is the configuration the client's server/browser split exists for, and
 * the one the host-run `startStackOrSkip` above deliberately avoids: inside the
 * `web` container, `NEXT_PUBLIC_API_BASE_URL` is the published
 * `http://localhost:8000`, which is the container's own loopback, while the API
 * is a sibling reachable only as `http://api:8000` via `API_INTERNAL_BASE_URL`.
 * Before `client.ts::getApiBaseUrl` split by runtime, the Server Component reads
 * used the localhost URL and the containerised dashboard rendered "Could not
 * reach the API" (reproduced: `docker compose exec web node -e
 * "fetch('http://localhost:8000/health')"` -> `fetch failed`, while
 * `fetch('http://api:8000/health')` -> 200). This helper starts the real thing
 * so that fix has a test that would have failed before it.
 *
 * Only postgres + migrate + api + web: the walkthrough's worker is not needed to
 * prove a server-side read reaches the API -- a `GET /api/runs` against the api
 * container over the compose network is the behaviour that was broken.
 *
 * Skips cleanly without Docker (same ADR-0004 pattern as `startStackOrSkip`); a
 * stack Docker was reachable enough to attempt and could not bring up is a
 * failure, never a skip.
 */
export async function startContainerisedWebOrSkip(): Promise<StackHandle> {
  const reason = dockerUnusableReason();
  if (reason !== null) {
    return { skip: true, reason };
  }

  // Same port-ownership check as the host-run stack above, and for the same
  // reason: Compose would otherwise fail to publish the port with a bind error
  // that names neither what is holding it nor how to move the run. It matters
  // here especially because this is the port the *host* dashboard also uses --
  // a host process left over from a previous run is exactly what would be
  // sitting on it.
  await assertPortFree(WEB_SERVICE_PORT, "the compose `web` service");

  const env = stackEnv();

  // A fresh database first, so the api's `/ready` is a real migration state
  // rather than a volume a previous session left behind (same reasoning as
  // `startStackOrSkip`).
  spawnSync("docker", ["compose", "down", "-v", "--remove-orphans"], {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env,
  });

  // `--build` because this spec is about the built image's runtime behaviour:
  // the fix is in a Server Component read, and a stale image would test the old
  // code. The `web` service gates on the api being healthy (`depends_on`), and
  // `--wait` blocks until its own healthcheck passes.
  const up = spawnSync(
    "docker",
    [
      "compose", "up", "-d", "--wait", "--wait-timeout", "300", "--build",
      "postgres", "migrate", "api", "web",
    ],
    { cwd: REPO_ROOT, stdio: "inherit", env },
  );
  if (up.error || up.status !== 0) {
    spawnSync("docker", ["compose", "down", "-v"], { cwd: REPO_ROOT, stdio: "inherit", env });
    throw new Error(
      "`docker compose up` did not bring postgres/migrate/api/web up. Docker " +
        "was reachable, so this is a failure rather than a skip -- see the " +
        "compose output above for the service that did not become healthy.",
    );
  }

  await waitForHttp(`${WEB_SERVICE_URL}/`);

  return {
    skip: false,
    teardown: () => {
      // The second stack tears down the first one's containers too -- they are
      // one Compose project -- so it carries the same capture. Without it, this
      // file's `down -v` would erase the logs the golden-path failure needs.
      writeComposeDiagnostics(env);
      spawnSync("docker", ["compose", "down", "-v"], {
        cwd: REPO_ROOT,
        stdio: "inherit",
        env,
      });
    },
  };
}

/**
 * The API base URL the `web` container's *server-side* fetch actually used.
 *
 * Asked of the container itself, over the compose network: it reads the same
 * `API_INTERNAL_BASE_URL` the client does from its own process environment and
 * probes both candidate URLs, so the test can name the URL rather than only
 * asserting a page rendered. Runs one `node -e` per candidate because the slim
 * image ships no HTTP client (the Dockerfile's healthcheck does the same).
 */
export function probeFromWebContainer(url: string): { ok: boolean; detail: string } {
  const program =
    "fetch(" +
    JSON.stringify(url) +
    ").then(r=>{console.log('OK '+r.status);process.exit(0)})" +
    ".catch(e=>{console.log('FAIL '+e.message);process.exit(0)})";
  const out = execFileSync("docker", ["compose", "exec", "-T", "web", "node", "-e", program], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    env: stackEnv(),
  });
  const line = out.trim().split("\n").pop() ?? "";
  return { ok: line.startsWith("OK"), detail: line };
}

// ---------------------------------------------------------------------------
// The worker's own account of itself: what it logged, and what Docker thinks
// of its container.
//
// Every function below reads rather than asserts, except
// `waitForWorkerPolling`, which is a precondition on a precondition. None of
// them can change what the golden-path test claims; they exist so that when it
// does fail, the failure carries the one piece of evidence that explains it.
// ---------------------------------------------------------------------------

/**
 * Run a `docker compose` subcommand and return its combined output.
 *
 * Never throws. A diagnostic that fails is a second failure nobody asked for,
 * and the commands here are all best-effort reads against containers that may
 * legitimately be gone (that is exactly when they are being asked). `--no-color`
 * because ANSI escapes in a `.log` file under `test-results/` are unreadable in
 * a browser-based artifact viewer and in `grep`.
 */
function composeOutput(args: string[], env: NodeJS.ProcessEnv): string {
  const out = spawnSync("docker", ["compose", ...args], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    env,
  });
  const text = `${out.stdout ?? ""}${out.stderr ?? ""}`.trim();
  return text === "" ? "(no output)" : text;
}

/**
 * The worker's container state, read from Docker rather than inferred.
 *
 * `.State.Restarting` and `.RestartCount` are the two that matter, and they are
 * the two an assertion on `--wait` cannot reach. `restart: unless-stopped` on
 * the worker means a process that dies at boot is *restarted*, so a
 * crash-looping worker is not absent -- it is a container that looks alive,
 * reports `Up`, and never polls. `RestartCount > 0` is the signature, and it is
 * invisible from the host unless someone looks.
 *
 * `-aq` rather than `-q`: `docker compose ps -q` lists only running containers
 * by default, which reports *no worker at all* for a worker that exited -- the
 * one state where the container id is most wanted.
 */
function workerContainerState(env: NodeJS.ProcessEnv): {
  found: boolean;
  status: string;
  restarting: boolean;
  exitCode: number;
  restarts: number;
} {
  const ids = spawnSync("docker", ["compose", "ps", "-aq", "worker"], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    env,
  });
  const id = (ids.stdout ?? "").trim().split("\n").pop() ?? "";
  if (id === "") {
    return { found: false, status: "", restarting: false, exitCode: 0, restarts: 0 };
  }
  const fields = spawnSync(
    "docker",
    [
      "inspect",
      "--format",
      "{{.State.Status}}|{{.State.Restarting}}|{{.State.ExitCode}}|{{.RestartCount}}",
      id,
    ],
    { cwd: REPO_ROOT, encoding: "utf-8", env },
  );
  const [status = "", restarting = "", exitCode = "0", restarts = "0"] = (fields.stdout ?? "")
    .trim()
    .split("|");
  return {
    found: true,
    status,
    restarting: restarting === "true",
    exitCode: Number(exitCode),
    restarts: Number(restarts),
  };
}

/** The worker's last `n` log lines, as the worker itself printed them. */
function workerLogs(env: NodeJS.ProcessEnv, tail = 200): string {
  return composeOutput(["logs", "--no-color", "--timestamps", "--tail", String(tail), "worker"], env);
}

/**
 * The worker's state and log, as one block of text.
 *
 * Shaped for a human reading a CI log or a `test-results/` file, which is the
 * only reader it has. Public because `golden-path.spec.ts` attaches it to a
 * failure: the gap it closes is that a 90-second timeout on "the run parked"
 * says nothing about the process that was supposed to cause the parking.
 */
export function workerReport(): string {
  const env = stackEnv();
  const state = workerContainerState(env);
  const stateLine = state.found
    ? `container ${state.status}` +
      (state.restarting ? ", RESTARTING" : "") +
      ` (exit code ${state.exitCode}, ${state.restarts} restart(s))`
    : "container NOT FOUND (compose reports no worker container at all)";
  return [`--- worker container: ${stateLine}`, "--- worker logs (last 200 lines):", workerLogs(env)].join(
    "\n",
  );
}

/**
 * Block until the worker says it is polling, or fail naming what it said.
 *
 * **`--wait` cannot do this and the worker will not grow a healthcheck to make
 * it able to.** The worker serves no HTTP endpoint, so there is nothing to
 * probe; `docker-compose.yml` records that reasoning next to the absent
 * `healthcheck:` and it is correct. What the worker *does* have is a boot line
 * it prints once, from `_run`, immediately before handing control to
 * `poll_forever` (`worker/__main__.py`):
 *
 *     opspilot-worker[<id>]: booted; marked N interrupted run(s) failed; polling every 1.0s
 *
 * That line is the worker's own claim to be polling, printed by the code that
 * is about to poll. Waiting for it turns an invisible precondition into a
 * checked one, and costs nothing when the worker is healthy.
 *
 * Two failure branches, because they mean different things:
 *
 *   * the container is gone, exiting, or has RESTARTED -> fail NOW rather than
 *     waiting out the deadline. A worker in a `restart: unless-stopped` loop is
 *     never going to settle, and waiting 60s to learn what the first second
 *     already said is how this failure got misdiagnosed once already.
 *   * the deadline passes with a running container that never printed it ->
 *     fail, with the log. A process that started, stayed up, and said nothing
 *     is its own answer, and the log is what it said.
 *
 * **Why a restart is a failure and not a shrug.** On a freshly-created stack
 * container `RestartCount` starts at zero and the only thing that increments it
 * is the worker process dying. And because the log is per-container and
 * append-only, a worker that booted, crashed, and came back leaves its FIRST
 * boot line in the log forever -- so matching on the log alone would pass a
 * crash-looping worker that had since stopped polling, which is the exact
 * failure being guarded against. `restarts > 0` is what closes that, and it is
 * free: on a healthy stack it is zero.
 *
 * The timeout is 60 seconds for a line this machine prints in well under one
 * (measured: `docker compose up -d --wait` returns with the worker container at
 * `Up 1 second`, and the boot line is in its log by then). It is a margin for a
 * cold runner under load, not a budget for a worker that is working: a healthy
 * worker satisfies this in the first poll iteration. What it deliberately is NOT
 * is a longer timeout on the golden-path assertion -- the 90-second park wait is
 * untouched, and a worker that boots late and then polls correctly is no longer
 * the question.
 */
async function waitForWorkerPolling(env: NodeJS.ProcessEnv, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  const BOOTED = /opspilot-worker\[.*]: booted;/;
  for (;;) {
    const logs = workerLogs(env, 50);
    if (BOOTED.test(logs)) {
      return;
    }
    const state = workerContainerState(env);
    const stateLine = state.found
      ? `container ${state.status}, ${state.restarts} restart(s), exit code ${state.exitCode}`
      : "no worker container";
    if (
      !state.found ||
      state.restarting ||
      state.status === "exited" ||
      state.status === "dead" ||
      state.restarts > 0
    ) {
      throw new Error(
        `the worker is not polling: there is no worker container, or it is ` +
          `${state.status === "exited" ? "exited" : state.status} rather than running (${stateLine}). ` +
          `No run submitted from here could ever have been claimed by a worker in that state -- ` +
          `which is the condition CI run 38202887580 reported as a run stuck at RECEIVED with no ` +
          `steps, and it should be treated as the same failure until the log says otherwise.\n` +
          `${diagnoseWorker(env, stateLine)}`,
      );
    }
    if (Date.now() >= deadline) {
      throw new Error(
        `the worker did not report itself polling within ${Math.round(timeoutMs / 1000)}s. It is ` +
          `running (${stateLine}) but never printed a boot line, so no run submitted from here could ` +
          `have been claimed. A running container is all \`docker compose up --wait\` was ever able to ` +
          `check -- it is not enough.\n${diagnoseWorker(env, stateLine)}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

/** The log block shared by both failure messages above. */
function diagnoseWorker(env: NodeJS.ProcessEnv, stateLine: string): string {
  return [`worker container: ${stateLine}`, "worker logs:", workerLogs(env, 200)].join("\n");
}

/**
 * Write the whole stack's own logs to `web/test-results/compose-logs/`.
 *
 * Called from both teardowns, immediately before `docker compose down -v`, and
 * unconditionally -- see the module docstring for why a CI-side `compose logs`
 * step cannot do this job and why a failure-gated capture is the wrong shape.
 *
 * `test-results/` is where Playwright already puts the trace and the screenshot
 * and is already gitignored (`web/.gitignore`), and the `web-e2e` job already
 * uploads it on failure. Writing here means the worker's log travels with the
 * artefact a reader is already going to open, rather than in a second place they
 * have to know to look.
 *
 * Best-effort throughout: this runs inside `afterAll`, where a thrown error
 * masks the test failure that actually mattered.
 */
function writeComposeDiagnostics(env: NodeJS.ProcessEnv): void {
  const dir = path.join(WEB_DIR, "test-results", "compose-logs");
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, "ps.txt"), composeOutput(["ps", "-a"], env), "utf-8");
    // Every service this repository's compose file declares, including `web`:
    // a failure in `web-service.spec.ts` is as unexplained without it, and
    // asking for a service that was never started costs one empty file.
    for (const service of ["postgres", "migrate", "api", "worker", "web"]) {
      writeFileSync(
        path.join(dir, `${service}.log`),
        composeOutput(["logs", "--no-color", "--timestamps", "--tail", "500", service], env),
        "utf-8",
      );
    }
  } catch {
    // Nothing here may mask the run's real outcome. A read-only or full disk
    // loses the diagnostics, not the test result.
  }
}

/**
 * Refuse to start on a port something already holds.
 *
 * This is the check that makes the readiness probe honest. `waitForHttp` asks
 * "does anything on this port answer 2xx?", which a server left over from an
 * earlier run satisfies perfectly well -- and the walkthrough would then drive
 * *that* one, pointed at an API that is no longer up, and report the result as
 * though it had learned something about the dashboard. Proving the port is free
 * immediately before we bind it removes the ambiguity at the only place it can
 * be removed.
 *
 * It throws rather than retrying or picking another port. Picking another
 * silently would mean the browser's `baseURL` and the server's `PORT` came from
 * different decisions -- the exact drift `dashboard-port.ts` exists to prevent --
 * and retrying would only turn a one-line diagnosis into a mystery.
 */
async function assertPortFree(port: number, subject = "the dashboard"): Promise<void> {
  if (!(await isPortBusy(port))) {
    return;
  }
  throw new Error(
    `something is already listening on port ${port}, so ${subject} would either fail ` +
      `to bind it or -- worse -- be silently driven in place of the one this run ` +
      `started. Stop it, or move this run: E2E_PORT=<free port> npm run e2e (the port ` +
      `is read by both playwright.config.ts and this module, so the browser and the ` +
      `server stay in agreement).`,
  );
}

/**
 * Terminate the dashboard and wait until its port is actually free.
 *
 * The wait is the point. "A signal was delivered" is not "the socket is
 * released": the kernel closes the listener when the process exits, which is a
 * moment after the signal, and the next run's bind can land in between. So
 * teardown returns only once nothing is listening -- and says so loudly if that
 * never happens, rather than leaving a poisoned port for the next run to trip
 * over and misdiagnose as its own failure.
 */
async function stopDashboard(child: ChildProcess, port: number): Promise<void> {
  if (child.pid !== undefined) {
    if (process.platform === "win32") {
      // `server.js` is spawned directly (no shell), but on Windows it may still
      // hold a child tree; `/T` kills the whole tree and `/F` forces it.
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      child.kill("SIGTERM");
      // Escalate rather than hope: a server that ignores SIGTERM (or is wedged
      // mid-render) would otherwise keep the port for the whole session.
      if (!(await waitForExit(child, 5_000))) {
        child.kill("SIGKILL");
        await waitForExit(child, 5_000);
      }
    }
  }

  if (!(await waitForPortFree(port))) {
    throw new Error(
      `port ${port} is still answering after the dashboard (pid ${child.pid ?? "unknown"}) ` +
        `was killed, so something other than this run holds it. Stop that process before ` +
        `running the walkthrough again -- otherwise this run's pre-flight check will ` +
        `refuse to start, and, without that check, the walkthrough would have driven ` +
        `the wrong server entirely.`,
    );
  }
}

/** Resolve `true` if `child` exits within `timeoutMs`, else `false`. */
function waitForExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  // Already gone: a signal kill leaves `exitCode` null and sets `signalCode`, so
  // both are checked or a dead child reads as a live one for the full timeout.
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const onExit = (): void => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off("exit", onExit);
      resolve(false);
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

/** Recursively copy `from` to `to`, skipping when `from` does not exist. */
function copyDir(from: string, to: string): void {
  if (!existsSync(from)) {
    return;
  }
  cpSync(from, to, { recursive: true });
}

/**
 * Poll a URL until it answers 2xx/3xx, or throw.
 *
 * A small hand-rolled poller rather than a dependency. The api's readiness is
 * gated by `compose --wait` already; this waits for the *dashboard* on the host
 * to accept connections (and, before that, for the api's `/ready`).
 *
 * `child` is optional and changes what the probe means. Without it the question
 * is only "is anything answering"; with it, the probe is watching a specific
 * process and a process that dies ends the wait at once. That is what stops a
 * 2xx from an unrelated listener being read as success -- the failure this whole
 * module's port handling exists to prevent, where a server left over from a
 * previous run answers the probe and the walkthrough quietly drives it instead.
 */
export async function waitForHttp(
  url: string,
  timeoutMs = 120_000,
  child?: ChildProcess,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown = null;
  while (Date.now() < deadline) {
    if (child !== undefined && (child.exitCode !== null || child.signalCode !== null)) {
      throw new Error(
        `the dashboard process exited (code ${child.exitCode}, signal ${child.signalCode}) ` +
          `before ${url} ever answered, so the wait was abandoned rather than run out. ` +
          `Its own output above is the diagnosis -- EADDRINUSE means another process ` +
          `holds the port this run was told to use.`,
      );
    }
    try {
      const res = await fetch(url, { redirect: "manual" });
      if (res.status >= 200 && res.status < 400) {
        return;
      }
      lastError = new Error(`${url} answered ${res.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${url} never answered: ${String(lastError)}`);
}

/**
 * Ask the billing MCP server directly whether a transaction is refunded.
 *
 * The assertion that matters for "the refund executed exactly once" must not be
 * the run's own account of itself -- this repository's core habit
 * (`.scratch/verify_m4.py`): a run's `COMPLETED` status proves nothing about
 * whether a refund row exists; the billing server's transaction row is what
 * happened. `docker compose exec` runs the *same* in-process MCP gateway the
 * worker uses, against the shared `mcp-data` volume, so this is the server
 * answering -- not the test parsing a file by hand and not the run reporting on
 * itself.
 */
export function refundedTransactionIds(invoiceId: string): string[] {
  const program = [
    "import asyncio, json",
    "from pathlib import Path",
    "from opspilot.adapters.tools.mcp_gateway import MCPToolGateway, build_in_process_servers",
    "INVOICE = " + JSON.stringify(invoiceId),
    "async def main():",
    "    gw = MCPToolGateway(servers=build_in_process_servers(Path('/data/mcp')))",
    "    rows = await gw.call_tool('billing.list_transactions', {'invoice_id': INVOICE})",
    "    txns = (rows.result or {}).get('transactions', [])",
    "    print(json.dumps([t['transaction_id'] for t in txns if t.get('status') == 'refunded']))",
    "asyncio.run(main())",
  ].join("\n");

  const out = execFileSync(
    "docker",
    ["compose", "exec", "-T", "worker", "python", "-c", program],
    {
      cwd: REPO_ROOT,
      encoding: "utf-8",
      env: { ...process.env, OPSPILOT_OPERATOR_TOKEN: THROWAWAY_TOKEN, ...PROVIDER_ENV },
    },
  );
  const line = out.trim().split("\n").pop() ?? "[]";
  return JSON.parse(line) as string[];
}
