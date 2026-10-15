/**
 * The security configuration, asserted rather than described.
 *
 * ## Why this file exists
 *
 * `docs/phase2-plan.md` P2-D: "Security configuration is not optional: context
 * isolation on, node integration off, sandbox on, navigation and IPC restricted
 * to what the app needs." A configuration nobody asserts is a comment, and a
 * comment is what gets lost the first time someone adds a `BrowserWindow` for a
 * "quick preview" or reaches for `nodeIntegration` to read a config file.
 *
 * So each of those four properties is pinned here, against the same
 * `src/security-policy.ts` the app builds its windows from. The policy module is
 * free of `require("electron")` for exactly this reason: these assertions run on
 * a CI runner with no display, and they would not if importing the module needed
 * a running Electron to mean anything.
 *
 * ## What each test is defending against
 *
 *   * The `webPreferences` test is against the renderer being unsandboxed. The
 *     app's server process holds `OPSPILOT_OPERATOR_TOKEN` — a refund-capable
 *     credential — so the renderer is the untrusted side of this application.
 *   * The navigation tests are against a `startsWith` check, which is what a
 *     string comparison of an origin always becomes. `http://127.0.0.1:3210.
 *     attacker.example` is the canonical example and it is here.
 *   * The sender test is against a privileged IPC handler that answers any
 *     frame — Electron's item 17, and the one item on that list that is a single
 *     missing `if`.
 *   * The CSP test is against the bootstrap page silently gaining a remote
 *     origin, which is the only way a `script-src` that says `'self'` is ever
 *     wrong.
 */

import { expect, test } from "@playwright/test";

import {
  BOOTSTRAP_CSP,
  BOOTSTRAP_FILES,
  BOOTSTRAP_HOST,
  BOOTSTRAP_ORIGIN,
  BOOTSTRAP_SCHEME,
  BOOTSTRAP_URL,
  isAllowedUrl,
  isTrustedSender,
  originOf,
  SECURE_WEB_PREFERENCES,
} from "../src/security-policy";
import { restrictWebContents } from "../src/security";

const DASHBOARD_ORIGIN = "http://127.0.0.1:3210";
// Origins, not URLs: `isAllowedUrl` compares `protocol + "//" + host`, so
// putting a full URL in the allowlist would silently refuse the app's own page.
const ALLOWED = [BOOTSTRAP_ORIGIN, DASHBOARD_ORIGIN];

test.describe("webPreferences", () => {
  test("isolates the context, integrates no Node, and sandboxes the renderer", () => {
    expect(SECURE_WEB_PREFERENCES.contextIsolation).toBe(true);
    expect(SECURE_WEB_PREFERENCES.nodeIntegration).toBe(false);
    expect(SECURE_WEB_PREFERENCES.sandbox).toBe(true);
  });

  test("does not disable web security, and offers no second renderer", () => {
    expect(SECURE_WEB_PREFERENCES.webSecurity).toBe(true);
    // A `<webview>` is a whole second renderer with its own webPreferences; this
    // app has no use for one and no safe way to configure it.
    expect(SECURE_WEB_PREFERENCES.webviewTag).toBe(false);
  });

  test("names no setting this app has not justified", () => {
    // The allowlist is closed on purpose. A new key here — `allowRunningInsecure
    // Content`, `enableBlinkFeatures`, `nodeIntegrationInSubFrames` — is a new
    // privilege, and this test is where somebody has to notice it was added.
    expect(Object.keys(SECURE_WEB_PREFERENCES).sort()).toEqual([
      "contextIsolation",
      "nodeIntegration",
      "sandbox",
      "webSecurity",
      "webviewTag",
    ]);
  });
});

