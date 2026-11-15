// Record the M2-05 showcase material from the disposable demo stack.
//
// WHAT THIS SCRIPT PRODUCES
// -------------------------
// Driven against the stack `scripts/demo_paths.py up` brought up (compose
// project `opspilot-demo-test`), it records:
//
//   * a Playwright **video** of each of the three demo paths (A approve,
//     B reject, C model failure), walked by CLICKING, the way
//     `web/e2e/demo-paths.spec.ts` does -- plus
//   * still screenshots of the key screens, saved to
//     `web/public/showcase/`, and
//   * a machine-readable record of which frame came from which path and
//     moment (`docs/assets/showcase-raw/record-manifest.json`), including
//     per-path BEAT timestamps (see below).
//
// The main video is path A, assembled by `scripts/cut_showcase_video.mjs`;
// this script is the capture half.
//
// PACING, AND WHY THE SCRIPT HOLDS ON EACH SCREEN
// -----------------------------------------------
// On a fast machine the whole golden path completes in ~5 seconds, which is
// too quick to read. Each beat therefore HOLDS the page for a presenter-paced
// interval (6-15 s) before moving on. Every frame is still the real UI driven
// by real clicks; the holds only let a viewer read. Each beat's label and
// offset (seconds since the path's recording started) is logged into the
// manifest, which is what the subtitle (.srt) generation is timed against.
//
// WHY A STANDALONE SCRIPT AND NOT THE SPEC ITSELF
// -----------------------------------------------
// `demo-paths.spec.ts` runs under the Playwright *test runner*, whose video
// is `retain-on-failure`-shaped. A showcase recording needs the video as the
// deliverable, with named output files per path and no test semantics. The
// walkthrough logic mirrors the spec's waits/locators so the two cannot drift
// silently.
//
// Run (after `scripts/demo_paths.py up`, with the SAME port overrides):
//     cd web && node ../scripts/record_showcase.mjs --api 18001 --web 3102 --pg 15434
//     node ../scripts/record_showcase.mjs --only A    (just path A + dashboard shot)
//
// Requires: Playwright + its Chromium (installed with `npm ci` in web/), and
// the demo stack up. Reset the stack before each take for pristine seed data:
//     python scripts/demo_paths.py reset --api-port 18001 --web-port 3102 --pg-port 15434

import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync, readdirSync, renameSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const REPO_ROOT = path.resolve(import.meta.dirname, "..");

// Playwright is a dependency of `web/`, not of the repository root -- resolve
// it from there (the same reasoning `scripts/record_demo_gif.mjs` documents).
const require = createRequire(path.join(REPO_ROOT, "web", "package.json"));
const { chromium } = require("@playwright/test");

const OUT_DIR = path.join(REPO_ROOT, "web", "public", "showcase");
const RAW_DIR = path.join(REPO_ROOT, "docs", "assets", "showcase-raw");
const VIDEOS_DIR = path.join(RAW_DIR, "videos");

// Ports follow `scripts/demo_paths.py up --api-port --web-port --pg-port`.
const args = process.argv.slice(2);
const argValue = (flag) => {
  const at = args.indexOf(flag);
  return at >= 0 ? args[at + 1] : undefined;
};
const ONLY = argValue("--only"); // "A" | "B" | "C" | undefined (all)
const API_PORT = argValue("--api") ?? "18000";
const WEB_PORT = argValue("--web") ?? "3101";
const DEMO_BASE_URL = `http://localhost:${WEB_PORT}`;
const API_BASE_URL = `http://localhost:${API_PORT}`;

/** The demo stack's token -- the same one demo_paths.py starts it with. */
const DEMO_TOKEN = "demo-paths-disposable-token";

const manifest = {
  recordedAt: new Date().toISOString(),
  stack: "opspilot-demo-test",
  baseUrl: DEMO_BASE_URL,
  paths: [],
  screenshots: [],
};

function note(entry) {
  if (entry.kind === "path") manifest.paths.push(entry);
  else manifest.screenshots.push(entry);
}

