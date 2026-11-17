// Record docs/assets/golden-path.gif from the browser walkthrough of the real
// two-process demo stack — the same screens the README's ten screenshots show,
// in the same order, on the shipped (HeroUI) design.
//
// WHY THIS EXISTS ALONGSIDE record_demo_gif.mjs
// ---------------------------------------------
// record_demo_gif.mjs films scripts/demo_golden_path.py through ttyd: one
// process on SQLite, printed to a terminal. That is the GIF the README has
// carried since M9. The Phase 4 re-skin changed the dashboard, not the
// terminal program, so the terminal recording is still "current" — but the
// README now sits between a browser walkthrough (ten new screenshots) and a
// terminal GIF whose on-screen chrome never changed. This script records the
// BROWSER walkthrough instead: the same fake-provider scenario, the same click
// order, the same approval and the same once-only refund, on the real
// two-process stack, at the same size class as the old GIF.
//
// Run (after `python scripts/demo_paths.py up --api-port 18001 --web-port
// 3102 --pg-port 15434`):
//     cd web && node ../scripts/retake_gif_from_stack.mjs --web 3102
//
// Requires ffmpeg on PATH.

import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import path from "node:path";

const REPO = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(REPO, "web", "package.json"));
const { chromium } = require("playwright");

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const BASE = `http://127.0.0.1:${arg("web", "3102")}`;
const API = `http://127.0.0.1:${arg("api", "18001")}`;
const TOKEN = "demo-paths-disposable-token";
const OUT_GIF = path.join(REPO, "docs", "assets", "golden-path.gif");
const WORK = path.join(REPO, ".opspilot", "tmp", "opspilot-gif-stack");
const FRAMES = path.join(WORK, "frames");
const VIEWPORT = { width: 1440, height: 900 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function apiGet(pathname) {
  const res = await fetch(`${API}${pathname}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  return res.json();
}

async function pollUntil(fn, label, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  for (;;) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${label}${lastError ? ` (last: ${lastError})` : ""}`);
    }
    await sleep(500);
  }
}

function sh(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} exited ${r.status}`);
}

rmSync(WORK, { recursive: true, force: true });
mkdirSync(FRAMES, { recursive: true });
mkdirSync(path.dirname(OUT_GIF), { recursive: true });

const browser = await chromium.launch({ args: ["--no-sandbox"] });
let n = 0;
const FRAMES_KEPT = [];

async function frame(page) {
  const file = path.join(FRAMES, `f${String(n++).padStart(4, "0")}.png`);
  await page.screenshot({ path: file });
  FRAMES_KEPT.push(file);
}

/** Hold a beat, capturing a frame every ~700 ms so the GIF reads at 4 fps. */
async function hold(page, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await frame(page);
    await sleep(700);
  }
}

try {
  const page = await browser.newPage({ viewport: VIEWPORT, baseURL: BASE });

  // 1 — dashboard on the fresh stack
  await page.goto(`${BASE}/`);
  await page.getByRole("heading", { name: /^Dashboard$|^总览$/ }).waitFor({ timeout: 60_000 });
  await hold(page, 3000);

  // 2 — knowledge reindexed
  await page.goto(`${BASE}/knowledge`);
  await page.getByRole("button", { name: /Reindex|重建|索引/i }).first().click();
  await pollUntil(async () => {
    const body = await apiGet("/api/knowledge");
    const docs = body?.data?.total ?? body?.total;
    return typeof docs === "number" && docs > 0;
  }, "knowledge reindex");
  await hold(page, 2500);

  // 3/4 — the ticket, filled and submitted
  await page.goto(`${BASE}/tickets`);
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  await hold(page, 1500);
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();
  const runLink = page.locator('a[href^="/runs/"]').first();
  await runLink.waitFor({ timeout: 60_000 });
  const runId = (await runLink.getAttribute("href")).replace("/runs/", "");
  console.log(`golden-path run: ${runId}`);
  await hold(page, 2500);

  // 5 — parked at the approval gate
  await page.goto(`${BASE}/runs/${runId}`);
  await pollUntil(async () => {
    const run = (await apiGet(`/api/runs/${runId}`)) ?? {};
    return (run.data ?? run).status === "waiting_approval";
  }, "run to park at waiting_approval");
  await hold(page, 4000);

  // 6 — the approvals inbox
  await page.goto(`${BASE}/approvals`);
  await page.getByText(/billing\.issue_refund/).first().waitFor({ timeout: 60_000 });
  await hold(page, 3000);

  // Approve, then recreate the worker so the resumed run replays the fixture
  // from the start (the fake's cursor is per process; the showcase recorder
  // does the same).
  await page.getByRole("button", { name: /Approve|批准/i }).first().click();
  await sleep(2000);
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
  if (up.error || up.status !== 0) throw new Error("recreateWorker failed");

  // 7 — the pending inbox empty
  await page.goto(`${BASE}/approvals`);
  await pollUntil(async () => {
    const approvals = await apiGet("/api/approvals?status=pending");
    const items = approvals?.items ?? [];
    return items.length === 0;
  }, "pending approvals to drain");
  await hold(page, 2500);

  // 8 — the completed run
  await page.goto(`${BASE}/runs/${runId}`);
  await pollUntil(async () => {
    const run = (await apiGet(`/api/runs/${runId}`)) ?? {};
    return (run.data ?? run).status === "completed";
  }, "run to complete");
  await hold(page, 4000);

  // 9 — the runs list
  await page.goto(`${BASE}/runs`);
  await hold(page, 2500);

  // 10 — the recorded decision
  await page.goto(`${BASE}/approvals`);
  await hold(page, 3000);

  await page.close();
} finally {
  await browser.close();
}

// Encode: 4 fps, two-pass palette, loop forever — the same encoder settings
// record_demo_gif.mjs uses, so the new GIF is the same kind of artefact.
const palette = path.join(WORK, "palette.png");
sh("ffmpeg", ["-y", "-framerate", "4", "-i", path.join(FRAMES, "f%04d.png"),
  "-vf", "palettegen=max_colors=128", palette]);
sh("ffmpeg", ["-y", "-framerate", "4", "-i", path.join(FRAMES, "f%04d.png"),
  "-i", palette, "-lavfi", "paletteuse=dither=bayer", "-loop", "0", OUT_GIF]);

console.log(`wrote ${OUT_GIF} from ${n} frames`);
