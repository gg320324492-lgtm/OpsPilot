// Record docs/assets/golden-path.gif from a real, deterministic OpsPilot run.
//
// WHY THIS IS NOT VHS
// -------------------
// §M9 asks for "a recorded GIF from the deterministic path". The intended tool
// is vhs (docs/demo/golden-path.tape), and the tape is committed. But vhs
// cannot record on this machine:
//
//   * its terminal capture is blank. vhs drives ttyd, whose xterm.js defaults
//     to the WebGL renderer; in this headless environment WebGL produces no
//     visible glyphs, so every captured frame is empty (verified: the same
//     page renders its text under a headless browser when ttyd is started with
//     `-t rendererType=dom`, and not otherwise).
//   * its GIF encoder writes no file at all: `vhs tape.tape` prints
//     "Creating ...gif" and exits 0 with nothing on disk, for a trivial tape
//     as well as the real one. No ffmpeg process is ever spawned.
//
// So the frames here are captured the same way vhs captures them -- screenshots
// of the real ttyd terminal, taken while the real demo runs -- and encoded to a
// GIF with the system ffmpeg. Nothing is drawn by hand; every frame is a real
// screenshot of a real run. scripts/demo_golden_path.py is the program shown.
//
// Run:
//     node scripts/record_demo_gif.mjs
//
// Requires: ttyd and ffmpeg on PATH, and the dashboard's own dependencies
// installed (`npm ci` in web/ -- Playwright comes with them).

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { setTimeout as sleep } from "node:timers/promises";
import path from "node:path";

const REPO = path.resolve(import.meta.dirname, "..");

// **Playwright is resolved from `web/`, and it has to be.** It is the
// dashboard's dev dependency, so it lives in `web/node_modules/` -- and this
// script lives in `scripts/`, which has no `node_modules` of its own and no
// root `package.json` to inherit one from. A bare `import { chromium } from
// "playwright"` therefore resolved to nothing and the script died at module
// load, so the README's "reproduce the GIF" command -- the one a reviewer is
// told to run -- regenerated nothing. The require is anchored at
// `web/package.json` so Node resolves the package where it actually is.
const require = createRequire(path.join(REPO, "web", "package.json"));
const { chromium } = require("playwright");
const OUT_GIF = path.join(REPO, "docs", "assets", "golden-path.gif");
// **Inside the repository, not in the system temp directory.** The recorder's
// frames used to be written under `os.tmpdir()`, which put them on the system
// drive; a recording that failed part-way left its frames there.
const WORK = path.join(process.env.OPSPILOT_SCRATCH_DIR ?? path.join(REPO, ".opspilot", "tmp"), "opspilot-gif");
const FRAMES = path.join(WORK, "frames");
const PORT = 7701;

const PY = process.platform === "win32"
  ? path.join(REPO, ".venv", "Scripts", "python.exe")
  : path.join(REPO, ".venv", "bin", "python");

function sh(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} exited ${r.status}`);
}

rmSync(WORK, { recursive: true, force: true });
mkdirSync(FRAMES, { recursive: true });
mkdirSync(path.dirname(OUT_GIF), { recursive: true });

// ttyd runs the demo and holds the terminal open so the final frame is stable.
// `rendererType=dom` is the fix for the blank-WebGL problem above.
// OPSPILOT_DEMO_STEP_DELAY slows only the printing so a few-frames-per-second
// recording shows the stages advancing; the run itself is real and unaffected.
const inner = `cd ${JSON.stringify(REPO)} && OPSPILOT_DEMO_STEP_DELAY=1.2 ${JSON.stringify(PY)} scripts/demo_golden_path.py; sleep 999`;
const ttyd = spawn(
  "ttyd",
  ["--port", String(PORT), "--interface", "127.0.0.1",
   "-t", "rendererType=dom", "-t", "fontSize=15",
   "--writable", "bash", "-c", inner],
  { stdio: ["ignore", "inherit", "inherit"] },
);

try {
  // Wait for ttyd to bind.
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/`);
      if (res.ok) break;
    } catch { /* not up yet */ }
    await sleep(200);
  }

  // `PLAYWRIGHT_CHROMIUM` points at a browser when the one Playwright would
  // pick is not the one to use. There is deliberately **no fallback path**:
  // this used to name one user's home directory and a browser build that is
  // not the installed one, so the literal was dead on the machine that wrote it
  // and would have been wrong on every other. Playwright resolves its own
  // browser when this is unset.
  const exe = process.env.PLAYWRIGHT_CHROMIUM;
  const browser = await chromium.launch(
    exe && existsSync(exe) ? { executablePath: exe, args: ["--no-sandbox"] } : { args: ["--no-sandbox"] },
  );
  const page = await browser.newPage({ viewport: { width: 1400, height: 1050 } });
  await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "load" });
  await sleep(1500);

  // Screenshot on a fixed cadence while the demo runs. The demo is paced with
  // OPSPILOT_DEMO_STEP_DELAY so the stages advance visibly; ~90 frames at ~0.25s
  // is ~22s, comfortably longer than the paced run.
  let n = 0;
  for (let i = 0; i < 90; i++) {
    const file = path.join(FRAMES, `f${String(n++).padStart(4, "0")}.png`);
    await page.screenshot({ path: file });
    await sleep(250);
  }
  await browser.close();
} finally {
  ttyd.kill();
}

// Encode: 4 fps, two-pass palette for clean terminal text, loop forever.
const palette = path.join(WORK, "palette.png");
sh("ffmpeg", ["-y", "-framerate", "4", "-i", path.join(FRAMES, "f%04d.png"),
  "-vf", "palettegen=max_colors=128", palette]);
sh("ffmpeg", ["-y", "-framerate", "4", "-i", path.join(FRAMES, "f%04d.png"),
  "-i", palette, "-lavfi", "paletteuse=dither=bayer", "-loop", "0", OUT_GIF]);

console.log(`wrote ${OUT_GIF}`);