test.describe("navigation", () => {
  test("permits the two origins this app owns", () => {
    expect(isAllowedUrl(BOOTSTRAP_URL, ALLOWED)).toBe(true);
    expect(isAllowedUrl(`${DASHBOARD_ORIGIN}/tickets`, ALLOWED)).toBe(true);
    expect(isAllowedUrl(`${DASHBOARD_ORIGIN}/approvals/abc/approve`, ALLOWED)).toBe(true);
  });

  test("refuses everything else, including the prefix and the userinfo tricks", () => {
    const refused = [
      "https://example.com/",
      "http://localhost:3210/",
      "http://127.0.0.1:3211/",
      "http://127.0.0.1:3210.attacker.example/",
      "http://127.0.0.1:3210@attacker.example/",
      "http://attacker.example/#http://127.0.0.1:3210",
      "file:///C:/Windows/System32/calc.exe",
      "about:blank",
      "not a url at all",
    ];
    for (const url of refused) {
      expect(isAllowedUrl(url, ALLOWED), `${url} must be refused`).toBe(false);
    }
  });
});

test.describe("originOf", () => {
  test("produces an origin for a custom scheme, where URL.origin would not", () => {
    // `new URL("opspilot://ui/x").origin` is the string "null" — a scheme the
    // URL standard does not treat as special. Comparing against that would make
    // every comparison in this app compare "null" to something else, and nothing
    // would ever match, including the one origin it owns.
    expect(new URL(BOOTSTRAP_URL).origin).toBe("null");
    expect(originOf(BOOTSTRAP_URL)).toBe(`${BOOTSTRAP_SCHEME}://${BOOTSTRAP_HOST}`);
  });

  test("reads the host, so userinfo cannot spoof it", () => {
    expect(originOf("opspilot://ui@attacker.example/x")).toBe("opspilot://attacker.example");
    expect(originOf("http://user:pass@127.0.0.1:3210/")).toBe("http://127.0.0.1:3210");
  });

  test("returns null rather than guessing at an unparseable URL", () => {
    expect(originOf("")).toBeNull();
    expect(originOf("://")).toBeNull();
  });
});

test.describe("IPC senders", () => {
  test("accepts the bootstrap page and nothing else", () => {
    expect(isTrustedSender({ url: BOOTSTRAP_URL })).toBe(true);
  });

  test("refuses a frame that has gone away, and every foreign frame", () => {
    expect(isTrustedSender(null)).toBe(false);
    expect(isTrustedSender(undefined)).toBe(false);
    expect(isTrustedSender({ url: `${DASHBOARD_ORIGIN}/` })).toBe(false);
    // The three URL shapes Electron's item 17 names: none of them identifies who
    // controls the document, so none may be treated as trusted.
    expect(isTrustedSender({ url: "about:blank" })).toBe(false);
    expect(isTrustedSender({ url: "blob:http://127.0.0.1:3210/abc" })).toBe(false);
    expect(isTrustedSender({ url: `data:text/html,<h1>x</h1>` })).toBe(false);
    expect(isTrustedSender({ url: `${BOOTSTRAP_SCHEME}://attacker/x` })).toBe(false);
  });
});

