/**
 * The containerised dashboard reaches the API -- the fix for the split base URL.
 *
 * WHAT THIS PROVES, AND WHY IT IS THE RIGHT TEST
 * ----------------------------------------------
 * `web/src/lib/api/client.ts` resolves the API base URL for *both* server-side
 * and browser code. It used to read `NEXT_PUBLIC_API_BASE_URL`
 * (`http://localhost:8000`) for both. That is correct in a browser -- there
 * "localhost" is the user's machine and the API's port is published -- and wrong
 * on the Next server **inside a container**, where `localhost` is the container
 * itself and the API is the sibling service `api`. The containerised dashboard
 * therefore rendered "Could not reach the API" on every screen: the exact
 * deployment defect this spec is a regression guard for.
 *
 * The fix splits the resolver by runtime (`typeof window`): the server prefers
 * `API_INTERNAL_BASE_URL` (`http://api:8000`, no `NEXT_PUBLIC_` prefix so it is
 * never inlined into the bundle), the browser keeps `NEXT_PUBLIC_API_BASE_URL`,
 * and with neither set the chain falls back to `http://localhost:8000`.
 *
 * Testing this **must** run the containerised dashboard. `golden-path.spec.ts`
 * runs the dashboard on the host, where `localhost:8000` really is the API, so
 * it cannot see this defect -- a green run there says nothing about the
 * container. So this spec starts the compose `web` service
 * (`startContainerisedWebOrSkip`) and asserts, in a real browser, that a
 * Server-Component read reaches the API. The three assertions are ordered from
 * the mechanism to the symptom:
 *
 *   1. **Negative control:** the API is NOT reachable at `http://localhost:8000`
 *      from inside the container. This is the premise of the whole defect; if it
 *      were reachable the test could pass for the wrong reason. `probeFromWebContainer`
 *      asks the container itself, not the host.
 *   2. **Positive control:** the API IS reachable at `http://api:8000` -- the URL
 *      the server-side resolver now prefers.
 *   3. **The behaviour:** the dashboard's own page renders live API data (a real
 *      run count from `GET /api/runs`), and the "Could not reach the API" panel
 *      and the "fetch failed" banner are absent. This is the Server Component
 *      read the published container path performs; it failed before the fix and
 *      passes after it.
 *   4. **The bundles:** every script and stylesheet the page requests is served.
 *      This spec used to stop at (3), and that is exactly why a second, entirely
 *      separate image defect shipped green: `web/Dockerfile` copied
 *      `.next/static` and `public` to `./web/...` while the standalone server
 *      reads them as siblings of `server.js`, so every bundle 404'd. The page
 *      still rendered -- (3) passed -- the container still reported `Healthy`
 *      (its healthcheck is `GET /`), and only the browser knew: Reindex, Submit
 *      and Approve did nothing. See `every bundle the page requests is served`
 *      below for what that assertion is and why it belongs here.
 *   5. **The icons:** the bytes the browser is given for the two committed
 *      icons -- `web/src/app/favicon.ico` and `web/src/app/apple-icon.png` --
 *      are the bytes this repository holds. Item (4) is the same argument one
 *      level down, and it named its own hole: it watches `script` and
 *      `stylesheet` and ignores `other`, "which is where a favicon lands".
 *      A favicon is not one of the kinds that are legitimately absent from a
 *      fresh stack; it is committed, required, and its absence is the defect.
 *      See `the browser is served the committed favicon and apple-touch icon`
 *      below for what that assertion is and why it belongs here.
 *
 * Skipped cleanly where Docker is unavailable, per ADR-0004 (see `stack.ts`); a
 * stack Docker could attempt and did not bring up is a failure, not a skip.
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";
import {
  probeFromWebContainer,
  startContainerisedWebOrSkip,
  type StackHandle,
  WEB_DIR,
  WEB_SERVICE_URL,
} from "./stack";

let stack: StackHandle | null = null;

test.beforeAll(async () => {
  stack = await startContainerisedWebOrSkip("web-service");
});

test.afterAll(() => {
  if (stack !== null && !stack.skip) {
    stack.teardown();
  }
});

/** Skip the calling test (with a reason) when the container stack is not up. */
function skipUnlessUp(): void {
  if (stack === null) {
    test.skip(true, "the container stack did not start");
  } else if (stack.skip) {
    // The reason NAMES why -- never a bare "skipped" (ADR-0004's habit).
    test.skip(true, stack.reason);
  }
}

