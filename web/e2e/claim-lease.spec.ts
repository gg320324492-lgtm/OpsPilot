/**
 * A run a worker *holds* reads differently from one nothing has claimed.
 *
 * ## The defect this closes
 *
 * A claim takes a run without changing its status
 * (`adapters/persistence/repositories.py::claim_next`), so a run that was claimed
 * and then abandoned renders character for character like a run nothing ever
 * claimed — and, worse, told the operator so. `docs/limitations.md` §2 records
 * the measurement: on a live stack with a healthy worker, a `received` run
 * carrying an unexpired lease went unclaimed for the full **300 s** of
 * `WORKER_CLAIM_LEASE_SECONDS`, and the page said `Status: Received`,
 * `0 steps, as the server ordered them`, `No steps recorded yet.` — plus the
 * sentence "the worker has not claimed it yet", which was false for five
 * minutes.
 *
 * The lease cannot be shortened (it has to exceed a run's p95 drive duration,
 * 276.7 s, or a long run loses it mid-drive), so the bound this adds is on
 * *detecting* the state, not on recovering from it. `claim_lease` on
 * `GET /api/runs/{id}` is that bound.
 *
 * ## Why these are three tests and not one
 *
 * The claim of this file is the **contrast**, and a contrast needs both sides of
 * it asserted on the same page by the same locators. Asserting only the claimed
 * case would pass against a dashboard that says "a worker holds this" about
 * every run; asserting only the unclaimed case is what the suite already did,
 * and it is what the lease was invisible behind. So the three tests read one
 * page three times: unclaimed, claimed and live, claimed and lapsed.
 *
 * ## How each state is produced
 *
 * The same rule `failure-states.spec.ts` holds to: nothing is written to the
 * database by hand, and the state is reached the way an operator reaches it.
 *
 * | State | How the system gets there |
 * | --- | --- |
 * | Never claimed | The worker container is stopped, then the ticket is submitted |
 * | Claimed and live | `SqlRunStore.claim_next` itself, with the worker still stopped |
 * | Claimed and lapsed | The same call with a lease of zero seconds |
 *
 * The middle row is the one that cannot come from a live worker, and
 * `claimOldestRunThroughTheRepository`'s docstring is the argument: `drain_once`
 * claims and then immediately writes the first `state_change` step, so the
 * claimed-but-silent window is milliseconds wide and cannot be caught by
 * polling. It is produced by calling the claim itself, which is the same method
 * a worker calls, rather than by an `UPDATE` no code in this repository runs.
 *
 * The third row uses `lease_seconds: 0`, which is not a special case the API
 * offers — it is `claim_next`'s own arithmetic (`now + timedelta(seconds=0)`),
 * and it produces exactly the row a worker that died mid-claim leaves behind.
 * Waiting out a real 300 s lease would test the same thing five minutes later.
 *
 * ## What is asserted, and what is deliberately not
 *
 * Every assertion below reads a sentence off the rendered page, because the
 * defect was a sentence: the API could have carried the lease the whole time and
 * the page would still have lied. The holder is asserted by name, since *who*
 * holds a claim is half of what makes it actionable.
 *
 * `failure-states.spec.ts` asserts the "no worker claimed it" text for a run it
 * produces by stopping the worker, which leaves the run genuinely unclaimed. That
 * assertion is still true, and it is true here too: the claimed case replaces
 * the sentence rather than appending to it, so a claimed run never contains the
 * words the unclaimed case is asserted on. The second test asserts their absence
 * directly, which is what keeps the two specs from drifting into disagreeing.
 *
 * Skipped cleanly where Docker is unavailable, per ADR-0004's pattern (see
 * `stack.ts`), and the per-test skips name which prerequisite was missing.
 */

import { expect, test, type Page } from "@playwright/test";
import {
  claimOldestRunThroughTheRepository,
  startStackOrSkip,
  stopWorker,
  type StackHandle,
} from "./stack";

// The README's ticket, verbatim -- the same values the form's one-click fill puts
// in the fields, so submitting through that button is still a browser click and
// not a `fetch`.
const SUBJECT = "We were charged twice for invoice INV-2026-384";
const CUSTOMER_EMAIL = "billing@acme.example";

/**
 * The holder, which the page has to name.
 *
 * Deliberately not `worker-compose` (the deployment default): the whole point of
 * projecting `claimed_by` is that the operator learns *which* worker to go and
 * look at, and a test that used the default could not tell a page rendering the
 * holder apart from one rendering a hard-coded string.
 */
const STAND_IN_WORKER = "e2e-stand-in-worker";

/** A lease long enough that the run cannot lapse while the page is read. */
const LIVE_LEASE_SECONDS = 300;