/** The compose env every docker-compose call here needs (token + port map). */
function composeEnv(extra = {}) {
  return {
    ...process.env,
    COMPOSE_PROJECT_NAME: "opspilot-demo-test",
    OPSPILOT_API_HOST_PORT: API_PORT,
    OPSPILOT_WEB_HOST_PORT: WEB_PORT,
    OPSPILOT_PG_HOST_PORT: argValue("--pg") ?? "15432",
    OPSPILOT_OPERATOR_TOKEN: DEMO_TOKEN,
    MODEL_PROVIDER: "fake",
    OPSPILOT_FAKE_SCENARIO: "duplicate_charge",
    EMBEDDING_PROVIDER: "local",
    ...extra,
  };
}

/** The throwaway 429 server (path C), mirroring the spec's `start429Container`. */
function start429Container() {
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
  const started = spawnSync(
    "docker",
    ["compose", "-p", "opspilot-demo-test", "run", "-d", "--no-deps", "--name", "opspilot-demo-test-429", "api", "python", "-c", program],
    { cwd: REPO_ROOT, stdio: "inherit", timeout: 120_000, env: composeEnv() },
  );
  if (started.error || started.status !== 0) throw new Error("the 429 injection container did not start");
}

function remove429Container() {
  spawnSync("docker", ["rm", "-f", "opspilot-demo-test-429"], { stdio: "ignore", timeout: 60_000 });
}

/** Recreate the worker container with the given env overrides (see stack.ts). */
function recreateWorker(extra = {}) {
  const env = composeEnv(extra);
  const up = spawnSync("docker", ["compose", "up", "-d", "--force-recreate", "worker"], {
    cwd: REPO_ROOT, stdio: "inherit", timeout: 900_000, env,
  });
  if (up.error || up.status !== 0) throw new Error("recreateWorker failed");
  // The worker has no healthcheck; wait for its boot line, as the suite does.
  const deadline = Date.now() + 60_000;
  for (;;) {
    const logs = spawnSync("docker", ["compose", "logs", "worker"], {
      cwd: REPO_ROOT, env, timeout: 60_000, encoding: "utf8",
    });
    // The suite's own marker: `opspilot-worker[...]: booted; ...`
    if (logs.status === 0 && /opspilot-worker\[.*\]: booted;/.test(logs.stdout)) break;
    if (Date.now() > deadline) throw new Error("worker did not report polling within 60s");
    sleep(1000);
  }
}

const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

/** Submit the README ticket through the form's one-click fill; return the run id. */
async function submitTicket(page) {
  await page.goto(`${DEMO_BASE_URL}/tickets`);
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();
  const runLink = page.locator('a[href^="/runs/"]').first();
  await runLink.waitFor({ timeout: 30_000 });
  const href = await runLink.getAttribute("href");
  return href.replace("/runs/", "");
}

/** Reload the run page until the parked banner appears. */
async function waitUntilParked(page, runId) {
  await expectPoll(async () => {
    await page.goto(`${DEMO_BASE_URL}/runs/${runId}`);
    return (await page.getByText(/Parked\. Nothing has been sent\./).count()) > 0;
  }, "waiting for the run to park on the approval gate");
}

/** Reload the run page until a status badge reads `status`. */
async function waitUntilStatus(page, runId, status) {
  await expectPoll(async () => {
    await page.goto(`${DEMO_BASE_URL}/runs/${runId}`);
    return (await page.locator("span").filter({ hasText: new RegExp(`^${status}$`) }).count()) > 0;
  }, `waiting for the run to reach ${status}`);
}

async function expectPoll(body, what, timeoutMs = 90_000, intervalMs = 1_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await body()) return;
    if (Date.now() > deadline) throw new Error(`timed out ${what}`);
    await sleep(intervalMs);
  }
}

/**
 * One recorded path.
 *
 * A per-path video directory makes the take->path mapping structural:
 * Playwright names the file itself (randomly) and only flushes it at
 * context.close(), so whatever file appears in videos/<name>/ IS this path's
 * take; it is renamed to videos/<name>.webm right after.
 */
