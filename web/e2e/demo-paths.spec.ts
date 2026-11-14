/**
 * The three demo paths, walked in a browser on a DISPOSABLE stack.
 *
 * ## What this file is for
 *
 * `docs/phase3-plan.md` §M2-04 asks for three browser demonstration paths, each
 * with seed data, start/reset instructions and a test, and -- the hard
 * constraint -- **never resetting by touching the existing user-data volumes**.
 * This file is the tests half. The walkthrough half lives in
 * `docs/demo-paths.md`, and the reset script half in `scripts/demo_paths.py`;
 * all three describe the same stack this file starts.
 *
 * The paths:
 *
 *   * **A -- approve:** a duplicate charge is investigated, the policy is
 *     cited, the refund parks on the approval gate, a human approves, and the
 *     simulated refund executes exactly once, with the audit trail to show for
 *     it.
 *   * **B -- reject:** the same investigation parks, a human REJECTS, and the
 *     run completes with an escalation reply and **no refund**. A rejection is
 *     the workflow working (`docs/agent-state-machine.md` §3), so the terminal
 *     state is `COMPLETED`, not `FAILED` -- and the server-side assertion is
 *     that no money moved.
 *   * **C -- model failure:** the model provider answers 429, the run fails
 *     LOUDLY with `model_unavailable` (never silently, never a retry loop --
 *     the provider SDK is configured `max_retries=0`), and the queue keeps
 *     working: the next ticket is claimed and completes after the worker is
 *     restored.
 *
 * ## THE STACK IS NOT THE ONE THE OTHER SPECS USE
 *
 * `startStackOrSkip` here is given a `StackConfig` whose compose project is
 * `opspilot-demo-test` -- a project the desktop application never uses -- with
 * its own published ports (api 18000, postgres 15432, dashboard 3101). Teardown
 * runs `docker compose down -v` against THAT project name, so it removes
 * exactly the volumes this walkthrough created and cannot reach the `opspilot`
 * volumes the desktop application owns. The demo data itself is not seeded by
 * this file at all: the MCP servers seed themselves from the committed
 * `seed.json` on a fresh volume, which is the property `scripts/demo_paths.py`
 * relies on when it destroys and recreates the stack for a human.
 *
 * ## How the model is scripted, and why the worker is recreated between paths
 *
 * All three paths run on `MODEL_PROVIDER=fake`, which replays a scenario's
 * recorded calls **in order, with a cursor per method, inside the worker
 * process**. One worker can therefore drive exactly one full golden run: a
 * second run finds the cursor past the end and dies with
 * `UnmatchedFixtureError`, which `drain_once` deliberately does not catch.
 * Rather than change that (the fake's strictness is the point, `docs/risks.md`
 * R2), this file recreates the worker container before each path -- each
 * recreation is a fresh process with a fresh cursor, and `recreateWorker`
 * already waits for the new worker to report itself polling.
 *
 * Path C's failure is injected the same way the deployment would see it: the
 * worker is recreated with `MODEL_PROVIDER=anthropic` and `ANTHROPIC_BASE_URL`
 * pointing at a throwaway HTTP container on the same compose network that
 * answers every request with **429**. The anthropic adapter builds its client
 * with `max_retries=0` (the M1-02 decision), so the SDK raises
 * `RateLimitError` on the first call, the worker classifies it
 * (`rate_limit` -> `model_unavailable`), marks THIS run failed, and moves on.
 * No `src/` code is touched, and no gate is relaxed -- the failure is a
 * deployment knob, exactly as a real outage would be.
 */

import { spawnSync } from "node:child_process";

import { expect, test, type Page } from "@playwright/test";

import {
  REPO_ROOT,
  recreateWorker,
  refundedTransactionIds,
  startStackOrSkip,
  workerReport,
  type StackConfig,
  type StackHandle,
} from "./stack";

/**
 * The demo stack's coordinates. Every value is chosen to be disjoint from the
 * `opspilot-e2e` project the other browser specs use AND from the `opspilot`
 * project the desktop application uses, so this file can run beside either
 * without fighting it for containers, volumes or ports.
 */
const DEMO_CONFIG: StackConfig = {
  composeProject: "opspilot-demo-test",
  apiHostPort: 18000,
  dashboardPort: 3101,
  postgresHostPort: 15432,
  fakeScenario: "duplicate_charge",
};