/** The run nothing claimed, then the run whose claim was lapsed on purpose. */
let unclaimedRunId: string | null = null;
let lapsedRunId: string | null = null;

let stack: StackHandle | null = null;

test.beforeAll(async ({ browser }) => {
  stack = await startStackOrSkip("claim-lease");
  if (stack.skip) {
    return;
  }
  // **No reindex, unlike every other spec that starts this stack.** Nothing in
  // this file is ever driven: the worker is stopped for the whole run and both
  // leases are taken by `claimOldestRunThroughTheRepository`, which does not
  // drive what it claims. Ingesting the corpus would add a minute of the run's
  // wall clock to prove something about a path this file does not take.
  //
  // The worker is stopped *first*, before the ticket exists, so the run cannot
  // be claimed in the gap between submitting it and asserting on it. That gap is
  // the same few-milliseconds window the helper's docstring describes, and a
  // race is not a test.
  //
  // `beforeAll` is handed no `page` fixture (Playwright's page fixture is
  // per-test), so this opens one for the setup and closes it again. The `browser`
  // fixture IS worker-scoped and is the supported way to do that.
  const page = await browser.newPage();
  try {
    stopWorker();
    unclaimedRunId = await submitTicketThroughTheForm(page);
  } finally {
    await page.close();
  }
});

test.afterAll(async () => {
  if (stack !== null && !stack.skip) {
    // Returned, not merely called: teardown waits until the dashboard's port is
    // released, and that wait is asynchronous. See `golden-path.spec.ts`'s
    // identical `afterAll` for why this is not fire-and-forget.
    await stack.teardown();
  }
});

// ---------------------------------------------------------------------------
// 1. Never claimed: the sentence that was false half the time is still true.
// ---------------------------------------------------------------------------

