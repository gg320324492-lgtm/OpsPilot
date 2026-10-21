/**
 * The navigation guard — the one Electron call that needs no Electron import.
 *
 * ## Why this is its own module
 *
 * `restrictWebContents` needs exactly one thing from Electron, and it is a
 * **type**: the `WebContents` it installs its handlers on. `import type` is
 * erased when TypeScript compiles, so this module has **no runtime dependency
 * on Electron at all** — which is the whole point of it being separate.
 *
 * It used to live in `src/security.ts`, beside the two functions that need
 * Electron's *values* (`protocol`, `session`). That file imports `electron` for
 * real, and `require("electron")` **downloads the Electron binary** when it is
 * not already installed — the package's own `index.js` does it at import time,
 * and throws if the download fails. So a spec that imported the guard from
 * there made the `desktop-check` CI job fetch ~100 MB of Electron during a
 * test run that its own step name and the project's own config describe as
 * needing "no Electron, no Docker, no browser binary".
 *
 * `desktop/e2e/security.spec.ts` says it plainly: "The policy module is free
 * of `require("electron")` for exactly this reason: these assertions run on a
 * CI runner with no display, and they would not if importing the module
 * needed a running Electron to mean anything." The policy module was free of
 * it; the *guard* was not, and the spec imported both. Now it can.
 *
 * The policy decisions stay in `src/security-policy.ts`, which also imports
 * nothing from Electron. Between them, these two files are the whole of what
 * the app does to a page it did not write, and neither of them can be reached
 * without an Electron binary.
 */

import type { WebContents } from "electron";

import { isAllowedUrl } from "./security-policy";

/**
 * Limit navigation to this app's own origins (item 13) and deny every attempt to
 * open a second window (item 14).
 *
 * `will-navigate` fires only for navigations the *page* initiates, so this does
 * not interfere with the main process calling `loadURL` itself when it hands the
 * window over to the dashboard.
 *
 * `setWindowOpenHandler` returns `deny` unconditionally and does **not** hand the
 * URL to `shell.openExternal` — item 15: `openExternal` on content a renderer
 * chose is a documented way to run something on the user's machine, and this
 * app has no reason to open anything at all.
 *
 * ## WHY `allowedOrigins` IS A FUNCTION AND NOT AN ARRAY
 *
 * It used to be an array, and that was a live defect. `main.ts` installs this
 * from `app.on("web-contents-created", …)`, which fires while `createWindow()`
 * runs — and the dashboard's origin is only known *later*, inside `launch()`,
 * which assigns it to a module-level variable:
 *
 *     mainWindow = createWindow();   // web-contents-created fires here
 *     await launch();                // allowedOrigins = […] happens in here
 *
 * An array passed at install time captured the value from *before* that
 * assignment — `[BOOTSTRAP_ORIGIN]` alone — so the dashboard's
 * `http://127.0.0.1:3210` was never in the allow-list and **every
 * page-initiated navigation in the running app was refused.** Next.js
 * client-side routing hid it, because a client transition does not fire
 * `will-navigate`; the first control that did a real document navigation was
 * the language switcher, which simply did nothing. Reproduced as a minimal
 * Electron program: the guard received `["opspilot://ui"]` while the module
 * variable already held both origins.
 *
 * Reading the origins through a function defers the lookup to the moment of the
 * navigation, which is the only moment the answer is known to be complete.
 */
export function restrictWebContents(
  contents: WebContents,
  allowedOrigins: () => readonly string[],
): void {
  contents.on("will-navigate", (event, url) => {
    if (!isAllowedUrl(url, allowedOrigins())) {
      event.preventDefault();
      console.warn(`[opspilot] refused a navigation to ${url}`);
    }
  });

  contents.setWindowOpenHandler(({ url }) => {
    console.warn(`[opspilot] refused to open a window for ${url}`);
    return { action: "deny" };
  });
}