/** Where THIS file's dashboard lives (the browser is pointed here via `test.use`). */
const DEMO_BASE_URL = `http://localhost:${DEMO_CONFIG.dashboardPort}`;

/** The throwaway 429 server's container name, on the demo compose network. */
const FAIL_429_CONTAINER = "opspilot-demo-test-429";

/** The seeded duplicate charge: the second of the two charges for INV-2026-384. */
const REFUNDED_TRANSACTION = "TX-88219";

let stack: StackHandle | null = null;

// The dashboard this spec drives is the one DEMO_CONFIG started, not the
// config-level default. `test.use` overrides `baseURL` for every test in this
// file, so `page.goto("/tickets")` lands on the demo stack's dashboard.
test.use({ baseURL: DEMO_BASE_URL });

test.beforeAll(async ({ browser }) => {
  // The stack start includes a Docker build and a `next build`; the budget is
  // the build's, for the same reason every other browser spec sets this.
  test.setTimeout(900_000);
  stack = await startStackOrSkip("demo-paths", DEMO_CONFIG);
  if (stack.skip) {
    return;
  }
  // The knowledge base must be reindexed before the first run: the API does
  // not ingest at start-up, so a fresh Postgres holds zero chunks and the run
  // would retrieve nothing and abstain. Clicked, because the control is what
  // a demonstrator uses. `beforeAll` has no `page` fixture, and
  // `browser.newPage()` would use the CONFIG's base URL (the default stack on
  // 3100), so this opens a context pointed at the DEMO dashboard explicitly.
  const context = await browser.newContext({ baseURL: DEMO_BASE_URL });
  const page = await context.newPage();
  try {
    await page.goto("/knowledge");
    await page.getByRole("button", { name: /Reindex knowledge/i }).click();
    await expect(page.getByText(/\d+ seen/)).toBeVisible({ timeout: 60_000 });
  } finally {
    await context.close();
  }
});

test.afterAll(async () => {
  // The 429 container outlives a worker recreation (it is plain `docker compose
  // run`, not a compose service), so it is removed here rather than left
  // behind -- and by name, so a failed path C cannot strand it either.
  remove429Container();
  if (stack !== null && !stack.skip) {
    // Returned, not merely called: teardown waits until the dashboard's port
    // is released. See `golden-path.spec.ts`'s identical `afterAll`.
    await stack.teardown();
  }
});

// ---------------------------------------------------------------------------
// Shared steps -- each one is a click a demonstrator would make, or a read of
// a state the system itself produced. No `fetch` from this file touches the
// API: the browser walkthrough is the artefact under test.
// ---------------------------------------------------------------------------

/** Skip the calling test (with a reason) when the stack is not up. */
function skipUnlessUp(): void {
  if (stack === null) {
    test.skip(true, "the stack did not start");
  } else if (stack.skip) {
    test.skip(true, stack.reason);
  }
}

/** Submit the README's ticket through the form's one-click fill, and return the run id. */
async function submitTicket(page: Page): Promise<string> {
  await page.goto("/tickets");
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();
  const runLink = page.locator('a[href^="/runs/"]').first();
  await expect(runLink).toBeVisible({ timeout: 30_000 });
  const href = await runLink.getAttribute("href");
  expect(href).toMatch(/^\/runs\/[0-9a-f-]{36}$/);
  return href!.replace("/runs/", "");
}

/**
 * Reload the run page until the parked banner appears.
 *
 * Waits on the WORKER, not the browser: a run nothing claims stays at
 * "Received" with no steps, and a timeout here means the worker, which is why
 * the failure carries `workerReport()`.
 */
async function waitUntilParked(page: Page, runId: string): Promise<void> {
  await withWorkerDiagnosis("waiting for the run to park on the approval gate", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${runId}`);
          return (await page.getByText(/Parked\. Nothing has been sent\./).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );
}

/** Reload the run page until the status badge reads `status` (e.g. "Completed"). */
async function waitUntilStatus(page: Page, runId: string, status: string): Promise<void> {
  await withWorkerDiagnosis(`waiting for the run to reach ${status}`, () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${runId}`);
          // The badge is a `<span>`; the same word also appears as a `<dt>`
          // field label, so the span is what distinguishes STATUS from label.
          return (
            (await page.locator("span").filter({ hasText: new RegExp(`^${status}$`) }).count()) > 0
          );
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );
}

