/**
 * The 409 an expired approval produces: nobody decided, not somebody else.
 *
 * ## Why this file is separate from `failure-states.spec.ts`
 *
 * Because the state needs **a run of its own**, and the fake model provider
 * cannot give one.
 *
 * `FakeModelProvider` in scenario mode answers from the recorded calls **in
 * order**, with a cursor per method. So it can drive exactly one golden run
 * per scenario: a second ticket submitted against the same scenario finds the
 * cursor past the end of the recorded calls and the run never gets anywhere —
 * `UnmatchedFixtureError: no recorded generate_structured response matched
 * scenario 'duplicate_charge'`. It happened exactly that way when this case
 * first lived in `failure-states.spec.ts` and submitted a second ticket.
 *
 * And the case cannot share that file's single golden approval either: the
 * case above it there *decides* the approval, and a decided approval is not a
 * pending one to expire. So this file starts its own stack, submits its own
 * ticket, and drives its own run.
 *
 * ## What the case is about
 *
 * An approval nobody decided within `APPROVAL_TTL_SECONDS` is swept to
 * `expired` by the worker, and a decision on it is refused with **409
 * `approval_expired`**. That is a different answer from **409
 * `approval_already_decided`**, and the difference is the whole point: the
 * first means *nobody was here at all*, and the second means *somebody else
 * got there first*. Reporting an expired approval as "already decided" would
 * put a person in a sentence that is false, on the one screen an operator
 * reads to find out what happened to their refund.
 *
 * The API's own refusal is pinned in Python
 * (`tests/integration/test_approval_expiry.py`); what this file is about is
 * what the **dashboard** says.
 */

import { expect, test, type Page } from "@playwright/test";

import {
  expireApprovalsForRunThroughTheRepository,
  startStackOrSkip,
  workerReport,
  type StackHandle,
} from "./stack";

const SUBJECT = "We were charged twice for invoice INV-2026-384";
const CUSTOMER_EMAIL = "billing@acme.example";

/** The run this file's own ticket drives, parked on the approval gate. */
let goldenRunId: string | null = null;

let stack: StackHandle | null = null;

test.beforeAll(async ({ browser }) => {
  // The stack start includes a Docker **build**, and a build after a source
  // change is minutes of work that happens inside this hook. Playwright's
  // default hook budget is the test timeout (5 minutes), which the build can
  // exceed -- and when it does, the hook is killed and the run reports a
  // teardown artefact ("browserContext.close: ENOENT") instead of the thing
  // that actually happened. The budget below is the build's.
  test.setTimeout(900_000);
  stack = await startStackOrSkip("expired-approval");
  if (stack.skip) {
    return;
  }
  // The knowledge base, the way `failure-states.spec.ts` does it: the API does
  // not ingest at start-up, so a fresh Postgres holds zero chunks and the run
  // would retrieve nothing, abstain, and complete without ever reaching the
  // gates under test. Clicked, because the control is what is being
  // exercised.
  //
  // `beforeAll` is handed no `page` fixture, so this opens one for the setup
  // and closes it again.
  const page = await browser.newPage();
  try {
    await page.goto("/knowledge");
    await page.getByRole("button", { name: /Reindex knowledge/i }).click();
    await expect(page.getByText(/\d+ seen/)).toBeVisible({ timeout: 60_000 });
    goldenRunId = await submitTicketThroughTheForm(page);
  } finally {
    await page.close();
  }
});

test.afterAll(async () => {
  if (stack !== null && !stack.skip) {
    // Returned, not merely called: teardown waits until the dashboard's port is
    // released, and that wait is asynchronous.
    await stack.teardown();
  }
});

