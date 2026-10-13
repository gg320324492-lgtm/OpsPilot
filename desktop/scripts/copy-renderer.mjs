/**
 * Copy the renderer assets into `dist/`, where the main process serves them.
 *
 * The renderer is three static files with no build step — `index.html`,
 * `bootstrap.css`, `bootstrap.js` — so "build" here means `tsc` for the two
 * TypeScript entry points and a copy for the rest. Doing the copy in a script
 * rather than as a `cp` in `package.json` is what makes it work identically on
 * Windows and on Linux: the npm scripts here run under `cmd.exe` on the
 * developer's machine and under `sh` on the CI runner, and a shell-specific
 * copy command is one of them failing for a reason that has nothing to do with
 * the app.
 *
 * The copy is deliberate and idempotent, and it fails loudly: a missing
 * `bootstrap.js` would otherwise produce a window that loads and does nothing,
 * which is exactly the blank window this app exists to avoid.
 */

import { cpSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(here, "..", "src", "renderer");
const target = path.resolve(here, "..", "dist", "renderer");

if (!existsSync(source)) {
  console.error(`renderer source directory is missing: ${source}`);
  process.exit(1);
}

mkdirSync(target, { recursive: true });
for (const file of ["index.html", "bootstrap.css", "bootstrap.js"]) {
  const from = path.join(source, file);
  if (!existsSync(from)) {
    console.error(`renderer asset is missing: ${from}`);
    process.exit(1);
  }
  cpSync(from, path.join(target, file));
}

console.log(`copied the renderer into ${target}`);