test("a run nothing has claimed says no worker has claimed it", async ({ page }) => {
  skipUnlessUp();
  skipUnlessReached("a ticket was submitted while no worker was polling", unclaimedRunId);

  await page.goto(`/runs/${unclaimedRunId}`);

  // Never claimed: `received`, with no worker having touched it. The badge is a
  // `<span>` read as one, because "Received" is also the word on the Runs filter
  // and matching that would assert the wrong element.
  await expect(page.locator("span").filter({ hasText: /^Received$/ }).first()).toBeVisible();
  // The screen distinguishes "no steps yet" from "nothing to show", and names the
  // cause: an unclaimed run and a run with no work are both "Received with no
  // steps", and only one of them is a problem.
  await expect(page.getByText("No steps recorded yet.")).toBeVisible();
  await expect(page.getByText(/the worker has not claimed it yet/)).toBeVisible();
  await expect(page.getByText("0 steps, as the server ordered them")).toBeVisible();

  // And the run's own field says the same thing, in the panel beside Started and
  // Completed -- so the fact survives once the timeline fills in.
  await expect(
    page.locator("dt").filter({ hasText: "Claim" }).locator("xpath=.."),
  ).toContainText("Not claimed");
  // Nothing on this page claims a holder. Asserted as an absence because the
  // defect was precisely a holder the page denied.
  await expect(page.getByText(/Held by/)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 2. Claimed and held: the same page, the other sentence.
// ---------------------------------------------------------------------------

test("the same run, once claimed, stops saying no worker claimed it", async ({ page }) => {
  skipUnlessUp();
  const runId = unclaimedRunId;
  if (runId === null) {
    test.skip(true, "a ticket was submitted while no worker was polling -- the setup did not reach it");
    return;
  }

  // The production claim, on this run. The worker is still stopped, so nothing
  // else can take the row, and the helper returns the run it actually claimed
  // rather than the one this test happened to submit.
  const claimed = claimOldestRunThroughTheRepository({
    workerId: STAND_IN_WORKER,
    leaseSeconds: LIVE_LEASE_SECONDS,
  });
  expect(claimed, "no run was claimable, so nothing claimed this one").not.toBeNull();
  expect(claimed!.id).toBe(runId);
  expect(claimed!.status).toBe("received");
  // The claim does not change the status. That is the whole premise: if it did,
  // the page would have said something and none of this would be needed.
  expect(claimed!.claimed_until).not.toBeNull();

  await page.goto(`/runs/${runId}`);

  // **The same notice, made true.** Not a second banner beside the first one --
  // the title is unchanged and the hint under it is what changed.
  await expect(page.getByText("No steps recorded yet.")).toBeVisible();
  // And the sentence it used to always carry is gone. This is the assertion that
  // makes the fix a fix: keeping it alongside the new one would leave the page
  // still telling an operator that nobody had claimed a run a worker is holding.
  await expect(page.getByText(/the worker has not claimed it yet/)).toHaveCount(0);
  // What replaces it names the holder and the bound, because "a worker has it"
  // without either is the same sentence with a noun swapped in.
  await expect(
    page.getByText(
      new RegExp(
        `A worker \\(${STAND_IN_WORKER}\\) claimed this run and has not written a step yet\\.`,
      ),
    ),
  ).toBeVisible();
  await expect(page.getByText(/The claim is held until /)).toBeVisible();
  // The run's own field agrees, and names the same holder.
  const claim = page.locator("dt").filter({ hasText: "Claim" }).locator("xpath=..");
  await expect(claim).toContainText(`Held by ${STAND_IN_WORKER} until`);
  // And the status is unchanged -- still `received`, still no steps. The page
  // distinguishes the two without inventing a status for either.
  await expect(page.locator("span").filter({ hasText: /^Received$/ }).first()).toBeVisible();
  await expect(page.getByText("0 steps, as the server ordered them")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 3. Claimed and lapsed: the third state, which is neither of the other two.
// ---------------------------------------------------------------------------

test("a claim that has lapsed says nobody is holding the run", async ({ page }) => {
  skipUnlessUp();
  if (unclaimedRunId === null) {
    test.skip(true, "a ticket was submitted while no worker was polling -- the setup did not reach it");
    return;
  }

  // A second run, so the run above keeps its live lease: `claim_next` will not
  // hand out a row whose deadline is in the future, and this test is about the
  // state *after* the deadline rather than about re-claiming.
  lapsedRunId = await submitTicketThroughTheForm(page);
  const claimed = claimOldestRunThroughTheRepository({
    workerId: STAND_IN_WORKER,
    leaseSeconds: 0,
  });
  expect(claimed, "the second run was not claimable").not.toBeNull();
  expect(claimed!.id).toBe(lapsedRunId);
  // Still `received`, still step-less -- the lease lapsed, nothing was driven.
  expect(claimed!.status).toBe("received");

  await page.goto(`/runs/${lapsedRunId}`);

  await expect(page.getByText("No steps recorded yet.")).toBeVisible();
  // Neither of the other two sentences: the run *was* claimed, and it is not
  // held now.
  await expect(page.getByText(/the worker has not claimed it yet/)).toHaveCount(0);
  await expect(page.getByText(/has not written a step yet/)).toHaveCount(0);
  await expect(page.getByText(/A worker claimed this run, and its claim expired at /)).toBeVisible();
  await expect(page.getByText(/No worker is holding it now/)).toBeVisible();
  // Recovery is automatic, so the page says so rather than leaving the operator
  // to work out that an expired lease is not a dead run.
  await expect(page.getByText(/becomes claimable again on the next poll/)).toBeVisible();

  const claim = page.locator("dt").filter({ hasText: "Claim" }).locator("xpath=..");
  await expect(claim).toContainText("nobody is holding it");
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Skip the calling test (with a reason) when the stack is not up. */
function skipUnlessUp(): void {
  if (stack === null) {
    test.skip(true, "the stack did not start");
  } else if (stack.skip) {
    // The reason NAMES why -- never a bare "skipped" (ADR-0004's habit).
    test.skip(true, stack.reason);
  }
}

/** Skip when the setup did not produce what this test reads. */
function skipUnlessReached(what: string, value: string | null): void {
  if (value === null) {
    test.skip(true, `${what} -- an earlier step in this file did not reach it`);
  }
}

/**
 * Submit the README's ticket through the form and return the run id.
 *
 * The one-click fill plus Submit, asserted rather than bypassed: retyping the
 * values would make this a test of nothing but the API. The run link is read
 * from the success panel, which is the only `/runs/{uuid}` link on this screen --
 * the ticket list below links to `/runs?ticket=...`, which is a filter, not a
 * run -- so the locator is unambiguous even after two tickets have been
 * submitted in one session.
 *
 * Deliberately duplicated from `failure-states.spec.ts` rather than hoisted into
 * `stack.ts`: that file is not to be edited by this work, and a helper imported
 * from it would couple two specs' assertions to one another's edits.
 */
async function submitTicketThroughTheForm(page: Page): Promise<string> {
  await page.goto("/tickets");
  await page.getByRole("button", { name: /Fill with the README/i }).click();
  await expect(page.getByLabel(/Subject/i)).toHaveValue(SUBJECT);
  await expect(page.getByLabel(/Customer email/i)).toHaveValue(CUSTOMER_EMAIL);
  await page.getByRole("button", { name: /^Submit ticket$/i }).click();

  const runLink = page.locator('a[href^="/runs/"]').first();
  await expect(runLink).toBeVisible({ timeout: 30_000 });
  const href = await runLink.getAttribute("href");
  expect(href).toMatch(/^\/runs\/[0-9a-f-]{36}$/);
  return href!.replace("/runs/", "");
}