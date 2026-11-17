// Retake the README's ten screenshots on the Phase 4 (HeroUI) UI.
//
// WHAT THIS PRODUCES
// ------------------
// docs/assets/screenshots/01..10, the same ten files, the same names, the
// same click order the case study documents — captured on the shipped
// design-token palette instead of the pre-HeroUI chrome. The alt texts in
// README.md must still describe what each picture shows; numbers that are
// run-specific (step counts, ids) are asserted loosely, by shape.
//
// RUN
// ---
// After `python scripts/demo_paths.py up --api-port 18001 --web-port 3102
// --pg-port 15434`:
//     cd web && node ../scripts/retake_readme_screenshots.mjs --api 18001 --web 3102
//
// Everything is driven by CLICKING the way a person would; waits poll the API
// or the page the way web/e2e/golden-path.spec.ts does, so the captures are
// reproducible, not timed guesses.

import { createRequire } from "node:module";
import path from "node:path";

const REPO = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(REPO, "web", "package.json"));
const { chromium } = require("playwright");

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const API = `http://127.0.0.1:${arg("api", "18001")}`;
const BASE = `http://127.0.0.1:${arg("web", "3102")}`;
const TOKEN = "demo-paths-disposable-token";
const OUT = path.join(REPO, "docs", "assets", "screenshots");
const VIEWPORT = { width: 1440, height: 900 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function apiGet(pathname) {
  const res = await fetch(`${API}${pathname}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  return res.json();
}

/** Poll until `fn` returns a truthy value, with a deadline and a label. */
async function pollUntil(fn, label, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  for (;;) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      lastError = error; // a transient API hiccup is retried, not fatal
    }
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${label}${lastError ? ` (last: ${lastError})` : ""}`);
    }
    await sleep(500);
  }
}

async function shot(page, file) {
  await page.screenshot({ path: path.join(OUT, file), fullPage: false });
  console.log(`captured ${file}`);
}

async function submitTicketFromForm(page) {
  await page.goto(`${BASE}/tickets`);
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();
  const link = page.locator('a[href^="/runs/"]').first();
  await link.waitFor({ timeout: 60_000 });
  return (await link.getAttribute("href")).replace("/runs/", "");
}

