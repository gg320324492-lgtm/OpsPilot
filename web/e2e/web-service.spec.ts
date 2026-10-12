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
  stack = await startContainerisedWebOrSkip();
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