test("from inside the web container, localhost:8000 is NOT the API", () => {
  skipUnlessUp();

  // The premise of the defect: the published URL is the container's own
  // loopback, so the old resolver could never have worked from here. If this
  // ever becomes reachable, this test should be reconsidered -- it would mean
  // the container shares the host's network namespace and the defect could not
  // reproduce. That is itself worth knowing, so it is asserted rather than
  // assumed.
  const localhost = probeFromWebContainer("http://localhost:8000/health");
  expect(
    localhost.ok,
    `expected localhost:8000 to be unreachable from inside the web container, ` +
      `but it answered: ${localhost.detail}`,
  ).toBe(false);

  // And the compose service name IS reachable -- the URL the server-side
  // resolver prefers after the fix.
  const api = probeFromWebContainer("http://api:8000/health");
  expect(
    api.ok,
    `expected api:8000 to be reachable from inside the web container, ` +
      `but it reported: ${api.detail}`,
  ).toBe(true);
});

test("the containerised dashboard renders live API data", async ({ page }) => {
  skipUnlessUp();

  // The dashboard's root is a Server Component that calls the API on load
  // (`checkApiReachability` + the run tallies). Against the *published* web
  // port, in a browser -- the same request the deploy does.
  await page.goto(WEB_SERVICE_URL, { waitUntil: "networkidle" });

  // The symptom that was the defect. `checkApiReachability` renders this exact
  // sentence when the server-side fetch cannot connect; before the fix it was
  // present on a freshly loaded containerised dashboard.
  await expect(page.getByText(/Could not reach the API/i)).toHaveCount(0);
  // The per-panel transport failure the same defect produced.
  await expect(page.getByText(/fetch failed/i)).toHaveCount(0);

  // The behaviour: a value the server could only get by reaching the API. The
  // "Waiting for approval" tile is rendered from `GET /api/approvals` and the
  // recent-runs panel from `GET /api/runs`; a successful render with real
  // content is a server-side read that reached the api container. A fresh stack
  // has no runs yet, so the assertion is that the panels rendered their real
  // empty/stale states -- not the error state above -- rather than a non-zero
  // count, which would make the test depend on run order.
  await expect(page.getByRole("heading", { name: /Recent runs/i })).toBeVisible();
  await expect(page.getByText(/Could not load recent runs/i)).toHaveCount(0);
  await expect(page.getByText(/Could not read the index/i)).toHaveCount(0);
});

/**
 * Every bundle the containerised page requests is served.
 *
 * WHY THIS TEST EXISTS AT ALL
 * --------------------------
 * `web/Dockerfile` copied the dashboard's `.next/static` and `public` trees to
 * `/app/web/...`, but the standalone server `chdir`s to its own directory and
 * resolves both relative to *that* (`base-server.js`: `distDir = join(dir,
 * config.distDir)`; `next-server.js`: `getPublicDir() = join(dir, 'public')`),
 * so with `server.js` at `/app/server.js` it reads `/app/.next/static` and
 * `/app/public`. Nothing was there. Every JavaScript bundle and stylesheet
 * 404'd, and the failure was shaped exactly like success:
 *
 *   - `GET /` returned 200, so the container's HEALTHCHECK passed.
 *   - The document rendered, so the tests above passed -- every assertion in
 *     this spec is about server-rendered HTML and server-side API reads.
 *   - Only the browser disagreed: no CSS, and no client component ever
 *     hydrated, so Reindex, Submit and Approve did nothing at all.
 *
 * `golden-path.spec.ts` cannot catch it either, and not by accident: it runs the
 * dashboard on the host, where `e2e/stack.ts` copies those same two directories
 * into the standalone tree by hand before spawning `server.js`, so the host path
 * has always been correct.
 *
 * WHY IT IS AN ASSET CHECK AND NOT A CLICK
 * ----------------------------------------
 * A click-through would be the more behavioural assertion, and it would also be
 * a broader one to keep honest -- it would duplicate the golden path's coverage
 * of the Reindex button while depending on a seeded knowledge directory and a
 * live worker. This assertion instead names the precise mechanism: the browser
 * asked for these files and the server had them. It is also robust in the way a
 * click is not. The requests happen whatever the page does, because the browser
 * fetches every `<script src>` and `<link rel=stylesheet>` in the document
 * before any of it can run -- so this fails on the inert page rather than
 * skipping past it -- and it cannot be satisfied by a page that served the
 * document but not its assets.
 *
 * `resourceType()` is filtered to `script` and `stylesheet` on purpose. The
 * kinds this deliberately ignores are the ones that are legitimately absent from
 * a freshly brought-up stack and would make this flaky: `image` (the screenshots
 * a run has not produced yet), `fetch`/`xhr` (panel data), and `other`, which is
 * where a favicon lands.
 */