async function main() {
  const browser = await chromium.launch();
  try {
    // -- 01: the dashboard on a fresh stack --------------------------------
    {
      const page = await browser.newPage({ viewport: VIEWPORT, baseURL: BASE });
      await page.goto(`${BASE}/`);
      await page.getByRole("heading", { name: /^Dashboard$|^总览$/ }).waitFor({ timeout: 60_000 });
      await sleep(1200);
      await shot(page, "01-dashboard.png");
      await page.close();
    }

    // -- 02: knowledge reindexed -------------------------------------------
    {
      const page = await browser.newPage({ viewport: VIEWPORT, baseURL: BASE });
      await page.goto(`${BASE}/knowledge`);
      await page
        .getByRole("button", { name: /Reindex|重建|索引/i })
        .first()
        .click();
      await pollUntil(async () => {
        const body = await apiGet("/api/knowledge");
        const docs = body?.data?.total ?? body?.total;
        return typeof docs === "number" && docs > 0;
      }, "knowledge reindex");
      await sleep(1500);
      await shot(page, "02-knowledge-reindexed.png");
      await page.close();
    }

    // -- 03..10: one golden-path run, walked in order ----------------------
    const page = await browser.newPage({ viewport: VIEWPORT, baseURL: BASE });

    // The demo stack is reset first: the fake provider replays its fixture
    // once per worker process, so the FIRST ticket of a fresh stack is the
    // one that parks and completes; every later ticket fails at classifying.
    // `demo_paths.py reset` reaches only this project's volumes.
    console.log("resetting the demo stack (demo_paths.py reset)...");
    await new Promise((resolve, reject) => {
      const { spawnSync } = require("node:child_process");
      const py = process.platform === "win32"
        ? path.join(REPO, ".venv", "Scripts", "python.exe")
        : path.join(REPO, ".venv", "bin", "python");
      const r = spawnSync(py, ["scripts/demo_paths.py", "reset",
        "--api-port", API.split(":").pop(), "--web-port", BASE.split(":").pop(),
        "--pg-port", "15434"], { cwd: REPO, stdio: "inherit", timeout: 1_800_000 });
      if (r.error || r.status !== 0) reject(new Error("demo reset failed"));
      else resolve();
    });

    // Submit the ticket the screenshots describe: the form, filled and sent.
    // This is the ONLY submission — the fake provider replays its fixture once
    // per worker process, so any second ticket would consume the fixture's
    // final entry as its classification and fail.
    await page.goto(`${BASE}/tickets`);
    await page.getByRole("button", { name: /Fill with the README/i }).click();
    await page.getByRole("button", { name: /^Submit ticket$/i }).click();
    const runLink = page.locator('a[href^="/runs/"]').first();
    await runLink.waitFor({ timeout: 120_000 });
    const runId = (await runLink.getAttribute("href")).replace("/runs/", "");
    console.log(`golden-path run: ${runId}`);
    await sleep(1500);

    // 03: the ticket form, filled and just submitted (the same click that
    // created the run).
    await shot(page, "03-ticket-submitted.png");
    await shot(page, "03-ticket-submitted.png");

    // 04: the accepted screen, naming the run.
    await sleep(800);
    await shot(page, "04-ticket-accepted.png");

    // 05: the run parked at WAITING_APPROVAL, timeline + sources + tool calls.
    await page.goto(`${BASE}/runs/${runId}`);
    await pollUntil(async () => {
      const run = (await apiGet(`/api/runs/${runId}`)) ?? {};
      const body = run.data ?? run;
      return body.status === "waiting_approval";
    }, "run to park at waiting_approval");
    await sleep(2000);
    await shot(page, "05-run-parked-for-approval.png");

    // 06: the approvals inbox with the parked refund and its explanation.
    await page.goto(`${BASE}/approvals`);
    await page.getByText(/billing\.issue_refund/).first().waitFor({ timeout: 60_000 });
    await sleep(1500);
    await shot(page, "06-approval-pending.png");

    // Approve, by clicking the button the way the walkthrough does.
    await page.getByRole("button", { name: /Approve|批准/i }).first().click();
    await sleep(2500);

    // The resumed run asks the fixture for its response entry; a fresh worker
    // process replays the scenario from cursor 0, which is what the resumed
    // run needs (the case study and the showcase recorder both do this).
    console.log("recreating the worker so the resumed run replays from fixture start...");
    await new Promise((resolve, reject) => {
      const { spawnSync } = require("node:child_process");
      const composeEnv = {
        ...process.env,
        COMPOSE_PROJECT_NAME: "opspilot-demo-test",
        OPSPILOT_API_HOST_PORT: API.split(":").pop(),
        OPSPILOT_WEB_HOST_PORT: BASE.split(":").pop(),
        OPSPILOT_PG_HOST_PORT: "15434",
        OPSPILOT_OPERATOR_TOKEN: TOKEN,
        MODEL_PROVIDER: "fake",
        OPSPILOT_FAKE_SCENARIO: "duplicate_charge",
        EMBEDDING_PROVIDER: "local",
      };
      const up = spawnSync(
        "docker",
        ["compose", "-p", "opspilot-demo-test", "up", "-d", "--force-recreate", "worker"],
        { cwd: REPO, stdio: "inherit", timeout: 900_000, env: composeEnv },
      );
      if (up.error || up.status !== 0) reject(new Error("recreateWorker failed"));
      else resolve();
    });

    // 07: the pending inbox is empty once the decision is recorded.
    await page.goto(`${BASE}/approvals`);
    await pollUntil(async () => {
      const approvals = await apiGet("/api/approvals?status=pending");
      const items = approvals?.data?.items ?? approvals?.items ?? [];
      return items.length === 0;
    }, "pending approvals to drain");
    await sleep(1200);
    await shot(page, "07-approval-decided.png");

    // 08: the completed run — refund executed once, audit trail shown.
    await page.goto(`${BASE}/runs/${runId}`);
    await pollUntil(async () => {
      const run = (await apiGet(`/api/runs/${runId}`)) ?? {};
      const body = run.data ?? run;
      return body.status === "completed";
    }, "run to complete");
    await sleep(2000);
    await shot(page, "08-refund-executed-audited.png");

    // 09: the runs list afterwards.
    await page.goto(`${BASE}/runs`);
    await sleep(1500);
    await shot(page, "09-runs-list.png");

    // 10: the approvals record (Approved tab) with the decided row.
    await page.goto(`${BASE}/approvals`);
    await sleep(1500);
    await shot(page, "10-approval-recorded.png");

    await page.close();
    console.log(`\nrun id used for 04–08: ${runId}`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