test.describe("the bootstrap page", () => {
  test("serves three files and nothing else", () => {
    expect([...BOOTSTRAP_FILES].sort()).toEqual(["bootstrap.css", "bootstrap.js", "index.html"]);
    // `package.json` and the built `server.js` are the two files an attacker
    // would want from a page that can name this scheme.
    expect(BOOTSTRAP_FILES.has("../package.json")).toBe(false);
    expect(BOOTSTRAP_FILES.has("package.json")).toBe(false);
  });

  test("carries a policy that permits nothing remote", () => {
    expect(BOOTSTRAP_CSP).toContain("default-src 'none'");
    expect(BOOTSTRAP_CSP).toContain("script-src 'self'");
    expect(BOOTSTRAP_CSP).toContain("frame-ancestors 'none'");
    expect(BOOTSTRAP_CSP).toContain("form-action 'none'");
    // No `*`, and no scheme other than the page's own.
    expect(BOOTSTRAP_CSP).not.toContain("*");
    expect(BOOTSTRAP_CSP).not.toContain("unsafe-inline");
    expect(BOOTSTRAP_CSP).not.toContain("unsafe-eval");
  });
});
/**
 * The navigation guard reads the allow-list at navigation time, not at install
 * time — the defect of 2026-10-16, pinned so it cannot come back.
 *
 * `main.ts` installs the guard from `app.on("web-contents-created", …)`, which
 * fires while `createWindow()` runs. The dashboard's origin is assigned to a
 * module-level `allowedOrigins` later still, inside `launch()`:
 *
 *     mainWindow = createWindow();   // web-contents-created fires here
 *     await launch();                // allowedOrigins = […] happens in here
 *
 * The guard used to take the array itself, so it captured `[BOOTSTRAP_ORIGIN]`
 * — the pre-launch value — and refused **every page-initiated navigation in the
 * running app**. Nothing noticed for a whole phase because Next.js client-side
 * routing does not fire `will-navigate`: the five nav links are client
 * transitions. The first control that performed a real document navigation was
 * the language switcher, whose click was silently preventDefault-ed. The user's
 * symptom was "the 中文 button does nothing".
 *
 * ## Why this is driven with a fake `WebContents`
 *
 * The bug is in *when the list is read*, not in what the list contains, so it is
 * testable without Electron, a display, or a running stack — which is what lets
 * it run in the `lifecycle` project on a bare CI runner. `restrictWebContents`
 * only ever calls `contents.on(...)`, so a recorder for that is the whole
 * collaborator.
 */

/** A stand-in for Electron's `WebContents` that records its `on` handlers. */
function fakeWebContents() {
  const handlers = new Map<string, (event: unknown, url: string) => void>();
  return {
    contents: {
      on(name: string, handler: (event: unknown, url: string) => void) {
        handlers.set(name, handler);
        return this;
      },
      setWindowOpenHandler() {
        /* not exercised here */
      },
    } as unknown as Parameters<typeof restrictWebContents>[0],
    /** Fire the recorded `will-navigate` handler and report whether it was allowed. */
    navigate(url: string): boolean {
      let prevented = false;
      handlers.get("will-navigate")?.({ preventDefault: () => (prevented = true) }, url);
      return !prevented;
    },
  };
}

test.describe("the navigation guard reads its allow-list late", () => {
  test("allows a dashboard navigation installed before the origin was known", () => {
    // Exactly main.ts's ordering: the guard is installed while the module
    // variable still holds only the bootstrap origin...
    let allowedOrigins: string[] = [BOOTSTRAP_ORIGIN];
    const fake = fakeWebContents();
    restrictWebContents(fake.contents, () => allowedOrigins);

    // ...and only afterwards does `launch()` learn the dashboard's origin.
    allowedOrigins = [BOOTSTRAP_ORIGIN, DASHBOARD_ORIGIN];

    expect(
      fake.navigate(`${DASHBOARD_ORIGIN}/api/locale?locale=zh-CN&returnTo=%2F`),
      "the language switcher's navigation was refused; the guard captured the " +
        "allow-list before launch() assigned the dashboard origin, which is the " +
        "2026-10-16 defect. The origin list must be read per navigation.",
    ).toBe(true);
  });

  test("still refuses a foreign origin after the same late assignment", () => {
    // The fix must not have widened into "allow everything once launch() runs".
    let allowedOrigins: string[] = [BOOTSTRAP_ORIGIN];
    const fake = fakeWebContents();
    restrictWebContents(fake.contents, () => allowedOrigins);
    allowedOrigins = [BOOTSTRAP_ORIGIN, DASHBOARD_ORIGIN];

    expect(fake.navigate("https://example.com/")).toBe(false);
    expect(fake.navigate(`${DASHBOARD_ORIGIN}.attacker.example/`)).toBe(false);
  });
});
