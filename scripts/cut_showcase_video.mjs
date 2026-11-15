// Assemble the M2-05 showcase main video (demo path A, 90-180 s).
//
// INPUTS  docs/assets/showcase-raw/videos/A-approve.webm  (77.4 s raw take)
//         docs/assets/showcase-raw/title-card.png / end-card.png
// OUTPUTS web/public/showcase/demo-path-a.mp4   (the main deliverable)
//         web/public/showcase/demo-path-b.mp4   (path B, raw + disclosure card)
//         web/public/showcase/demo-path-c.mp4   (path C, raw + disclosure card)
//
// The raw take already carries presenter pacing (each beat holds 6-14 s, see
// scripts/record_showcase.mjs); the assembly adds the title/end cards so the
// deliverable lands in the 90-180 s band with the live/fake disclosure on
// screen at both ends. Nothing else is cut: the recording IS the walkthrough.
//
// Run: node scripts/cut_showcase_video.mjs
// Requires ffmpeg on PATH.

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const REPO_ROOT = path.resolve(import.meta.dirname, "..");
const RAW = path.join(REPO_ROOT, "docs", "assets", "showcase-raw");
const VIDEOS = path.join(RAW, "videos");
const OUT = path.join(REPO_ROOT, "web", "public", "showcase");

const W = 1440, H = 900, FPS = 25;
const TITLE_SECONDS = 8, END_SECONDS = 8;

function ffmpeg(args, what) {
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], {
    stdio: "inherit", timeout: 900_000,
  });
  if (r.error || r.status !== 0) throw new Error(`ffmpeg failed: ${what}`);
}

function run(args, what) {
  const r = spawnSync(args[0], args.slice(1), { stdio: "inherit", timeout: 300_000 });
  if (r.error || r.status !== 0) throw new Error(`failed: ${what}`);
}

/** Encode one still PNG into an H.264 segment of `seconds`. */
function stillSegment(png, seconds, target) {
  ffmpeg(
    ["-loop", "1", "-i", png, "-t", String(seconds), "-r", String(FPS),
     "-vf", `scale=${W}:${H}:flags=lanczos,setsar=1,format=yuv420p`,
     "-c:v", "libx264", "-preset", "medium", "-crf", "20", target],
    `still segment ${target}`);
}

/** Re-encode a webm take to a clean H.264 1440x900 segment. */
function videoSegment(webm, target) {
  ffmpeg(
    ["-i", webm, "-r", String(FPS),
     "-vf", `scale=${W}:${H}:flags=lanczos,setsar=1,format=yuv420p`,
     "-c:v", "libx264", "-preset", "medium", "-crf", "20", target],
    `video segment ${target}`);
}

/** Concatenate H.264 segments with identical parameters. */
function concat(segments, target) {
  const list = path.join(path.dirname(target), "concat-list.txt");
  writeFileSync(list, segments.map((s) => `file '${s.replace(/\\/g, "/")}'`).join("\n"));
  ffmpeg(["-f", "concat", "-safe", "0", "-i", list, "-c", "copy", target], `concat ${target}`);
}

function main() {
  mkdirSync(OUT, { recursive: true });
  const tmp = path.join(RAW, "tmp-segments");
  mkdirSync(tmp, { recursive: true });

  const a = path.join(VIDEOS, "A-approve.webm");
  const b = path.join(VIDEOS, "B-reject.webm");
  const c = path.join(VIDEOS, "C-model-failure.webm");
  const title = path.join(RAW, "title-card.png");
  const end = path.join(RAW, "end-card.png");
  if (!existsSync(a)) throw new Error("missing A-approve.webm; run record_showcase.mjs first");

  // -- main deliverable: title + path A + end ------------------------------
  const t0 = path.join(tmp, "title.mp4");
  const a0 = path.join(tmp, "a.mp4");
  const e0 = path.join(tmp, "end.mp4");
  stillSegment(title, TITLE_SECONDS, t0);
  videoSegment(a, a0);
  stillSegment(end, END_SECONDS, e0);
  const main_out = path.join(OUT, "demo-path-a.mp4");
  concat([t0, a0, e0], main_out);

  // -- companions: disclosure card + take (B and C keep their real pacing) --
  const d0 = path.join(tmp, "disc.mp4");
  stillSegment(title, 6, d0);
  for (const [src, name] of [[b, "demo-path-b.mp4"], [c, "demo-path-c.mp4"]]) {
    if (!existsSync(src)) continue;
    const v = path.join(tmp, name.replace(".mp4", ".mp4"));
    videoSegment(src, v);
    concat([d0, v], path.join(OUT, name));
  }

  // Report durations.
  for (const name of ["demo-path-a.mp4", "demo-path-b.mp4", "demo-path-c.mp4"]) {
    const file = path.join(OUT, name);
    if (!existsSync(file)) continue;
    const probe = spawnSync("ffmpeg", ["-hide_banner", "-i", file], { encoding: "utf8" });
    const m = ((probe.stderr ?? "") + (probe.stdout ?? "")).match(/Duration: (\d+):(\d+):(\d+\.\d+)/);
    if (m) console.log(`${name}: ${(Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3])).toFixed(1)}s`);
  }
  console.log("assembly complete ->", OUT);
}

main();
