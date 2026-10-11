/**
 * The golden path, walked in a browser start to finish.
 *
 * This is the artefact `docs/milestones.md` §M7's criterion asked for: "The
 * golden path is walkable in a browser start to finish." It drives the real
 * deployment -- **api + worker + web + postgres**, brought up by Compose --
 * through the dashboard's own controls, clicking, not by calling the API
 * directly. A `fetch` from this test to `/api/...` would defeat the criterion
 * entirely, so there is none: every state change below is a click on a control
 * a human would use.
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
  stack = await startStackOrSkip();
});

test.afterAll(() => {
  if (stack !== null && !stack.skip) {
    stack.teardown();
  }
});

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
  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("heading", { name: /^Run / })).toBeVisible();
  // The parked banner is the dashboard's own words for the approval gate.
  await expect(page.getByText(/Parked\. Nothing has been sent\./)).toBeVisible({
    timeout: 90_000,
  });
  // The Timeline panel is the trace screen: it is rendered from
  // GET /api/runs/{id}/trace, server-ordered. Assert it actually rendered
  // steps rather than the "no steps yet" empty state.
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
  await expect(page.getByText(/No steps recorded yet\./)).not.toBeVisible();

  // The Sources panel must cite the two golden-path documents (M6).
  for (const doc of EXPECTED_CITATIONS) {
    await expect(page.getByText(doc, { exact: true })).toBeVisible({ timeout: 30_000 });
  }

  // -- 4. Approve the parked refund, by clicking the control ---------------
  await page.goto("/approvals");
  // The inbox is filtered to pending by default; the golden-path refund is the
  // card whose run is this run.
  const card = page
    .locator("li")
    .filter({ has: page.getByRole("button", { name: /^Approve$/ }) })
    .filter({ hasText: runId.slice(0, 8) });
  // The card may not carry the run id visibly; fall back to the first pending
  // Approve button -- there is exactly one parked refund in this fresh stack.
  const approve = (await card.count()) > 0
    ? card.getByRole("button", { name: /^Approve$/ })
    : page.getByRole("button", { name: /^Approve$/ }).first();
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await approve.click();
  // The card becomes a statement ("Approved ... the run moved to ..."), which is
  // the dashboard confirming the decision landed and did not just look busy.
  await expect(page.getByText(/Approved/).first()).toBeVisible({ timeout: 30_000 });

  // -- 5. Reload the trace screen; assert the terminal status --------------
  await expect
    .poll(
      async () => {
        await page.goto(`/runs/${runId}`);
        return (await page.getByText(/^Completed$/).count()) > 0;
      },
      { timeout: 90_000, intervals: [2_000] },
    )
    .toBe(true);
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