/** Run an assertion, and if it fails, append the worker's own account of itself. */
async function withWorkerDiagnosis<T>(what: string, body: () => Promise<T>): Promise<T> {
  try {
    return await body();
  } catch (error) {
    // Read BEFORE `afterAll` tears the stack down; afterwards the containers
    // are gone and the report would say "(no output)" for every service.
    const suffix =
      `\n\n--- why this matters: ${what} waits on the WORKER, not the browser. ---\n` +
      `${workerReport()}\n`;
    if (error instanceof Error) {
      error.message += suffix;
      throw error;
    }
    throw new Error(`${String(error)}${suffix}`);
  }
}

/**
 * Start the throwaway 429 server on the demo compose network.
 *
 * A plain `docker compose run` container from the already-built `api` image:
 * no extra image is pulled, and the container is reachable from the worker by
 * its container name on the project's own network. It answers every POST with
 * a 429 and an Anthropic-shaped error body, which is what the SDK turns into
 * `RateLimitError` -- the same exception a real provider's rate limiter
 * produces, arrived at through the real HTTP path rather than a mock.
 */
function start429Container(): void {
  remove429Container();
  const program = [
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
  ].join("\n");
  // `run -d` from the already-built `api` image, with `--no-deps`: no
  // dependency is started with it, and no service is replaced -- the
  // container is only a network neighbour the worker reaches by name. It is
  // NOT a compose service, so teardown's `down -v` does not remove it; the
  // afterAll above does, by name.
  const started = spawnSync(
    "docker",
    [
      "compose", "-p", String(DEMO_CONFIG.composeProject),
      "run", "-d", "--no-deps", "--name", FAIL_429_CONTAINER, "api",
      "python", "-c", program,
    ],
    { cwd: REPO_ROOT, stdio: "inherit", timeout: 120_000 },
  );
  if (started.error || started.status !== 0) {
    throw new Error("the 429 injection container did not start (see the output above).");
  }
}

/** Remove the 429 container if it exists; best-effort, for teardown. */
function remove429Container(): void {
  spawnSync("docker", ["rm", "-f", FAIL_429_CONTAINER], { stdio: "ignore", timeout: 60_000 });
}

/** The provider env that makes the worker's model calls hit the 429 server. */
function rateLimitedProviderEnv(): Record<string, string> {
  return {
    MODEL_PROVIDER: "anthropic",
    // Any non-empty key: the adapter refuses to build with an empty one, and
    // the 429 server never checks it. This is not a credential.
    ANTHROPIC_API_KEY: "demo-path-throwaway-key",
    // The SDK appends /v1/messages to this base URL; the container name
    // resolves on the demo compose network.
    ANTHROPIC_BASE_URL: `http://${FAIL_429_CONTAINER}:8000`,
  };
}

/** The provider env that restores the fake provider on a given scenario. */
function fakeProviderEnv(scenario: string): Record<string, string> {
  return {
    MODEL_PROVIDER: "fake",
    OPSPILOT_FAKE_SCENARIO: scenario,
    // Blank the real-provider knobs so a restore does not inherit them.
    ANTHROPIC_API_KEY: "",
    ANTHROPIC_BASE_URL: "",
  };
}

// ---------------------------------------------------------------------------
// PATH A -- approve: investigate, cite policy, park, human approves, refund
// executes exactly once.
// ---------------------------------------------------------------------------

test("path A: a human approves the parked refund and the refund executes exactly once", async ({
  page,
}) => {
  skipUnlessUp();

  const runId = await submitTicket(page);
  await waitUntilParked(page, runId);

  // The Approvals inbox is where the decision is made. Exactly one refund is
  // parked on this fresh stack; its card carries the Approve button. The click
  // RELEASES the run to the worker -- it does not execute the refund in the
  // request, which is what the card's own note says.
  await page.goto("/approvals");
  const approve = page.getByRole("button", { name: /^Approve$/ }).first();
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await approve.click();
  // The decided card leaves the pending inbox: the UI-observable proof the
  // approval was recorded.
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible({
    timeout: 30_000,
  });

  // The worker re-enters the gates for the approved call, executes the refund,
  // and completes the run.
  await waitUntilStatus(page, runId, "Completed");

  // The tool call shows as executed, and the reply is a refund, not an
  // escalation.
  await expect(page.getByText("executed", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/Escalated\./)).not.toBeVisible();

  // THE SERVER's answer, not the run's self-report: exactly one transaction
  // for the invoice is refunded, and it is the duplicate (TX-88219). Read
  // through the billing MCP server's store.
  expect(refundedTransactionIds("INV-2026-384")).toEqual([REFUNDED_TRANSACTION]);
});

