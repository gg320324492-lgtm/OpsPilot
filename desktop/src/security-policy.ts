/**
 * The security *policy* — every value and every predicate, with no Electron
 * import anywhere in this file.
 *
 * ## Why the policy is separate from the Electron calls
 *
 * Two reasons, and the first is the whole point.
 *
 * The policy is what D1 is judged on. "Context isolation on, node integration
 * off, sandbox on, navigation and IPC restricted to what the app needs" is a
 * claim about a configuration, and a claim nobody asserts is a comment. Keeping
 * the values and the predicates free of `require("electron")` means a Playwright
 * spec can import them and assert them on a runner with no display — which is
 * what `security.spec.ts` does, and it is the same reason
 * `stack.ts::assessWorker` is a pure function.
 *
 * The second is structural: `src/security.ts` is then only the six Electron
 * calls that install this policy, so the file a reviewer reads to answer "what
 * does this app do to a page it did not write?" is short and contains no policy.
 *
 * ## The guidance
 *
 * Electron's own security checklist, at the version pinned in `package.json`
 * (44.7.1): `docs/tutorial/security.md`. Each item is named where it is
 * implemented. In summary, the ones that apply to this app:
 *
 *   * **1. Only load secure content** — everything loaded is loopback or a
 *     `secure:` custom scheme. HTTPS buys transport integrity on a connection
 *     that never leaves the loopback interface; the property it would provide is
 *     delivered here by `HOSTNAME=127.0.0.1` on the dashboard server instead.
 *   * **2. No Node integration for the renderer.**
 *   * **3. Context isolation.** Note Electron's warning on this item:
 *     disabling context isolation also disables sandboxing, whatever `sandbox`
 *     says. Both are set, and both must stay.
 *   * **4. Process sandboxing.**
 *   * **5. Permission requests refused.**
 *   * **6. `webSecurity` not disabled**, and no `allowRunningInsecureContent`,
 *     `enableBlinkFeatures` or `allowpopups`.
 *   * **7. A Content-Security-Policy** on the bootstrap page.
 *   * **13. Navigation limited** to the origins this app owns.
 *   * **14. New windows denied.**
 *   * **17. Every IPC sender validated.**
 *   * **18. Local pages from a custom protocol**, not `file://`.
 *
 * The settings below are written out even where they match Electron's default,
 * because a configuration that relies on a default is a configuration whose
 * default can move with a version bump, and "is the renderer sandboxed" should
 * not require knowing which Electron release changed it.
 */

/** The custom scheme the bootstrap page is served from (item 18). */
export const BOOTSTRAP_SCHEME = "opspilot";
export const BOOTSTRAP_HOST = "ui";
export const BOOTSTRAP_ORIGIN = `${BOOTSTRAP_SCHEME}://${BOOTSTRAP_HOST}`;
export const BOOTSTRAP_URL = `${BOOTSTRAP_ORIGIN}/index.html`;

/**
 * `webPreferences`, in full (items 2, 3, 4, 6).
 *
 * `sandbox: true` is the OS sandbox for the renderer; `contextIsolation: true`
 * keeps the preload's world separate from the page's, so a script in the page
 * cannot reach through `Array.prototype` or `JSON.parse` into it. Together they
 * are what make `nodeIntegration: false` mean anything.
 *
 * `webviewTag: false` because a `<webview>` is a second renderer with its own
 * `webPreferences` — a whole privilege boundary this app has no use for and no
 * safe way to configure.
 */
export const SECURE_WEB_PREFERENCES = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  webviewTag: false,
} as const;

/**
 * Content-Security-Policy for the bootstrap page (item 7).
 *
 * `default-src 'none'`, then exactly the three things the page is made of: its
 * own script, its own stylesheet, and inline `data:` images. There is no remote
 * origin in it, so there is nothing for this policy to permit beyond the app.
 */
export const BOOTSTRAP_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

/** The only files the bootstrap page may load. Anything else is a 404. */
export const BOOTSTRAP_FILES: ReadonlySet<string> = new Set(["index.html", "bootstrap.css", "bootstrap.js"]);

export const CONTENT_TYPES: Readonly<Record<string, string>> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

/**
 * The origin a URL belongs to, as this app computes it.
 *
 * `protocol + "//" + host` rather than `URL.origin`, and the reason is specific:
 * for a scheme the URL standard does not treat as *special* — which is what
 * `opspilot://` is — `URL.origin` serialises as the string `"null"`. Every
 * comparison against `parsed.origin` would then be comparing `"null"` to
 * another string and matching nothing, including the one origin this app owns.
 * `protocol` and `host` together are exactly the origin for a `standard:` scheme,
 * so building it this way handles `http://`, `opspilot://` and anything else
 * with one definition.
 *
 * `URL` is still the parser, because it is what decides
 * `opspilot://ui@elsewhere.example` correctly — and that string is exactly the
 * shape a naive split on `"://"` gets wrong, which is why Electron's item 13
 * recommends the parser over string comparison.
 *
 * @returns the origin, or `null` for anything `URL` cannot parse.
 */
export function originOf(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  return `${parsed.protocol}//${parsed.host}`;
}

/**
 * Is `url` on one of the origins this app owns? (item 13)
 *
 * Origins are compared, never prefixes. `startsWith("http://127.0.0.1:3210")`
 * would admit `http://127.0.0.1:3210.attacker.example`, which is precisely the
 * mistake Electron's item 13 calls out.
 */
export function isAllowedUrl(url: string, allowedOrigins: readonly string[]): boolean {
  const origin = originOf(url);
  return origin !== null && allowedOrigins.includes(origin);
}

/**
 * Is this IPC message from a frame this app put there? (item 17)
 *
 * Electron's guidance in item 17 is to validate the sender of *every* IPC
 * message, and to validate the **frame's origin** rather than its URL, because
 * `about:blank`, `blob:` and sandboxed documents carry URLs that do not identify
 * who controls them, and the frame may be `null` once it has gone away.
 *
 * Structural rather than `WebFrameMain`, so this predicate is testable without
 * Electron — which is the only reason it is worth having as a separate function.
 */
export function isTrustedSender(frame: { readonly url: string } | null | undefined): boolean {
  if (frame === null || frame === undefined) {
    return false;
  }
  return originOf(frame.url) === BOOTSTRAP_ORIGIN;
}