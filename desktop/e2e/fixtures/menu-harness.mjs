/**
 * A throwaway Electron application whose only job is to be a menu.
 *
 * ## WHY A HARNESS AND NOT THE REAL APP
 *
 * The real application spends its first seconds finding a stack, spawning
 * Compose and waiting for a dashboard — all of which `desktop/e2e/shell.spec.ts`
 * covers and all of which this has no interest in. What this has to prove is
 * narrow: that a cookie in the session's store puts the *real* Electron menu
 * into the *real* language, and that changing the cookie moves it and
 * clearing it puts it back.
 *
 * So this is the real `src/menu.ts`, in a real Electron, against a real cookie
 * store — with nothing else running. It loads the compiled module rather than
 * reimplementing it, so the thing under test is the thing that ships.
 *
 * ## WHY IT IS AN `.mjs` FILE
 *
 * Electron's main process accepts an ES module, and the `.js`/`.mjs` lint
 * block in `eslint.config.mjs` declares browser globals only — a CommonJS
 * `require` would need an exception there for a file that has no reason to be
 * CommonJS. `createRequire` is the sanctioned way to load a CommonJS module
 * from an ES module, and `dist/menu.js` is CommonJS because `tsconfig.build.json`
 * compiles to it.
 *
 * ## WHY IT MOVES THE PROFILE
 *
 * `sessionData` is where Chromium keeps cookies and caches, and it is the
 * developer's real one unless it is moved. A test that deleted the locale
 * cookie to prove the menu falls back to English would otherwise change the
 * language of the application the developer is actually using. Both paths are
 * pointed at a throwaway directory, before `ready`, which is when Electron
 * says they have to be set.
 *
 * The directory can be named from outside, so a spec can run the harness
 * twice against the same profile — which is how "the application starts in
 * the language the operator chose last time" is observed rather than
 * assumed.
 */

import { app, session } from "electron";
import { mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));

const { installMenu, syncMenuToCookie, watchLocaleCookie } = require(
  path.join(here, "..", "..", "dist", "menu.js"),
);

/** The origin the locale cookie lives on. The dashboard's, in a real run. */
const DASHBOARD_URL = process.env["OPSPILOT_MENU_HARNESS_URL"] ?? "http://127.0.0.1:3210/";

const profile =
  process.env["OPSPILOT_MENU_HARNESS_PROFILE"] ?? mkdtempSync(path.join(tmpdir(), "opspilot-menu-harness-"));
app.setPath("userData", profile);
app.setPath("sessionData", profile);

app.whenReady().then(async () => {
  // The development items, because this harness stands in for a checkout.
  installMenu({ debug: true });

  // The same two calls `main.ts` makes, in the same order: follow the cookie,
  // and read it once to start in the language the operator chose last time.
  watchLocaleCookie(session.defaultSession, DASHBOARD_URL);
  await syncMenuToCookie(session.defaultSession, DASHBOARD_URL);

  // Nothing here opens a window, and an Electron application with no window
  // has nothing to keep its event loop busy. The spec closes the app when it
  // is done; this is only so that "no window" does not also mean "no app".
  setInterval(() => undefined, 60_000);
});