async function recordPath(browser, name, actions) {
  const videoDir = path.join(VIDEOS_DIR, name);
  mkdirSync(videoDir, { recursive: true });
  const context = await browser.newContext({
    baseURL: DEMO_BASE_URL,
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: videoDir, size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  const startedAt = new Date();
  const entry = { name, startedAt: startedAt.toISOString(), beats: [] };
  // `beat` logs the presenter beats: label + offset from the recording start.
  // The subtitle track is generated from exactly these offsets.
  const beat = (label) =>
    entry.beats.push({ label, at: Number(((Date.now() - startedAt.getTime()) / 1000).toFixed(2)) });
  try {
    await actions(page, beat);
    entry.ok = true;
  } catch (error) {
    entry.ok = false;
    entry.error = String(error && error.message || error);
  } finally {
    entry.finishedAt = new Date().toISOString();
    await page.close();
    await context.close(); // flushes the video file
  }
  const takes = readdirSync(videoDir).filter((f) => f.endsWith(".webm"));
  if (takes.length !== 1) {
    throw new Error(`path ${name}: expected exactly one video take, found ${takes.length}`);
  }
  renameSync(path.join(videoDir, takes[0]), path.join(VIDEOS_DIR, `${name}.webm`));
  entry.videoFile = `docs/assets/showcase-raw/videos/${name}.webm`;
  note({ kind: "path", ...entry });
  if (!entry.ok) throw new Error(`path ${name} failed: ${entry.error}`);
}

async function screenshot(page, file, label, noteExtra = {}) {
  const target = path.join(OUT_DIR, file);
  mkdirSync(OUT_DIR, { recursive: true });
  await page.screenshot({ path: target, fullPage: false });
  note({ kind: "screenshot", file: `web/public/showcase/${file}`, label, ...noteExtra });
  console.log(`  screenshot -> ${file} (${label})`);
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  mkdirSync(VIDEOS_DIR, { recursive: true });
  mkdirSync(RAW_DIR, { recursive: true });

  const browser = await chromium.launch();
  try {
    // -- shared establishing shot: the dashboard on the fresh stack ---------
    if (ONLY === undefined || ONLY === "A") {
      const context = await browser.newContext({ baseURL: DEMO_BASE_URL, viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      await page.goto(`${DEMO_BASE_URL}/`);
      await page.getByText(/What needs a human|Dashboard/i).first().waitFor({ timeout: 30_000 });
      await sleep(1500);
      await screenshot(page, "01-dashboard.png", "Dashboard on a freshly brought-up demo stack");
      await context.close();
    }

    // -- PATH A: approve ----------------------------------------------------
    if (ONLY === undefined || ONLY === "A") {
      await recordPath(browser, "A-approve", async (page, beat) => {
        await page.goto(`${DEMO_BASE_URL}/`);
        await sleep(8_000);
        beat("dashboard");

        await page.goto(`${DEMO_BASE_URL}/tickets`);
        await sleep(6_000);
        await page.getByRole("button", { name: /Fill with the README/i }).click();
        await sleep(3_000);
        beat("ticket-filled");
        await page.getByRole("button", { name: /^Submit ticket$/i }).click();
        // The link's href is `/runs/<uuid>`; strip the prefix so the navigations
        // below build `/runs/<uuid>` and not `/runs//runs/<uuid>` (a 404 the
        // park-wait would poll forever -- the bug that cost one take).
        const href = await page.locator('a[href^="/runs/"]').first()
          .waitFor({ timeout: 30_000 }).then(() =>
            page.locator('a[href^="/runs/"]').first().getAttribute("href"));
        const runId = href.replace("/runs/", "");
        await sleep(5_000);
        beat("ticket-submitted");
        await screenshot(page, "02-ticket-submitted.png", "Ticket submitted; the form names the new run");

        // Poll until parked; the run page auto-refreshes, so the timeline
        // fills in live while the recording rolls.
        await waitUntilParked(page, runId);
        await sleep(10_000);
        beat("parked");
        await screenshot(page, "03-run-parked.png", "Run parked at the approval gate (WAITING_APPROVAL)");

        await page.goto(`${DEMO_BASE_URL}/approvals`);
        const approve = page.getByRole("button", { name: /^Approve$/ }).first();
        await approve.waitFor({ timeout: 30_000 });
        await sleep(14_000);
        beat("approval-card");
        await screenshot(page, "04-approval-pending.png", "Approvals inbox: the parked refund with its policy explanation");

        await approve.click();
        await page.getByText(/Nothing is waiting for a decision\./).waitFor({ timeout: 30_000 });
        await sleep(6_000);
        beat("approved");
        await screenshot(page, "05-approval-decided.png", "Decision recorded; the pending inbox is empty");

        await page.goto(`${DEMO_BASE_URL}/runs/${runId}`);
        await sleep(8_000);
        beat("executing");
        await waitUntilStatus(page, runId, "Completed");
        await sleep(14_000);
        beat("completed");
        await screenshot(page, "06-run-completed.png", "Run completed: refund executed exactly once, audit trail shown");

        await page.goto(`${DEMO_BASE_URL}/runs`);
        await sleep(7_000);
        beat("runs-list");
        await screenshot(page, "07-runs-list.png", "Runs list after the golden path");
      });
    }

    // -- PATH B: reject (worker recreated first, fresh fake cursor) ---------
    if (ONLY === undefined || ONLY === "B") {
      await recordPath(browser, "B-reject", async (page, beat) => {
        await recreateWorker();
        const runId = await submitTicket(page);
        await sleep(4_000);
        beat("submitted");
        await waitUntilParked(page, runId);
        await sleep(8_000);
        beat("parked");
        await page.goto(`${DEMO_BASE_URL}/approvals`);
        const reject = page.getByRole("button", { name: /^Reject$/ }).first();
        await reject.waitFor({ timeout: 30_000 });
        await sleep(8_000);
        beat("approval-card");
        await screenshot(page, "08-reject-decision.png", "Path B: the Reject decision on the parked refund");
        await reject.click();
        await page.getByText(/Nothing is waiting for a decision\./).waitFor({ timeout: 30_000 });
        await sleep(5_000);
        beat("rejected");
        await waitUntilStatus(page, runId, "Completed");
        await sleep(10_000);
        beat("escalated");
        await screenshot(page, "09-rejected-escalated.png", "Path B: run completed with the escalation banner; no refund");
        await page.goto(`${DEMO_BASE_URL}/approvals?status=rejected`);
        await sleep(6_000);
        beat("rejected-tab");
        await screenshot(page, "10-rejected-recorded.png", "Path B: the rejection recorded as a human decision");
      });
    }

    // -- PATH C: model failure ---------------------------------------------
    if (ONLY === undefined || ONLY === "C") {
      await recordPath(browser, "C-model-failure", async (page, beat) => {
        start429Container();
        await recreateWorker({ MODEL_PROVIDER: "anthropic", ANTHROPIC_API_KEY: "demo-path-throwaway-key", ANTHROPIC_BASE_URL: "http://opspilot-demo-test-429:8000" });
        const failedRunId = await submitTicket(page);
        await sleep(4_000);
        beat("submitted");
        await waitUntilStatus(page, failedRunId, "Failed");
        await sleep(12_000);
        beat("failed");
        await screenshot(page, "11-model-failure.png", "Path C: the run fails loudly with model_unavailable");
        remove429Container();
        await recreateWorker({ MODEL_PROVIDER: "fake", OPSPILOT_FAKE_SCENARIO: "already_refunded", ANTHROPIC_API_KEY: "", ANTHROPIC_BASE_URL: "" });
        const nextRunId = await submitTicket(page);
        await sleep(4_000);
        beat("next-submitted");
        await waitUntilStatus(page, nextRunId, "Completed");
        await sleep(10_000);
        beat("queue-recovered");
        await screenshot(page, "12-queue-recovered.png", "Path C: the queue keeps working; the next ticket completes");
      });
    }
  } finally {
    remove429Container();
    await browser.close();
    writeFileSync(path.join(RAW_DIR, "record-manifest.json"), JSON.stringify(manifest, null, 2));
  }

  writeFileSync(path.join(RAW_DIR, "record-manifest.json"), JSON.stringify(manifest, null, 2));
  console.log("recording complete");
  console.log(`  screenshots: ${OUT_DIR}`);
  console.log(`  videos:      ${VIDEOS_DIR}`);
  console.log(`  manifest:    ${path.join(RAW_DIR, "record-manifest.json")}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