test("a click on an expired approval says nobody decided, not that somebody else did", async ({
  page,
  context,
}) => {
  skipUnlessUp();
  const run = goldenRunId;
  if (run === null) {
    test.skip(true, "the ticket was submitted -- the setup before this file did not reach it");
    return;
  }

  // Wait for the run to park. The Approve button only exists once a run has
  // proposed a refund and stopped at gate 5, so waiting on the button would be
  // waiting on the worker with a 15-second expectation timeout and no
  // diagnosis when it did not come.
  await withWorkerDiagnosis("waiting for the run to park on the approval gate", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${run}`);
          return (await page.getByText(/Parked\. Nothing has been sent\./).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );

  // Both tabs load the inbox while the card is still PENDING. This is the
  // two-tabs case `decision-actions.tsx`'s docstring is written about: the
  // second tab holds a card the API no longer considers pending, and the only
  // honest thing it can do is click and be told what happened.
  await page.goto("/approvals");
  const card = page.locator("li").filter({ hasText: "arguments_snapshot" });
  await expect(card.getByRole("button", { name: /^Approve$/ })).toBeVisible({ timeout: 30_000 });
  const secondTab = await context.newPage();
  await secondTab.goto("/approvals");
  const secondCard = secondTab.locator("li").filter({ hasText: "arguments_snapshot" });
  await expect(secondCard.getByRole("button", { name: /^Approve$/ })).toBeVisible({
    timeout: 30_000,
  });

  // Now the approval times out. The staging is the worker's own TTL sweep --
  // the same conditional `UPDATE` the worker runs, with the run's filter added
  // so it cannot touch another run's approval.
  const expired = expireApprovalsForRunThroughTheRepository(run);
  expect(expired, "there was no pending approval to expire").toBe(1);

  // The second tab, still holding the card it loaded, clicks. The Route Handler
  // forwards the 409 with its own status, and this is the whole claim under
  // test: that what reaches the screen is the API's own answer, not a generic
  // failure invented by the dashboard.
  await secondCard.getByRole("button", { name: /^Approve$/ }).click();
  // Scoped to the panel by the one control only it has. A bare
  // `getByRole("alert")` is ambiguous on every App Router page: Next.js renders
  // its own `role="alert"` route announcer for accessibility.
  const panel = secondTab.locator('div[role="alert"]').filter({ hasText: "Dismiss" });
  await expect(panel).toBeVisible({ timeout: 30_000 });

  // The point of the whole branch: it does not say somebody decided. "Already
  // decided" would put a person in a sentence that is false -- nobody was here
  // at all, and the run is still parked.
  await expect(panel).toContainText("This approval expired — this click granted nothing.");
  await expect(panel).toContainText("HTTP 409");
  await expect(panel).toContainText("code: approval_expired");

  // ...and it must not read as the other 409, which says a *person* decided.
  await expect(panel).not.toContainText("Already decided");
  await expect(panel).not.toContainText("was already approved by operator at");
  // ...nor as a generic refusal, which is what an unrecognised 409 would read
  // as (`decision-error.ts`'s `generic` branch).
  await expect(panel).not.toContainText("The API refused this decision with 409");
  // The API *answered* -- with a 409 -- so the panel must not say the
  // dashboard could not reach it. That sentence is the transport failure's,
  // and an operator reading it would go looking at Next.js instead of at the
  // decision. (The wording this used to assert, "Something went wrong", is
  // not in either catalogue: the assertion could not fail, which made it a
  // promise about a sentence the product does not contain.)
  await expect(panel).not.toContainText("could not reach");
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

/**
 * Run an assertion, and if it fails, append the worker's own account of itself.
 *
 * The same idea as the other specs' copies of this, for the same reason: a
 * timeout waiting for the WORKER says nothing about the worker unless the
 * message carries the worker's container state and its log. Read before
 * `afterAll` runs its `docker compose down -v`, which is the only moment the
 * logs still exist.
 */
async function withWorkerDiagnosis<T>(what: string, body: () => Promise<T>): Promise<T> {
  try {
    return await body();
  } catch (error) {
    const suffix =
      `\n\n--- why this matters: ${what} waits for the WORKER, not the browser. ---\n` +
      `${workerReport()}\n` +
      `A run nothing claims stays at "Received" with no steps, and a run the worker ` +
      `cannot finish never reaches a terminal state, while the API keeps answering every ` +
      `request. (stack.ts also writes these logs to web/test-results/compose-logs/ before ` +
      `teardown, so they travel with the run's Playwright artefact.)`;
    if (error instanceof Error) {
      error.message += suffix;
      throw error;
    }
    throw new Error(`${String(error)}${suffix}`);
  }
}

/**
 * Submit the README's ticket through the form and return the run id.
 *
 * The one-click fill plus Submit, asserted rather than bypassed: retyping the
 * values would make this a test of nothing but the API. The run link is read
 * from the success panel, which is the only `/runs/{uuid}` link on this screen.
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