// ---------------------------------------------------------------------------
// PATH B -- reject: the same park, a human declines, the run completes with an
// escalation reply and NO refund.
// ---------------------------------------------------------------------------

test("path B: a human rejects the parked refund; nothing executes and the run completes", async ({
  page,
}) => {
  skipUnlessUp();

  // The worker that finished path A has exhausted the duplicate_charge
  // script's cursor. Recreate it FIRST, so the fresh worker -- with a fresh
  // cursor -- is the one that claims this ticket. Submitting against the
  // exhausted worker would kill it with UnmatchedFixtureError, and that
  // strictness is the fake's point: the test schedules around it, it does not
  // relax it.
  await recreateWorker();
  const runId = await submitTicket(page);
  await waitUntilParked(page, runId);

  // THE DECISION: reject, on the approvals inbox. The run transitions
  // WAITING_APPROVAL -> RESPONDING (not FAILED) and the same worker composes
  // an escalation reply on its next poll.
  await page.goto("/approvals");
  const reject = page.getByRole("button", { name: /^Reject$/ }).first();
  await expect(reject).toBeVisible({ timeout: 30_000 });
  await reject.click();
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible({
    timeout: 30_000,
  });

  // A rejected refund is the workflow working: the run COMPLETES with the
  // escalation banner. (The fake's recorded reply text is a placeholder; the
  // banner is the system's own statement that no refund was issued.)
  await waitUntilStatus(page, runId, "Completed");
  await expect(page.getByText(/Escalated\./)).toBeVisible();

  // The rejection is recorded as a human decision: the decided card is on the
  // rejected tab, not the pending one.
  await page.goto("/approvals?status=rejected");
  await expect(page.getByText(/Nothing is waiting for a decision\./)).not.toBeVisible();

  // THE SERVER's answer: no money moved. The only refund in the ledger is
  // still path A's.
  expect(refundedTransactionIds("INV-2026-384")).toEqual([REFUNDED_TRANSACTION]);
});

// ---------------------------------------------------------------------------
// PATH C -- model failure: the provider answers 429, the run fails loudly and
// honestly, and the queue keeps working.
// ---------------------------------------------------------------------------

test("path C: a 429 provider fails the run loudly, and the queue continues with the next ticket", async ({
  page,
}) => {
  skipUnlessUp();

  // Inject the failure at the deployment layer: a provider that answers 429.
  // The SDK is configured max_retries=0, so the very first model call of the
  // run raises, and the worker marks THIS run failed and moves on.
  start429Container();
  await recreateWorker(rateLimitedProviderEnv());

  const failedRunId = await submitTicket(page);
  await waitUntilStatus(page, failedRunId, "Failed");

  // The failure names the upstream, not a category: the banner leads with the
  // reason the worker recorded, and the run's own fields repeat it.
  await expect(page.getByText("This run failed: model_unavailable")).toBeVisible();

  // THE QUEUE KEEPS WORKING. The failed run is terminal; the worker is still
  // polling. Remove the 429 server, restore a real (fake-provider) worker on
  // the already_refunded script -- honest here, because path A really did
  // refund TX-88219 -- and submit the next ticket: it must be claimed and
  // complete without needing another approval.
  remove429Container();
  await recreateWorker(fakeProviderEnv("already_refunded"));

  const nextRunId = await submitTicket(page);
  await waitUntilStatus(page, nextRunId, "Completed");

  // The next run completes WITHOUT an approval and WITHOUT an escalation: the
  // already_refunded script detects TX-88219's existing refund and proposes
  // no further write.
  await expect(page.getByText(/Escalated\./)).not.toBeVisible();

  // And the ledger is unchanged by the whole path: still exactly path A's one
  // refund, and no approval is left pending.
  expect(refundedTransactionIds("INV-2026-384")).toEqual([REFUNDED_TRANSACTION]);
  await page.goto("/approvals?status=pending");
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible();
});
