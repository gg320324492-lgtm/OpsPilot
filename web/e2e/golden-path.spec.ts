/**
 * The golden path, walked in a browser start to finish.
 *
 * This is the artefact `docs/milestones.md` §M7's criterion asked for: "The
 * golden path is walkable in a browser start to finish." It drives the real
 * deployment -- **api + worker + web + postgres** -- through the dashboard's own
 * controls, clicking, not by calling the API directly. A `fetch` from this test
 * to `/api/...` would defeat the criterion entirely, so there is none: every
 * state change below is a click on a control a human would use.
 *
 * The stack is assembled by `stack.ts`: postgres + migrate + api + worker with
 * `docker compose` on the repository's own `docker-compose.yml`, and the
 * dashboard on the host with `node .next/standalone/server.js`. The dashboard is
 * NOT the compose `web` service, because that container cannot reach the API
 * from inside itself -- see `stack.ts`'s module docstring for the diagnosis.
 *
 * What it proves, and why each step is a click:
 *
 *   1. **Reindex by clicking** the "Reindex knowledge/" button on Knowledge.
 *      The API does NOT reindex at start-up (`api/app.py` builds the retrieval
 *      stack but runs no ingest), so a fresh Postgres has 0 chunk rows and the
 *      worker would retrieve nothing, abstain and escalate -- no refund. The
 *      reindex is what writes the embeddings the run needs, and on Postgres
 *      they persist in the `vector` column, so the *worker* (a second process)
 *      searches the same vectors the *api* (the process that owns reindex)
 *      wrote. That cross-process retrieval is the whole reason this test is not
 *      the GIF's single-process SQLite script.
 *
 *   2. **Submit the golden-path ticket** through the Tickets form (the "Fill
 *      with the README's ticket" one-click fill, then Submit).
 *
 *   3. **Follow the run to its trace screen** -- the Run Detail page, whose
 *      Timeline panel renders `GET /api/runs/{id}/trace` server-ordered -- and
 *      wait for it to park on the approval gate.
 *
 *   4. **Approve the parked refund by clicking "Approve"** on the Approvals
 *      screen. Approval does not execute the refund in the request; it releases
 *      the run to the worker, which performs it.
 *
 *   5. **Reload the trace screen** and assert the terminal status `Completed`.
 *
 *   6. **Ask the billing server** whether exactly one transaction (`TX-88219`)
 *      was refunded -- the server's answer, not the run's own account of
 *      itself (this repository's core habit; a `COMPLETED` status proves
 *      nothing about whether a refund row exists).
 *
 * Skipped cleanly where Docker is unavailable, per ADR-0004's pattern (see
 * `stack.ts`).
 */

import { expect, test } from "@playwright/test";
import {
  refundedTransactionIds,
  startStackOrSkip,
  type StackHandle,
  workerReport,
} from "./stack";

// The README's ticket, verbatim (the form's one-click fill uses exactly these).
const SUBJECT = "We were charged twice for invoice INV-2026-384";
const CUSTOMER_EMAIL = "billing@acme.example";

// The seeded duplicate charge: the second of the two charges for INV-2026-384.
const REFUNDED_TRANSACTION = "TX-88219";

// The two documents §M6 requires the golden path to cite.
const EXPECTED_CITATIONS = ["refund-policy.md", "duplicate-charge-sop.md"];

let stack: StackHandle | null = null;

test.beforeAll(async () => {
  stack = await startStackOrSkip("golden-path");
});

test.afterAll(() => {
  if (stack !== null && !stack.skip) {
    // Returned, not merely called: teardown waits until the dashboard's port is
    // released, and that wait is asynchronous. Fire-and-forget would let the run
    // finish -- and the next run begin -- while the old server still held the
    // port, which is the EADDRINUSE this suite used to fail with. A teardown
    // that cannot complete rejects here rather than being swallowed.
    return stack.teardown();
  }
});

