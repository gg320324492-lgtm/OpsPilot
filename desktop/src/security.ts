/**
 * The Electron calls that install `src/security-policy.ts`.
 *
 * Two functions here, installing **three** Electron calls, and one in
 * `src/security-web-contents.ts`, installing **two** — five Electron calls in
 * all — and no policy. Everything a reviewer needs to know
 * about what this app does to a page it did not write — which origins it will
 * navigate to, which files it will serve, what the renderer may ask for — is
 * in the policy module, which imports nothing from Electron and is asserted by
 * `desktop/e2e/security.spec.ts`.
 *
 * **Why the guard is in another file.** The two functions here need
 * Electron's *values* (`protocol`, `session`), and `require("electron")`
 * downloads the Electron binary when it is not already installed — at import
 * time, from the package's own `index.js`. The navigation guard needs only the
 * `WebContents` *type*, so it lives in `src/security-web-contents.ts`, whose
 * `import type` is erased at compile time and which therefore has no runtime
 * dependency on Electron at all. That is what lets the spec that exercises it
 * run on a CI runner with no Electron binary — which is the whole point of
 * the `lifecycle` project. See that file's docstring for the rest.
 */

import { protocol, session, type Session } from "electron";

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

export { restrictWebContents } from "./security-web-contents";
export { isAllowedUrl, isTrustedSender };