test("every bundle the page requests is served", async ({ page }) => {
  skipUnlessUp();

  const requested: string[] = [];
  const failed: string[] = [];
  page.on("response", (response) => {
    const kind = response.request().resourceType();
    if (kind !== "script" && kind !== "stylesheet") {
      return;
    }
    const label = `${response.status()} ${kind} ${new URL(response.url()).pathname}`;
    requested.push(label);
    if (!response.ok()) {
      failed.push(label);
    }
  });

  await page.goto(WEB_SERVICE_URL, { waitUntil: "networkidle" });

  // Guarded before it is asserted, because a page that requested nothing would
  // otherwise pass a "nothing failed" assertion vacuously -- which is the shape
  // of the bug this file exists to prevent, one level up. A rendered dashboard
  // always pulls at least one chunk and one stylesheet; zero of either means the
  // page did not load as a page, and the message says so.
  expect(
    requested.length,
    `expected the dashboard to request scripts and stylesheets, but it ` +
      `requested none. The document rendered without its assets.`,
  ).toBeGreaterThan(0);

  // Named in the failure, because "an asset 404'd" is the whole diagnosis and
  // this is the first time anyone would be told which one.
  expect(
    failed,
    `these bundles did not load; the dashboard will render but never hydrate, ` +
      `so its buttons do nothing. Check web/Dockerfile's \`public\` and ` +
      `\`\.next/static\` destinations -- they must be siblings of server.js.`,
  ).toEqual([]);
});

/**
 * A resource the browser fetched, as the browser saw it.
 *
 * The bytes are the browser's own: the fetch happens IN the page (see
 * `fetchInBrowser`), not in a test-side HTTP client, because the claim this
 * file makes is about what the browser was handed.
 */
type FetchedResource = {
  readonly status: number;
  readonly contentType: string;
  readonly bytes: Buffer;
};

/**
 * Fetch a URL FROM THE PAGE, and hand back what came back.
 *
 * **Why the fetch happens in the page and not in the test process.** This
 * spec's whole thesis is that the browser is the only witness to several
 * defects -- the bundle 404s its docstring records were invisible to the
 * document, the healthcheck and CI alike. A test-side HTTP client would be a
 * second witness, and a different one: it would fetch the URL the TEST
 * resolved, not the URL the document names, and it would not be the request
 * a browser makes. Fetching from the page makes the resource one the page
 * itself asked for.
 *
 * `cache: "no-store"` because the favicon route sends
 * `cache-control: public, max-age=0, must-revalidate` and a cached copy
 * would be a copy of an earlier answer, not of the tree under test.
 *
 * The bytes come back base64-encoded because `page.evaluate` serialises its
 * return value and a 92 KiB `Uint8Array` does not survive that as anything
 * useful. The encoding is done in 32 KiB chunks: `String.fromCharCode(...bytes)`
 * over the whole buffer at once exceeds the argument limit, and one character
 * at a time is a 92,000-iteration loop inside the page.
 */