/**
 * Run an assertion, and if it fails, append the worker's own account of itself.
 *
 * WHAT THIS IS FOR
 * ----------------
 * CI run 38202887580 failed at the park-wait below with
 * `expect(locator).toBeVisible() failed ... Timeout: 90000ms`, and the page
 * snapshot said `Status: Received` / `No steps recorded yet.` Every one of
 * those is a fact about the DASHBOARD. None of them is a fact about the WORKER,
 * and the worker is the process whose absence explains all of them -- a run
 * nothing ever claims stays at `received` with no steps, forever, while the api
 * answers every request perfectly. The CI job captured no worker output, so the
 * failure could not be told apart from a slow run, a broken claim query, or a
 * dashboard that re-rendered the wrong run.
 *
 * So when one of these waits times out, the message now carries the worker's
 * container state and its log. That is the difference between "the browser
 * could not see this text" and "the process that was supposed to produce it
 * never started".
 *
 * WHAT IT IS NOT
 * --------------
 * It changes no assertion. `body` is the unmodified assertion this spec makes --
 * same locators, same timeouts, same expectation -- and it is only ever called
 * with the same values it already used. A failure still fails; the M7 criterion
 * is not softened by a better error message, and nothing here skips, retries or
 * re-times anything.
 */
async function withWorkerDiagnosis<T>(what: string, body: () => Promise<T>): Promise<T> {
  try {
    return await body();
  } catch (error) {
    // Read BEFORE `afterAll` runs its `docker compose down -v`. After teardown
    // the containers are gone and this returns the same "(no output)" it would
    // return on a machine with no stack at all -- which is how this failure
    // became undiagnosable in the first place.
    const report = workerReport();
    const suffix =
      `\n\n--- why this matters: ${what} waits for the WORKER, not the browser. ---\n` +
      `${report}\n` +
      `A run nothing claims stays at "Received" with no steps, exactly as above, while ` +
      `the API keeps answering. If the worker is not polling, nothing on this page was ` +
      `ever going to appear. (stack.ts also writes these logs to ` +
      `web/test-results/compose-logs/ before teardown, so they travel with the run's ` +
      `Playwright artefact.)`;
    if (error instanceof Error) {
      error.message += suffix;
      throw error;
    }
    throw new Error(`${String(error)}${suffix}`);
  }
}

/** Skip the calling test (with a reason) when the stack is not up. */
function skipUnlessUp(): void {
  if (stack === null) {
    test.skip(true, "the stack did not start");
  } else if (stack.skip) {
    // The reason NAMES why -- never a bare "skipped" (ADR-0004's habit).
    test.skip(true, stack.reason);
  }
}

