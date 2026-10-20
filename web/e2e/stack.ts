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
 * `web/test-results/compose-logs/<spec>/` -- the directory Playwright already
 * uploads on failure, and which `web/.gitignore` already excludes, one
 * subdirectory per spec so the two captures cannot overwrite each other (see
 * `composeDiagnosticsDir`). The capture is unconditional rather than
 * failure-gated: the code path that most needs it (a failing test) is precisely
 * the one where a conditional written by the same person who just got the
 * failure wrong is worth least, the files are a few KB of a directory nobody
 * commits, and gating on the test's status would change nothing CI stores --
 * `web-e2e` uploads this tree under `if: failure()` anyway. The workflow still
 * gets its own `compose ps -a` / `compose logs` step, because a failure inside
 * `beforeAll` leaves `stack === null`, skips teardown entirely, and leaves live
 * containers that only the workflow can see.
 *
 * `--wait` is why the condition is hard to see in the first place. It returns
 * as soon as every healthchecked service is healthy and every one-shot has
 * exited -- and the worker has **no healthcheck** (docker-compose.yml says why:
 * it serves no HTTP endpoint, so there is nothing to probe). Measured on this
 * machine, `docker compose up -d --wait` returned with the worker container
 * reporting `Up 1 second`: `--wait` is satisfied by "the container started",
 * which is several seconds short of "the process is polling". Worse, it is
 * satisfied by a worker that is crash-looping: a container whose command was
 * overridden to print its boot line and then `exit 1` was reported by
 * `docker compose up -d --wait` as **`Healthy`** while `docker compose ps -a`
 * showed `Restarting (1)`. Nothing in the run distinguishes those states, which
 * is why a worker that came up and then sat there reads exactly like a worker
 * that is merely slow. `waitForWorkerPolling` below is what closes that gap, by
 * asking the worker itself *and* Docker -- the log alone cannot, because the
 * log is append-only and a dead attempt's boot line is still in it.
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

/**
 * The `web/` directory (this file lives in `web/e2e/`).
 *
 * Exported because a spec has to name a file in the tree the image is built
 * from: `web-service.spec.ts` compares the bytes the browser receives with
 * the committed `web/src/app/favicon.ico`, and "the `web/` directory" is a
 * fact with one definition here rather than a path each spec re-derives.
 */
export const WEB_DIR = path.join(REPO_ROOT, "web");

/** The `web` service's own published URL. The dashboard, containerised. */
export const WEB_SERVICE_URL = process.env.E2E_WEB_SERVICE_URL ?? "http://localhost:3000";

/**
 * The port Compose must publish for the containerised dashboard.
 *
 * Read out of `WEB_SERVICE_URL` rather than assumed, so the URL this module
 * probes and the port `docker-compose.yml` binds are the same fact. Compose
 * publishes a literal `127.0.0.1:3000:3000` -- the host interface is named
 * rather than left to Docker's `0.0.0.0` default, so the dashboard is
 * loopback-only and this suite's `http://localhost:3000` still reaches it --
 * so an operator who moves this one with `E2E_WEB_SERVICE_URL` has to move the
 * compose mapping too, which the pre-flight below turns into a named error
 * instead of a container that never becomes healthy.
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
 *
 * `label` is required and not optional, and it names this spec's diagnostics
 * capture. It is a parameter rather than something inferred from the caller so
 * that a spec cannot bring up a stack and silently contribute an unattributable
 * set of logs -- see `composeDiagnosticsDir` for what that costs when it goes
 * wrong.
 */