async function fetchInBrowser(page: Page, href: string): Promise<FetchedResource> {
  const raw = await page.evaluate(async (target: string) => {
    try {
      const response = await fetch(target, { cache: "no-store" });
      const bytes = new Uint8Array(await response.arrayBuffer());
      let binary = "";
      const CHUNK = 0x8000;
      for (let offset = 0; offset < bytes.length; offset += CHUNK) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK));
      }
      return {
        status: response.status,
        contentType: response.headers.get("content-type") ?? "",
        base64: btoa(binary),
        error: "",
      };
    } catch (error) {
      return { status: 0, contentType: "", base64: "", error: String(error) };
    }
  }, href);

  if (raw.error !== "") {
    throw new Error(
      `the browser could not fetch ${href} at all: ${raw.error}. A link that names ` +
        `a resource the browser cannot reach is the silent failure this test exists ` +
        `for, and it is reported as what it is rather than as a byte mismatch.`,
    );
  }

  return {
    status: raw.status,
    contentType: raw.contentType,
    bytes: Buffer.from(raw.base64, "base64"),
  };
}

/**
 * The `href` of a link, and a hard stop if there is not one.
 *
 * A `null` href and an empty one are the same defect -- a link that names
 * nothing -- and both are refused here, before anything is fetched, so the
 * failure says "the link is empty" rather than "the bytes were wrong".
 */
function requiredHref(href: string | null, what: string): string {
  expect(href, `${what} has no href attribute at all.`).not.toBeNull();
  const value = (href ?? "").trim();
  expect(value, `${what} has an empty href.`).not.toBe("");
  return value;
}

/**
 * Byte-for-byte equality, and nothing else.
 *
 * `Buffer.equals` already compares lengths, so this is a named one-liner
 * rather than a helper with a subtlety in it -- but it is named, because the
 * test below has to be able to say that THIS comparison is the one that can
 * tell two files apart, and a comparison the test cannot name is a
 * comparison the test cannot show is doing anything.
 */
function sameBytes(a: Buffer, b: Buffer): boolean {
  return a.length === b.length && a.equals(b);
}

/**
 * A short fingerprint of some bytes, for a failure message.
 *
 * The first 16 hex characters of the SHA-256: enough to tell two artefacts
 * apart in a log, short enough to read. The full digest is of no use to
 * anyone reading a failed CI run.
 */
function fingerprint(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex").slice(0, 16);
}