test("the golden path is walkable in a browser start to finish", async ({ page }) => {
  skipUnlessUp();

  // -- 1. Reindex the knowledge base, by clicking the control ---------------
  await page.goto("/knowledge");
  await page.getByRole("button", { name: /Reindex knowledge/i }).click();
  // The result panel states the counts. On a fresh Postgres this is the first
  // ingest, so documents are indexed; a re-run must be tolerated too (reindex
  // is idempotent and reports `0 indexed`), so the assertion is that the result
  // panel appeared -- not a specific count, which would make the test order-
  // dependent. What it must NOT see is the failure alert.
  await expect(page.getByText(/\d+ seen/, { exact: false })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/documents were found|The API answered/i)).not.toBeVisible();

  // -- 2. Submit the golden-path ticket through the form -------------------
  await page.goto("/tickets");
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  // The one-click fill put the README's values in the fields; assert so, then
  // submit. Asserting the fill (rather than retyping it) keeps this a test of
  // the dashboard's own affordance.
  await expect(page.getByLabel(/Subject/i)).toHaveValue(SUBJECT);
  await expect(page.getByLabel(/Customer email/i)).toHaveValue(CUSTOMER_EMAIL);
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();

  // The success panel names the new run; capture its id from the link href.
  const runLink = page.locator('a[href^="/runs/"]').first();
  await expect(runLink).toBeVisible({ timeout: 30_000 });
  const runHref = await runLink.getAttribute("href");
  expect(runHref).toMatch(/^\/runs\/[0-9a-f-]{36}$/);
  const runId = runHref!.replace("/runs/", "");

  // -- 3. Follow the run to its trace screen; wait for it to park ----------
  // The Run Detail page is a pure Server Component with NO client polling
  // (verified: no `use client` and no interval in `web/src/app/runs/`), so the
  // worker's progress is only visible on the next navigation. That is the
  // dashboard's own documented affordance -- the Tickets form says "watch it on
  // the run page, or reload in a moment" -- so this reloads, exactly as a human
  // would, rather than waiting on a page that will never change itself.
  await withWorkerDiagnosis("waiting for the run to park on the approval gate", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${runId}`);
          // The parked banner is the dashboard's own words for the gate.
          return (await page.getByText(/Parked\. Nothing has been sent\./).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );

  // On the parked screen: the Timeline (the trace screen, rendered from
  // GET /api/runs/{id}/trace, server-ordered) has real steps, and the Sources
  // panel cites the two golden-path documents (M6).
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
  await expect(page.getByText(/No steps recorded yet\./)).not.toBeVisible();
  for (const doc of EXPECTED_CITATIONS) {
    // `.first()`: retrieval returns the top-5 chunks, and a document can
    // contribute more than one (the golden path cites `duplicate-charge-sop.md`
    // twice), so the panel legitimately shows the same `document` on several
    // rows. The claim is "this document was cited", not "exactly once".
    await expect(page.getByText(doc, { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });
  }
  // The parked run must NOT yet be completed -- the gate is real. The status is
  // read from the `StatusBadge` (a `<span>`), not `getByText`, because the Run
  // panel's field LABEL is also the literal word "Completed" (a `<dt>`) and
  // matching it would assert the wrong thing.
  await expect(page.locator("span").filter({ hasText: /^Completed$/ })).toHaveCount(0);

  // -- 4. Approve the parked refund, by clicking the control ---------------
  await page.goto("/approvals");
  // Exactly one refund is parked in this fresh stack; its card carries the
  // Approve button. Click it -- the refund is released to the worker here, not
  // executed by this request.
  const approve = page.getByRole("button", { name: /^Approve$/ }).first();
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await approve.click();
  // The click landed: the decided card LEAVES the pending inbox. The Approvals
  // screen defaults to `status=pending` and `DecisionActions` calls
  // `router.refresh()` after a 200, so the row the API no longer considers
  // pending is removed and the inbox empties. That the empty state appears --
  // and it is the same run whose card was there a moment ago -- is the
  // UI-observable proof the approval was recorded, not a transient button state.
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible({
    timeout: 30_000,
  });

  // -- 5. Reload the trace screen; assert the terminal status --------------
  await withWorkerDiagnosis("waiting for the approved run to reach Completed", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${runId}`);
          // The status badge is a `<span>`; the word "Completed" also appears as a
          // `<dt>` field label, so the span is what distinguishes the STATUS from
          // the label.
          return (await page.locator("span").filter({ hasText: /^Completed$/ }).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );
  // The refund was actually executed (not just released): the tool call shows
  // as `executed`, and the customer reply is a refund, not an escalation.
  await expect(page.getByText("executed", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/Escalated\./)).not.toBeVisible();

  // -- 6. The server's answer: exactly one refunded transaction -------------
  // Read from the billing MCP server's store, not from the run's own status.
  const refunded = refundedTransactionIds("INV-2026-384");
  expect(refunded).toEqual([REFUNDED_TRANSACTION]);
});

/**
 * A second, separate test uses the SAME stack: the walkthrough above left the
 * run completed, so re-asserting the exactly-once property from a fresh browser
 * context (a fresh page, no in-memory carry-over) proves the property is the
 * server's and not this test's session state. It is deliberately a distinct
 * `test()` so a reader sees the two claims named separately.
 */
test("the refund executed exactly once, as the billing server reports it", async ({ page }) => {
  skipUnlessUp();

  // The dashboard on a fresh load shows zero parked approvals (the one we
  // approved is gone from the pending inbox), which is a UI-level restatement
  // of "the gate cleared once and did not re-open".
  await page.goto("/approvals?status=pending");
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible({
    timeout: 30_000,
  });

  // And the server itself: exactly one transaction for the invoice is refunded.
  expect(refundedTransactionIds("INV-2026-384")).toEqual([REFUNDED_TRANSACTION]);
});