export async function startStackOrSkip(label: DiagnosticLabel): Promise<StackHandle> {
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
      // After it there is nothing left to ask. Into this spec's OWN directory:
      // `web-service.spec.ts` runs after this one, tears down the same Compose
      // project, and used to write over these files with the output of a stack
      // that has no worker in it.
      writeComposeDiagnostics(env, label);
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
 *
 * `label` is this spec's own name, for the same reason `startStackOrSkip`'s is:
 * it is the directory its capture is written to, and this spec's stack has no
 * worker, so letting it write into a shared one would erase the golden path's.
 */
export async function startContainerisedWebOrSkip(label: DiagnosticLabel): Promise<StackHandle> {
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
      // And into a DIFFERENT directory from the golden path's, which is the
      // other half of that sentence: it tears the containers down, so it must
      // not also overwrite the first capture's account of them.
      writeComposeDiagnostics(env, label);
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

/** What Docker says about the worker's container. Read, never asserted on directly. */
export type WorkerContainerState = {
  readonly found: boolean;
  readonly status: string;
  readonly restarting: boolean;
  readonly exitCode: number;
  readonly restarts: number;
};

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
function workerContainerState(env: NodeJS.ProcessEnv): WorkerContainerState {
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
 * The worker's state in one line, for a human reading a CI log.
 *
 * One definition, because this string now appears in four places that must not
 * drift apart -- the two `waitForWorkerPolling` failures, `workerReport`'s
 * header, and `assessWorker`'s return value. A reader comparing a thrown error
 * against an attached report is comparing two renderings of the same fact, and
 * the fact is worthless if only one of them names the restart count.
 */
export function describeWorkerState(state: WorkerContainerState): string {
  if (!state.found) {
    return "no worker container (compose reports no worker container at all)";
  }
  return (
    `container ${state.status}` +
    (state.restarting ? ", RESTARTING" : "") +
    ` (exit code ${state.exitCode}, ${state.restarts} restart(s))`
  );
}

/** The boot line `_run` prints once, immediately before `poll_forever`. */
const BOOTED = /opspilot-worker\[.*]: booted;/;

/**
 * The worker's verdict, given what its log says and what Docker says about it.
 *
 * **The two are read together, and the container's state is consulted even when
 * the log has already said yes.** That is the whole point of this function
 * existing separately from the poll loop.
 *
 * The log is per-container and append-only, so a worker that booted once and
 * then died in a `restart: unless-stopped` loop still has its boot line
 * sitting in `compose logs worker` -- every attempt's output is retained.
 * Deciding on the log alone therefore returns success for exactly the worker
 * the caller is trying to rule out, and the `restarts > 0` clause below becomes
 * unreachable on that shape: it sits behind an early `return`, on the branch only
 * a *silent* worker reaches. `waitForWorkerPolling`'s docstring claimed the
 * clause closed the hole. It did not -- the code and the docstring disagreed,
 * and the code was the one the suite ran. That was measured, not argued: against
 * a worker whose command was overridden to print its boot line and then `exit 1`,
 * `BOOTED.test(logs)` was `true`, the container read `restarting` with 6 restarts,
 * and the gate passed.
 *
 * So the order is: **container state first, boot line second.** A boot line is
 * necessary but not sufficient. A container Docker has not reported as absent,
 * restarting, exited, dead, or restarted at least once is a precondition for
 * counting as polling, and the boot line is what then says the process inside it
 * got as far as `poll_forever`. The deny-list is a deny-list rather than a
 * positive `status === "running"` on purpose: a container Docker has only just
 * created is transient rather than broken, and failing on a shape that would
 * have settled on its own is the other way to be wrong.
 *
 * The two failures are distinguished rather than merged, because they mean
 * different things to whoever reads them:
 *
 *   * `unhealthy_container` -- the container itself cannot be a polling worker
 *     (absent, restarting, exited, dead, or restarted at least once). Fail
 *     immediately: this will not settle on its own, and waiting out a deadline
 *     to learn what the first second already said is how this failure got
 *     misdiagnosed once already.
 *   * `silent` -- a running container with a clean restart count that never
 *     printed the boot line. The log is the answer, and the caller decides how
 *     long to keep asking.
 */
export function assessWorker(
  logs: string,
  state: WorkerContainerState,
): { readonly polling: true } | {
  readonly polling: false;
  readonly cause: "unhealthy_container" | "silent";
  readonly stateLine: string;
} {
  const stateLine = describeWorkerState(state);
  const containerCanPoll =
    state.found &&
    !state.restarting &&
    state.status !== "exited" &&
    state.status !== "dead" &&
    state.restarts === 0;
  if (!containerCanPoll) {
    return { polling: false, cause: "unhealthy_container", stateLine };
  }
  if (!BOOTED.test(logs)) {
    return { polling: false, cause: "silent", stateLine };
  }
  return { polling: true };
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
  return [
    `--- worker container: ${describeWorkerState(state)}`,
    "--- worker logs (last 200 lines):",
    workerLogs(env),
  ].join("\n");
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
 * is the worker process dying. Both callers here hand this function a fresh
 * container -- `startStackOrSkip` after `down -v` + `up`, and `recreateWorker`
 * after `--force-recreate` -- so a non-zero count means exactly one thing and
 * never a recycled container from a previous run.
 *
 * **The state is read before the log, on purpose.** The earlier version of this
 * loop tested `BOOTED.test(logs)` first and returned on a match, which left
 * every check below it -- `restarts > 0` among them -- unreachable whenever the
 * boot line was present. Because the log is append-only, that is the *crash-loop
 * case*: a worker that booted and then died keeps its first boot line forever,
 * so the guard documented above was documented but not implemented. The
 * decision itself lives in `assessWorker`, which is a pure function of (log,
 * state) precisely so it can be tested against that shape without Docker --
 * `stack-diagnostics.spec.ts` does.
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
  for (;;) {
    // The container's state is read first and unconditionally. The log alone
    // cannot distinguish a healthy worker from one that booted and then died,
    // because the boot line of a dead attempt is still in an append-only log.
    const state = workerContainerState(env);
    const verdict = assessWorker(workerLogs(env, 50), state);
    if (verdict.polling) {
      return;
    }
    if (verdict.cause === "unhealthy_container") {
      throw new Error(
        `the worker is not polling: ${verdict.stateLine}. A boot line in its log does ` +
          `not rescue it -- a container's log is append-only, so a worker that booted and then ` +
          `crash-looped still carries its FIRST boot line there, and a gate that trusted the log ` +
          `without also reading the container passed exactly that worker through. ` +
          `No run submitted from here could ever have been claimed by a worker in that state -- ` +
          `which is the condition CI run 38202887580 reported as a run stuck at RECEIVED with no ` +
          `steps, and it should be treated as the same failure until the log says otherwise.\n` +
          `${diagnoseWorker(env, verdict.stateLine)}`,
      );
    }
    if (Date.now() >= deadline) {
      throw new Error(
        `the worker did not report itself polling within ${Math.round(timeoutMs / 1000)}s. It is ` +
          `running (${verdict.stateLine}) but never printed a boot line, so no run submitted from here ` +
          `could have been claimed. A running container is all \`docker compose up --wait\` was ever able to ` +
          `check -- it is not enough.\n${diagnoseWorker(env, verdict.stateLine)}`,
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
 * The specs that capture compose diagnostics, and the directory each one gets.
 *
 * A closed union rather than a free string, because the capture is only useful
 * if its path says whose it is, and a free string lets a new spec pick a name
 * nobody will guess and never be corrected. Adding a spec is a one-line change
 * here; that is the price of a capture a reader can attribute from the artifact
 * listing alone.
 */
export type DiagnosticLabel =
  | "golden-path"
  | "failure-states"
  | "web-service"
  | "claim-lease"
  | "i18n";

/** The directory `label`'s capture goes in, relative to `web/`. */
const DIAGNOSTICS_SUBDIR = ["test-results", "compose-logs"] as const;

/**
 * Every service this repository's compose file declares, including `web`.
 *
 * A failure in `web-service.spec.ts` is as unexplained without the `web` log as
 * one in the golden path is without the worker's, and asking a service that was
 * never started costs one empty file. Exported so the capture's file list is
 * one definition the tests can assert on, rather than a literal repeated here.
 */
export const DIAGNOSTIC_SERVICES = ["postgres", "migrate", "api", "worker", "web"] as const;

/**
 * Where `label`'s capture goes: `web/test-results/compose-logs/<label>/`.
 *
 * **One directory per capture, named for the spec that wrote it.** The bug this
 * replaces wrote six fixed filenames (`worker.log`, `ps.txt`, …) into one shared
 * directory that two specs both wrote to. Playwright runs the files in name
 * order, `web-service.spec.ts` sorts last, and its stack has no worker -- so on
 * every full run, passing or failing, the golden path's `worker.log` was
 * overwritten with `(no output)` and `ps.txt` with a listing that had no worker
 * row in it. The one file that would settle a real worker failure was destroyed
 * by a spec that had nothing to say about the worker.
 *
 * The subdirectory is what makes it work, and it is below `test-results/` on
 * purpose: the `web-e2e` job uploads `web/test-results/` wholesale with
 * `if: failure()`, so anything that reads a path-first reader (a GitHub artifact
 * browser, `ls`, a grep) sees `compose-logs/golden-path/worker.log` next to
 * `compose-logs/web-service/worker.log` and knows which stack each belongs to
 * without opening either.
 *
 * Pure and exported because the path is the whole defect: it is testable with
 * no Docker, no containers and no filesystem, by asking two labels for their
 * directories and checking they differ and land where the upload looks.
 *
 * `label` is constrained by `DiagnosticLabel` to the literal names above, which
 * is why there is no sanitising here -- a value that cannot contain a separator
 * or a `..` cannot escape the directory it is joined to.
 */
export function composeDiagnosticsDir(label: DiagnosticLabel): string {
  return path.join(WEB_DIR, ...DIAGNOSTICS_SUBDIR, label);
}

/**
 * Write the whole stack's own logs to `web/test-results/compose-logs/<label>/`.
 *
 * Called from both teardowns, immediately before `docker compose down -v`, with
 * the calling spec's own name. Two calls therefore cannot overwrite each other:
 * see `composeDiagnosticsDir` for the measurement that showed they did.
 *
 * **Unconditional -- on success as well as on failure -- and the reason is not
 * the one usually given.** The attractive argument is comparison: a later red
 * run wants the passing run's context beside it. That argument does not survive
 * contact with CI, where `web-e2e` uploads `web/test-results/` under
 * `if: failure()` -- a green run's capture is never uploaded, and the next
 * `npm run e2e` clears `test-results/` before it starts, so locally it lasts
 * until the next run and nowhere else. What actually decides it is simpler:
 * gating the write on the test's status would change *nothing CI stores* (the
 * upload is already failure-gated) while adding a condition to the one piece of
 * code that runs when the run is going wrong. `afterAll` executes the same way
 * whether the body passed or failed, the containers are gone a second later
 * either way, and a teardown whose correctness depends on a status flag is a
 * teardown that can be wrong exactly when it matters. The cost of not gating is
 * a few KB in a gitignored directory (`web/.gitignore`).
 *
 * `test-results/` is where Playwright already puts the trace and the screenshot
 * and is already uploaded on failure. Writing here means the worker's log
 * travels with the artefact a reader is already going to open, rather than in a
 * second place they have to know to look.
 *
 * Best-effort throughout: this runs inside `afterAll`, where a thrown error
 * masks the test failure that actually mattered.
 */
function writeComposeDiagnostics(env: NodeJS.ProcessEnv, label: DiagnosticLabel): void {
  const dir = composeDiagnosticsDir(label);
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, "ps.txt"), composeOutput(["ps", "-a"], env), "utf-8");
    for (const service of DIAGNOSTIC_SERVICES) {
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

// ---------------------------------------------------------------------------
// Changing the stack while a spec is running: stop the worker, bring it back
// with a deployment knob overridden.
//
// WHY A SPEC NEEDS THIS
// --------------------
// Some states an operator must be able to read are ones the golden path's own
// configuration cannot produce. `failure-states.spec.ts` needs a run the policy
// engine *refused* -- and the golden script's one refund ($129.00) is under no
// ceiling, so gate 4 waves it through to the approval gate like everything else.
// Reaching the refusal means running the worker with `OPSPILOT_REFUND_CEILING`
// set, which is a deployment knob Compose already reads from the environment
// (`docker-compose.yml`: `OPSPILOT_REFUND_CEILING: ${OPSPILOT_REFUND_CEILING:-}`).
// Recreating that one container changes the deployment without rebuilding
// postgres, the api, the image or the dashboard -- seconds, not a second full
// stack bring-up.
//
// These live here rather than in that spec for one reason: `OPSPILOT_OPERATOR_TOKEN`
// has exactly one value in this suite. A spec that ran Compose without it would
// either render the file with a second token or fail the `:?` guard, and the first
// is the "diagnostic rendered with a different environment than the one it is
// diagnosing" hazard `stackEnv`'s own comment is about.
// ---------------------------------------------------------------------------

/**
 * The environment a spec's own `docker compose` calls must run with.
 *
 * `stackEnv()` plus overrides. Exported rather than reconstructed so the operator
 * token stays a single definition (`THROWAWAY_TOKEN`, above) and a diagnostic
 * cannot be rendered against an environment the stack was not started with.
 */
export function composeEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  return { ...stackEnv(), ...extra };
}

/**
 * Stop the worker container and leave the rest of the stack up.
 *
 * The api, the database and the dashboard keep serving while this holds, which is
 * the point: what an operator sees with the worker down is a run the worker never
 * claimed -- a screen with a real, explainable state on it, not an error page.
 *
 * Throws rather than returning a boolean. A stopped worker changes what every
 * later assertion means, so a failure to stop it must stop the spec rather than
 * quietly leave it driving a stack that is not the one the test thinks it is.
 */
export function stopWorker(): void {
  const stopped = spawnSync("docker", ["compose", "stop", "worker"], {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env: stackEnv(),
  });
  if (stopped.error || stopped.status !== 0) {
    throw new Error(
      "`docker compose stop worker` failed (see the output above). This spec asserts on " +
        "what the dashboard shows while the worker is down, and it cannot do that with the " +
        "worker still polling.",
    );
  }
}

/**
 * Recreate the worker with deployment knobs overridden, and wait until it says it
 * is polling again.
 *
 * `--force-recreate` rather than `restart`, because a restart reuses the container
 * and with it the environment it was created with -- which is the opposite of what
 * an override is for. The container is new, so its `RestartCount` starts at zero
 * and `waitForWorkerPolling` below can still tell a healthy worker from one that
 * crashed and came back.
 *
 * The boot line is waited for for the same reason `startStackOrSkip` waits for it:
 * `up -d` returning says the container was created, not that the process inside it
 * reached `poll_forever`. A worker recreated with a knob it then rejects would
 * otherwise be indistinguishable from one that came up.
 */
export async function recreateWorker(extra: Record<string, string> = {}): Promise<void> {
  const env = composeEnv(extra);
  const up = spawnSync("docker", ["compose", "up", "-d", "--force-recreate", "worker"], {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env,
  });
  if (up.error || up.status !== 0) {
    throw new Error(
      "`docker compose up --force-recreate worker` failed (see the output above). The stack " +
        "is otherwise up, so this is a failure rather than a skip.",
    );
  }
  await waitForWorkerPolling(env);
}

/**
 * The marker the claim program below prints its JSON on.
 *
 * A prefix rather than "the last line of stdout", because the api container's
 * Python may emit warnings (log configuration, a DeprecationWarning) on the way
 * out and a parse that trusted the last line would read one of those as the
 * claim. `parse` skips everything that does not carry it and fails loudly if no
 * line does, which is the difference between "the claim did not happen" and
 * "the claim happened and the harness misread it".
 */
const CLAIM_MARKER = "@@opspilot-claim@@";

/** One claimed run, as `SqlRunStore.claim_next` returned it. */
export type ClaimedRun = {
  readonly id: string;
  readonly claimed_by: string | null;
  readonly claimed_until: string | null;
  readonly status: string;
};

/**
 * Claim the oldest claimable run by calling `SqlRunStore.claim_next` itself.
 *
 * WHY THIS RUNS PYTHON INSTEAD OF AN UPDATE
 * -----------------------------------------
 * The state under test is a run that is *claimed and not driven*: `received`, no
 * steps, a lease whose deadline is in the future. The production code that
 * produces it is `claim_next`, so this calls it — same method, same transaction,
 * same columns (`adapters/persistence/repositories.py`), from the `api`
 * container, which holds the same `DATABASE_URL` the worker does. A hand-written
 * `UPDATE agent_runs SET claimed_until = ...` would be shorter and would test a
 * state no code in this repository can reach.
 *
 * **And it cannot be reached by a live worker**, which is why a stand-in is
 * needed at all. `drain_once` claims and then drives: the very next thing it does
 * is `set_status(CLASSIFYING)`, which writes a `state_change` step in the same
 * transaction. So a run with a live lease and zero steps exists for a few
 * milliseconds between two database round trips, and catching it means polling
 * the row faster than the worker can commit — a race, not a test. `docs/
 * limitations.md` §2 measured the window that *does* persist (300 s) by writing
 * the lease from outside the worker; this is that measurement, made repeatable.
 *
 * The `workerId` is a stand-in and the caller says so, because the field under
 * test includes *who*: the dashboard names the holder, and a worker that is not
 * there must not be able to produce a lease that reads as `worker-compose`'s.
 *
 * **The worker must be stopped** (`stopWorker()`) while this runs, or its next
 * poll claims the same row. That is not a caveat — it is the precondition the
 * two callers hold, and `claim_next` returning the *wrong* run is caught by
 * comparing the returned id with the one the caller submitted.
 *
 * Returns `null` when nothing was claimable, which is a real answer (the queue
 * was empty) and is why the return type says so rather than throwing.
 */
export function claimOldestRunThroughTheRepository(options: {
  workerId: string;
  leaseSeconds: number;
}): ClaimedRun | null {
  const program = [
    "import asyncio, json",
    "from opspilot.adapters.persistence import db",
    "from opspilot.adapters.persistence.repositories import SqlRunStore",
    "from opspilot.settings import get_settings",
    `WORKER = ${JSON.stringify(options.workerId)}`,
    `LEASE = ${JSON.stringify(options.leaseSeconds)}`,
    "async def main():",
    "    factory = db.session_factory(get_settings())",
    "    run = await SqlRunStore(factory).claim_next(worker_id=WORKER, lease_seconds=LEASE)",
    "    payload = None if run is None else {",
    "        'id': str(run.id),",
    "        'claimed_by': run.claimed_by,",
    "        'claimed_until': run.claimed_until.isoformat() if run.claimed_until else None,",
    "        'status': run.status.value,",
    "    }",
    `    print(${JSON.stringify(CLAIM_MARKER)} + json.dumps(payload))`,
    "asyncio.run(main())",
  ].join("\n");

  const out = execFileSync("docker", ["compose", "exec", "-T", "api", "python", "-c", program], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    env: stackEnv(),
  });
  const line = out
    .split("\n")
    .map((candidate) => candidate.trim())
    .filter((candidate) => candidate.startsWith(CLAIM_MARKER))
    .pop();
  if (line === undefined) {
    throw new Error(
      "the claim program printed no result line, so whether it claimed anything is unknown.\n" +
        `It was given:\n${program}\n\nand printed:\n${out}`,
    );
  }
  const parsed = JSON.parse(line.slice(CLAIM_MARKER.length)) as ClaimedRun | null;
  if (parsed !== null && parsed.claimed_by !== options.workerId) {
    throw new Error(
      `the claim wrote claimed_by=${String(parsed.claimed_by)}, not the ${options.workerId} this ` +
        "test asked for, so the page's 'held by' text is not being tested against the holder it " +
        "claims. Either the worker container is still polling and took the run first, or the " +
        "repository is not writing the column this helper reads.",
    );
  }
  return parsed;
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