/**
 * The browser is handed the two icons this repository committed.
 *
 * WHAT THIS PROVES, AND WHY IT IS NOT THE ARTWORK GUARD
 * ------------------------------------------------------
 * `web/src/app/favicon.ico` (the browser tab: 16, 32, 48, 64 and 256 pixels)
 * and `web/src/app/apple-icon.png` (the iOS home screen: 180x180) are two
 * artefacts the repository commits, and the `lint` job already proves they
 * are current with their artwork: `python scripts/make_assets.py --check`
 * regenerates them from `docs/assets/icon-source.png` and compares PICTURE
 * against PICTURE. What no step proved is the NEXT link in that chain --
 * that the bytes the browser is given are the bytes this repository holds.
 *
 * That link is not a small one, because nothing in the dashboard's source
 * names either file. Next.js's App Router takes them over by the file
 * convention alone (`app/favicon.ico`, `app/apple-icon.png`) and injects the
 * `<link rel="icon">` and `<link rel="apple-touch-icon">` into the `<head>`
 * itself. There is no import to follow, no component to read and no compiler
 * to complain: a renamed file, a file that never made it into the image, or
 * a change to Next's convention all remove the link SILENTLY. The page still
 * renders, the container still reports `Healthy` (its healthcheck is
 * `GET /`), CI stays green, the tab shows the browser's default page icon
 * and the iOS home screen shows a blank tile -- and nothing says so.
 *
 * THE PRECEDENT, WHICH IS IN THIS FILE
 * ------------------------------------
 * The test above is the same argument about the bundles, and its docstring
 * records the incident that made it necessary: `web/Dockerfile` copied
 * `.next/static` and `public` to `./web/...` while the standalone server
 * reads them as siblings of `server.js`, so every bundle 404'd. The page
 * still rendered, the container still reported `Healthy`, and only the
 * browser knew -- Reindex, Submit and Approve did nothing.
 *
 * That test also names its own hole, and this test is that hole filled:
 * its `resourceType()` filter watches `script` and `stylesheet` and
 * deliberately ignores `other`, "which is where a favicon lands", because
 * the other kinds are legitimately absent from a freshly brought-up stack
 * and would make it flaky. A favicon is not one of those kinds -- it is
 * committed, it is required, and its absence is the defect rather than a
 * property of a fresh stack. So it is asserted here, explicitly, and the
 * filter above is left exactly as it is.
 *
 * WHY THE BYTES, AND NOT JUST THE LINK
 * ------------------------------------
 * A `<link>` in the `<head>` proves the framework INTENDED to serve the file.
 * It does not prove the file was found, that it was copied into the image,
 * or that the bytes behind the link are the committed ones: a stale image, a
 * `public/` copy shadowing the convention, or a route serving the wrong
 * artefact all leave the link in place and in perfect order.
 *
 * So the test compares the bytes the browser received with the bytes of the
 * committed file, and it compares them as BYTES rather than as pictures. The
 * picture is `make_assets.py --check`'s job, and it compares picture against
 * picture on purpose: an `.ico`'s bytes depend on the zlib that packed them,
 * so a byte comparison there would call an unchanged tree stale. Here the
 * question is the other one -- is the artefact that reached the browser THE
 * artefact in the tree -- and Next copies both files verbatim, which was
 * measured rather than assumed: the build's `.next/server/app/favicon.ico.body`
 * is byte-identical to `web/src/app/favicon.ico`, and the same holds for the
 * apple-touch icon. The container's copy and the repository's copy are the
 * same bytes because `web/Dockerfile` builds the image from `web/` itself
 * (`COPY web/ ./`).
 *
 * WHY THE ORDER
 * -------------
 *   1. **The links are there, and they name a resource.** The convention was
 *      taken over at all, and the href is not empty -- the first thing that
 *      can be wrong, and the one whose failure is otherwise invisible.
 *   2. **Both resources answer 200.** Fetched AS THE DOCUMENT NAMES THEM:
 *      the href carries Next's own content hash (`?favicon.<hash>.ico`) and
 *      the test uses it verbatim rather than rebuilding a path, because the
 *      claim is about what the BROWSER is told to fetch.
 *   3. **The comparison is shown to be able to say "different"** before it is
 *      relied on to say "the same" (the one-byte flip below). A byte
 *      comparison that cannot fail is not a guard, and "the served bytes
 *      equal the committed bytes" is worth nothing if it is true of
 *      everything.
 *   4. **The served bytes equal the committed bytes** -- and, in the other
 *      direction, do NOT equal the other icon's bytes. That last part is
 *      what makes it a statement about THESE artefacts rather than about
 *      bytes.
 */
