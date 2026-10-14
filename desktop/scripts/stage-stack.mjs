/**
 * Stage the tree the installer ships, from this checkout.
 *
 * ## Why this exists
 *
 * `src/runtime/dependencies.ts::resolveStackRoot` answers one question two
 * ways: a development launch walks up from the working directory looking for
 * `docker-compose.yml`, and a packaged launch reads `resources/stack/`. Before
 * this script there was only the first, so a packaged app on a clean machine
 * found nothing and told the operator to start it from inside a repository they
 * did not have.
 *
 * This is the other half. It copies exactly what `docker compose up postgres
 * migrate api worker` and `startDashboard` read at run time, and nothing else,
 * into `desktop/stage/stack/` — which `electron-builder.yml` turns into
 * `extraResources`. Both consumers reach the disk, so this tree ships **outside**
 * the asar archive, as real files in a real directory.
 *
 * ## What is in it, and why each thing
 *
 *   * `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `alembic.ini` and
 *     `.dockerignore` — the compose project and the build it names. The compose
 *     file's `build: context: .` is why the next line is a directory and not a
 *     file: Docker builds the API image from this very tree.
 *   * `src/`, `mcp_servers/`, `migrations/`, `knowledge/`, `evals/datasets/` —
 *     the four `COPY` lines in the Dockerfile plus the product data. Together
 *     about 2.3 MB; shipping the images themselves instead would mean hosting a
 *     registry, which is a different project with its own publishing rules.
 *   * `web/.next/standalone/` — the dashboard server, with `public/` and
 *     `.next/static/` already copied inside it so the tree is self-contained.
 *     See below for why that matters on an installed machine.
 *
 * ## What is deliberately NOT in it
 *
 * **No `.env`, anywhere.** `OPSPILOT_OPERATOR_TOKEN` is a refund-capable
 * credential and Compose refuses to render the project without one. Shipping an
 * installer that contained one would publish a shared secret to everyone who
 * downloads it. The check at the bottom of this file fails the build rather than
 * trusting the list above to stay correct: a `.env` is the one file most likely
 * to be added here by someone who did not mean to.
 *
 * **No `.next` source, no `node_modules` of the dashboard's dev tree** — the
 * standalone tree is the traced, minimal output and nothing else is read.
 *
 * ## Why `public/` and `.next/static/` are copied into the standalone tree
 *
 * `next build --output standalone` emits `server.js` and a `node_modules`
 * subset, and does **not** copy `public/` or `.next/static` into it (Next's own
 * `output.md` says so). `startDashboard` copies them in at launch, which is
 * right for a checkout: the app is allowed to write there. It is wrong for an
 * installed app, where the tree lives under `Program Files` and the account
 * running the app cannot write to it. Copying them at package time makes the
 * shipped tree complete, so `startDashboard`'s copy becomes a no-op — it skips
 * what is already present, and `buildDependencies`'s `copyDir` refuses to copy
 * a directory onto itself besides.
 *
 * ## This is not committed, and should not be
 *
 * `stage/` is a build output. `.next/` is gitignored for the same reason. CI
 * builds `web/` before it would build an installer, and a 30 MB traced
 * dependency tree in git is a thing nobody reviews and everybody has to keep
 * from going stale. The script therefore requires the tree to already exist and
 * says exactly which command produces it, rather than building it here — the
 * same discipline `copy-renderer.mjs` follows about failing loudly.
 */

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(here, "..");
const repoRoot = path.resolve(desktopDir, "..");
const stageDir = path.resolve(desktopDir, "stage", "stack");

/** Fail rather than ship something that will not start on the target machine. */
function required(relativePath) {
  const from = path.join(repoRoot, relativePath);
  if (!existsSync(from)) {
    console.error(`cannot stage the app: ${from} does not exist`);
    process.exit(1);
  }
  return from;
}

/** Copy a file, creating the destination's directory first. */
function copyFile(relativePath) {
  const from = required(relativePath);
  const to = path.join(stageDir, relativePath);
  mkdirSync(path.dirname(to), { recursive: true });
  cpSync(from, to);
}

/** Copy a directory tree, recursively. */
function copyDir(relativePath) {
  const from = required(relativePath);
  const to = path.join(stageDir, relativePath);
  mkdirSync(path.dirname(to), { recursive: true });
  cpSync(from, to, { recursive: true });
}

// A stale tree from an earlier build is worse than no tree: `cpSync` merges into
// what is there, so a file deleted from `src/` since would survive into the
// installer and nothing would say so. The target is a build output, so removing
// it is safe and makes each run reproducible.
if (existsSync(stageDir)) {
  rmSync(stageDir, { recursive: true, force: true });
}
mkdirSync(stageDir, { recursive: true });

// --- The compose project and the Docker build it names -------------------------
for (const file of ["docker-compose.yml", "Dockerfile", "pyproject.toml", "alembic.ini", ".dockerignore"]) {
  copyFile(file);
}

// --- The build context the Dockerfile COPYs ------------------------------------
for (const dir of ["src", "mcp_servers", "migrations", "knowledge", "evals/datasets"]) {
  copyDir(dir);
}

// --- The dashboard ------------------------------------------------------------
// `stage-stack` must have been run after `npm run build` in web/. Saying which
// command is missing is the difference between a packaging failure and a
// mystery on the target machine.
const standaloneSrc = path.join(repoRoot, "web", ".next", "standalone");
if (!existsSync(path.join(standaloneSrc, "server.js"))) {
  console.error(
    `cannot stage the app: ${standaloneSrc}\\server.js does not exist.\n` +
      "Run `npm ci && npm run build` in web/ first — the installer ships a built dashboard,\n" +
      "it does not build one on the target machine.",
  );
  process.exit(1);
}
copyDir("web/.next/standalone");

// Make the copied standalone tree self-contained. See this file's docstring:
// the installed tree is under Program Files and cannot be written to at launch.
const publicSrc = required("web/public");
cpSync(publicSrc, path.join(stageDir, "web/.next/standalone/public"), { recursive: true });
const staticSrc = required("web/.next/static");
cpSync(staticSrc, path.join(stageDir, "web/.next/standalone/.next/static"), { recursive: true });

// --- The check that is the point of this script -------------------------------
// Walk the staged tree for anything shaped like a dotenv file. Cheap, and the
// only place in the packaging pipeline that would notice one being added.
function findEnvFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...findEnvFiles(full));
    } else if (entry.name === ".env" || entry.name.startsWith(".env.")) {
      found.push(full);
    }
  }
  return found;
}

const leaked = findEnvFiles(stageDir);
if (leaked.length > 0) {
  console.error(
    "refusing to package: the staged tree contains dotenv files, and one of them may hold " +
      "OPSPILOT_OPERATOR_TOKEN.\nRemove them from the copy list above — an installer that ships a " +
      `credential publishes it to everyone who downloads it.\n${leaked.join("\n")}`,
  );
  process.exit(1);
}

/** Bytes in a tree, so the report line is a number rather than an impression. */
function measure(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    total += entry.isDirectory() ? measure(full) : statSync(full).size;
  }
  return total;
}

const bytes = measure(stageDir);
console.log(`staged the runtime tree into ${stageDir} (${Math.round(bytes / 1_048_576)} MiB)`);