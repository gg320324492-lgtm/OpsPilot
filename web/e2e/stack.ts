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
 * * web (the Next.js dashboard)  -- started **on the host** with `next start`,
 *   NOT as the compose `web` service.
 *
 *   This is not a convenience; it is a documented defect in the compose wiring.
 *   `docker-compose.yml` gives the `web` container `NEXT_PUBLIC_API_BASE_URL`
 *   (a build arg, `http://localhost:8000`) for *browser* fetches and
 *   `API_INTERNAL_BASE_URL=http://api:8000` for *server-side* fetches -- but
 *   `web/src/lib/api/client.ts::getApiBaseUrl` reads only
 *   `NEXT_PUBLIC_API_BASE_URL`, for both paths. Inside a Linux container
 *   `localhost:8000` is the container itself, not the host, so every
 *   Server-Component read fails with "Cannot reach the API" (verified:
 *   `docker compose exec web node -e "fetch('http://localhost:8000/health')"`
 *   -> fetch failed, while `http://api:8000/health` -> 200). The compose file's
 *   own comment claims this "works because the port is published", which is true
 *   only from the host. Running the dashboard on the host puts it where
 *   `localhost:8000` really is the published API port, so the browser and the
 *   Server Components agree. The defect is reported in the milestone note and is
 *   deliberately NOT fixed here: it is product wiring, not a test artefact.
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
 * Teardown always runs (`compose down -v`, then the `next start` child is
 * killed), so a failed run leaves no half-up stack holding ports 3000/8000.
 */

import { type ChildProcess, execFileSync, spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

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

/** A disposable token: only to satisfy Compose's `:?` render guard. Not a secret. */
const THROWAWAY_TOKEN = "e2e-disposable-token-not-a-secret";

/** The deterministic provider the golden path runs on. No key, no network model. */
const PROVIDER_ENV: Record<string, string> = {
  MODEL_PROVIDER: "fake",
  OPSPILOT_FAKE_SCENARIO: "duplicate_charge",
  EMBEDDING_PROVIDER: "local",
  RETRIEVAL_MIN_SCORE: "0.22",
};

/** The dashboard's published URL (the host `next start`, port 3000). */
export const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

/** The API's published URL (the compose `api` service, port 8000). */
export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

export type StackHandle =
  | { skip: true; reason: string }
  | { skip: false; teardown: () => void };

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

  const env = { ...process.env, OPSPILOT_OPERATOR_TOKEN: THROWAWAY_TOKEN, ...PROVIDER_ENV };

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

  // The dashboard, on the host, built and run against the published API port.
  // `NEXT_PUBLIC_API_BASE_URL` is read by both the browser bundle (at build
  // time) and the Server Components (at run time), so it is set for both steps.
  const webEnv = { ...env, NEXT_PUBLIC_API_BASE_URL: API_URL, PORT: "3000" };
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

  const child = spawn("npm", ["run", "start"], {
    cwd: WEB_DIR,
    stdio: "inherit",
    env: webEnv,
    shell: process.platform === "win32",
  });

  await waitForHttp(`${BASE_URL}/`);

  return {
    skip: false,
    teardown: () => {
      killChild(child);
      spawnSync("docker", ["compose", "down", "-v"], {
        cwd: REPO_ROOT,
        stdio: "inherit",
        env,
      });
    },
  };
}

/** Terminate the `next start` child, on Windows (`shell: true`) as well as POSIX. */
function killChild(child: ChildProcess): void {
  if (child.pid === undefined) {
    return;
  }
  if (process.platform === "win32") {
    // `npm run start` runs through cmd, so the child is cmd.exe and `next` is a
    // grandchild; killing the pid alone orphans the server and leaves 3000 busy.
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

/**
 * Poll a URL until it answers 2xx/3xx, or throw.
 *
 * A small hand-rolled poller rather than a dependency. The api's readiness is
 * gated by `compose --wait` already; this waits for the *dashboard* on the host
 * to accept connections (and, before that, for the api's `/ready`).
 */
export async function waitForHttp(url: string, timeoutMs = 120_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown = null;
  while (Date.now() < deadline) {
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