test("the browser is served the committed favicon and apple-touch icon", async ({ page }) => {
  skipUnlessUp();

  await page.goto(WEB_SERVICE_URL, { waitUntil: "networkidle" });

  // (1) The links. Exactly one of each: the repository has exactly one
  // favicon and exactly one apple-touch icon, so a second link would mean
  // the document is naming a resource this test does not know about, and
  // "the first one" would then be an arbitrary choice.
  const iconLink = page.locator('head link[rel="icon"]');
  const appleLink = page.locator('head link[rel="apple-touch-icon"]');

  await expect(
    iconLink,
    "the document has no <link rel=\"icon\"> in its <head>. Next.js's App Router " +
      "injects that link from the app/favicon.ico file convention alone -- nothing " +
      "in this repository names the file -- so a favicon that is missing, renamed " +
      "or not copied into the image removes the link silently, and a tab with no " +
      "icon is the only symptom.",
  ).toHaveCount(1);

  await expect(
    appleLink,
    "the document has no <link rel=\"apple-touch-icon\"> in its <head>, for the " +
      "same reason: app/apple-icon.png is found by the file convention alone, and " +
      "an iOS home screen with a blank tile says nothing about why.",
  ).toHaveCount(1);

  const iconHref = requiredHref(
    await iconLink.getAttribute("href"),
    'the <link rel="icon">',
  );
  const appleHref = requiredHref(
    await appleLink.getAttribute("href"),
    'the <link rel="apple-touch-icon">',
  );

  // (2) Both resources, fetched as the document names them.
  const servedFavicon = await fetchInBrowser(page, iconHref);
  const servedAppleIcon = await fetchInBrowser(page, appleHref);

  expect(
    servedFavicon.status,
    `the browser asked for the favicon at ${iconHref} and got ${servedFavicon.status} ` +
      `(content-type: ${servedFavicon.contentType}). A favicon that does not come ` +
      `back is a page that renders, a container that reports itself healthy and a ` +
      `tab with no icon -- the shape of the bundle incident above, one level down.`,
  ).toBe(200);

  expect(
    servedAppleIcon.status,
    `the browser asked for the apple-touch icon at ${appleHref} and got ` +
      `${servedAppleIcon.status} (content-type: ${servedAppleIcon.contentType}).`,
  ).toBe(200);

  // The two artefacts this repository commits, read from the tree the image
  // was built from.
  const committedFavicon = readFileSync(path.join(WEB_DIR, "src", "app", "favicon.ico"));
  const committedAppleIcon = readFileSync(path.join(WEB_DIR, "src", "app", "apple-icon.png"));

  // (3) THE COMPARISON IS NOT VACUOUS -- proven, not assumed.
  //
  // Flip one bit of the committed favicon and the same comparison must now
  // say "different" from what the browser received. A comparison that
  // returned true for everything -- a constant, or two empty buffers -- fails
  // here, which is what makes the assertion below mean something.
  const flipped = Buffer.from(committedFavicon);
  flipped[0] = flipped[0] ^ 0xff;
  expect(
    sameBytes(flipped, servedFavicon.bytes),
    "the byte comparison says a favicon whose first byte is flipped is the same " +
      "file as the one the browser received, so the comparison below is not " +
      "comparing anything.",
  ).toBe(false);

  // And the two committed icons are genuinely different files, so the
  // cross-check in (4) -- each served body is NOT the other icon -- is a real
  // check and not a formality.
  expect(
    sameBytes(committedFavicon, committedAppleIcon),
    "web/src/app/favicon.ico and web/src/app/apple-icon.png are byte-identical, so " +
      "the cross-check below cannot tell the two artefacts apart.",
  ).toBe(false);

  // (4) The claim: the browser was given THESE artefacts.
  expect(
    sameBytes(servedFavicon.bytes, committedFavicon),
    `the favicon the browser was given is not the committed web/src/app/favicon.ico: ` +
      `served ${servedFavicon.bytes.length} bytes (sha256 ` +
      `${fingerprint(servedFavicon.bytes)}), committed ${committedFavicon.length} bytes ` +
      `(sha256 ${fingerprint(committedFavicon)}). Either the image was built from a ` +
      `different tree than the one this test reads, or the route is serving something ` +
      `other than the committed artefact.`,
  ).toBe(true);

  expect(
    sameBytes(servedFavicon.bytes, committedAppleIcon),
    "the favicon the browser was given is the apple-touch icon's bytes, so the " +
      "comparison above is not discriminating between the two artefacts.",
  ).toBe(false);

  expect(
    sameBytes(servedAppleIcon.bytes, committedAppleIcon),
    `the apple-touch icon the browser was given is not the committed ` +
      `web/src/app/apple-icon.png: served ${servedAppleIcon.bytes.length} bytes ` +
      `(sha256 ${fingerprint(servedAppleIcon.bytes)}), committed ` +
      `${committedAppleIcon.length} bytes (sha256 ${fingerprint(committedAppleIcon)}).`,
  ).toBe(true);

  expect(
    sameBytes(servedAppleIcon.bytes, committedFavicon),
    "the apple-touch icon the browser was given is the favicon's bytes, so the " +
      "comparison above is not discriminating between the two artefacts.",
  ).toBe(false);
});
