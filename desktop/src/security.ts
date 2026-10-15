/**
 * The Electron calls that install `src/security-policy.ts`.
 *
 * Six functions and no policy. Everything a reviewer needs to know about what
 * this app does to a page it did not write — which origins it will navigate to,
 * which files it will serve, what the renderer may ask for — is in the policy
 * module, which imports nothing from Electron and is asserted by
 * `desktop/e2e/security.spec.ts`.
 */

import { protocol, session, type Session, type WebContents } from "electron";

import { isAllowedUrl, isTrustedSender } from "./security-policy";

/**
 * Declare the custom scheme (item 18).
 *
 * Must run at module scope, before `app.whenReady()`: privileges are fixed
 * when a scheme is registered, and registering afterwards does not
 * retroactively apply them. `main.ts` calls this before anything else.
 */
export function registerPrivilegedSchemes(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: "opspilot",
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: false,
        stream: true,
        // Never true. `bypassCSP` would defeat the bootstrap page's CSP for this
        // scheme, and there is no reason on earth for a bundled page to need it.
        bypassCSP: false,
      },
    },
  ]);
}

/** Refuse every permission request and every permission check (item 5). */
export function denyAllPermissions(target: Session = session.defaultSession): void {
  target.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  target.setPermissionCheckHandler(() => false);
}

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

export { isAllowedUrl, isTrustedSender };