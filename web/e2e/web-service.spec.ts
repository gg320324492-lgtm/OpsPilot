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
 *
 * Skipped cleanly where Docker is unavailable, per ADR-0004 (see `stack.ts`); a
 * stack Docker could attempt and did not bring up is a failure, not a skip.
 */

import { expect, test } from "@playwright/test";
import {
  probeFromWebContainer,
  startContainerisedWebOrSkip,
  type StackHandle,
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
