import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the dashboard's browser walkthrough.
 *
 * WHY THIS EXISTS
 * ---------------
 * `docs/milestones.md` §M7 carries one criterion no artefact demonstrated:
 * "The golden path is walkable in a browser start to finish." The GIF
 * (`scripts/record_demo_gif.mjs`) screenshots a *terminal* running
 * `scripts/demo_golden_path.py`, which drives the API and worker in ONE process
 * on SQLite. This config's single spec instead drives the real deployment --
 * api + worker + web + postgres -- through the dashboard's own controls, by
 * clicking, which is the thing the criterion asks for.
 *
 * WHY IT IS NOT `webServer`
 * -------------------------
 * Playwright's `webServer` starts a command and waits for a port. That is the
 * right tool for a `next dev`-only test, but it cannot start Compose, wait for
 * postgres to be healthy AND migrations to have run AND the api's `/ready` to
 * certify AND the worker to poll. The spec owns that lifecycle itself (see
 * `e2e/stack.ts`) because only it can make the "skip cleanly without Docker"
 * decision the repository's Postgres tests make (ADR-0004). A `webServer` block
 * here would either fail hard on a machine without Docker or silently test the
 * wrong tree.
 *
 * ONE WORKER, NO PARALLELISM
 * --------------------------
 * The spec is a single ordered walkthrough of one shared stack; two concurrent
 * workers would submit two tickets into one database. `fullyParallel: false`
 * and `workers: 1` make that structural rather than a comment.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  // The walkthrough waits on a worker poll and a real Chromium; the composed
  // stack is also driven at start-up, which on a cold image build can take
  // minutes. A generous per-test timeout beats a flaky failure.
  timeout: 5 * 60_000,
  expect: { timeout: 15_000 },
  // No retries: a flaky walkthrough that passes on the second try is a lie about
  // the dashboard, and the criterion is about proving a real path works.
  retries: 0,
  reporter: [["list"]],
  use: {
    // The stack publishes the dashboard on :3000 (docker-compose.yml `web`).
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // A failure artefact a human can read is the whole point of an e2e run:
    // without it a red CI run says only "the click did not land".
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The dashboard is a local, single-operator tool over HTTP; no TLS to
    // ignore and cookies are not used, so nothing else is configured here.
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
