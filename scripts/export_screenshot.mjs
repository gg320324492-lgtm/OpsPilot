// Capture the evidence-export screenshot for the M2-05 showcase.
//
// The main recording script never clicks "Export summary" (a download would
// navigate the recording away), so this companion takes the one extra frame:
// the completed path-A run's detail page with the export control in frame.
//
// Run after record_showcase.mjs, against the same stack:
//     cd web && node ../scripts/export_screenshot.mjs --api 18001 --web 3102
//
// The run id is read from the API (the newest completed run), so the frame
// matches the run the video actually walked.

import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const REPO_ROOT = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(REPO_ROOT, "web", "package.json"));
const { chromium } = require("@playwright/test");

const OUT_DIR = path.join(REPO_ROOT, "web", "public", "showcase");
const RAW_DIR = path.join(REPO_ROOT, "docs", "assets", "showcase-raw");

const args = process.argv.slice(2);
const argValue = (flag) => {
  const at = args.indexOf(flag);
  return at >= 0 ? args[at + 1] : undefined;
};
const API_PORT = argValue("--api") ?? "18000";
const WEB_PORT = argValue("--web") ?? "3101";
const DEMO_BASE_URL = `http://localhost:${WEB_PORT}`;
const DEMO_TOKEN = "demo-paths-disposable-token";

async function main() {
  // The newest completed run, straight from the API.
  const response = await fetch(`http://localhost:${API_PORT}/api/runs?status=completed`, {
    headers: { Authorization: `Bearer ${DEMO_TOKEN}` },
  });
  const data = await response.json();
  const run = data.items[0];
  if (!run) throw new Error("no completed run found; record first");
  console.log(`export screenshot for run ${run.id}`);

  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL: DEMO_BASE_URL, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${DEMO_BASE_URL}/runs/${run.id}`);
  // The control's accessible name is the catalogue's ariaLabel, not its text.
  const exportButton = page.getByRole("button", { name: /redacted evidence export/i });
  await exportButton.waitFor({ timeout: 30_000 });
  await page.waitForTimeout(1200);
  const target = path.join(OUT_DIR, "13-evidence-export.png");
  await page.screenshot({ path: target });
  console.log(`  screenshot -> 13-evidence-export.png (Run detail with the evidence-export control)`);

  // Append to the manifest without rewriting the rest.
  const { readFileSync } = await import("node:fs");
  const manifestPath = path.join(RAW_DIR, "record-manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  manifest.screenshots.push({
    kind: "screenshot",
    file: "web/public/showcase/13-evidence-export.png",
    label: "Run detail with the evidence-export control (run " + run.id.slice(0, 8) + ")",
    runId: run.id,
  });
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
