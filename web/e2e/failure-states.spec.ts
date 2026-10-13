/**
 * What the dashboard says when something goes wrong.
 *
 * ## Why this file exists
 *
 * `golden-path.spec.ts` walks the success path: reindex, submit, follow, approve,
 * and read the terminal state off the screen. That is the regression guard for
 * what the system does when it works, and nothing here replaces it. What it does
 * not do is assert anything about the states an operator has to *debug* -- and a
 * dashboard's failure states are the part a reviewer reads first, because they
 * are the part that claims the system is trustworthy when it is not.
 *
 * `docs/phase2-plan.md` §P2-C3 states the acceptance criterion: "e2e specs for
 * the failure states, asserting the UI says something true. A failure the UI
 * renders as 'something went wrong' is a defect even when the code is right."
 * So the assertions here are never "an alert appeared". Each one names a specific
 * fact -- who decided an approval and when, which configured rule refused a
 * refund, which reason a run carries -- and reads it out of the rendered page.
 *
 * ## How each state is produced
 *
 * Every state below is produced by the system's own code against the real stack.
 * Nothing is written to the database directly, no fixture is hand-edited, and no
 * API call is made from this file: each state is reached the way an operator
 * reaches it, by clicking, with the deployment configured so the system takes the
 * branch under test. Where a state needs a deployment knob rather than a click
 * (the gate refusal, below) the knob is a setting the repository already ships --
 * `OPSPILOT_REFUND_CEILING` and `MAX_STEPS` -- not a product change.
 *
 * | State | How the system gets there |
 * | --- | --- |
 * | A second click on a decided approval | Two tabs, both holding the same pending card; the first decides it |
 * | An approval that was already decided | The decided card, read under `?status=approved` |
 * | A run nothing claimed | The worker container stopped before the ticket was submitted |
 * | A run that failed | The same run, driven by a worker whose step budget the golden script exhausts |
 * | A tool call a gate refused | The same run, driven by a worker with a refund ceiling below the proposed refund |
 * | A run id this deployment does not have | A UUID that was never inserted |
 *
 * The failed run and the refused tool call share one run on purpose: they are
 * two views of a single incident, and asserting them together is what proves the
 * dashboard tells the same story on the banner, in the run's own fields and in
 * the tool-call list, rather than three separate things that happen to agree.
 *
 * ## What this file does NOT cover, and why that is a finding rather than an omission
 *
 * **The gate's name is not in the API's payload.** `_reject`
 * (`agents/runtime.py`) and the gate-4 denial both write two separate columns:
 * `rejection_reason` (which gate) and `error` (why within it). `ToolCallDetail`
 * (`api/schemas.py`) carries `error` and has no `rejection_reason` field at all,
 * and `GET /api/runs/{id}` never projects one -- so the trace screen can show *why*
 * a call was refused and cannot show *which gate* refused it. The assertion below
 * is for the reason the API actually sends. Widening the contract to carry the
 * gate name is a backend decision, not a dashboard one, so it is reported rather
 * than worked around here.
 *
 * **A duplicate `external_id` does not answer 409.** The Tickets form's own hint
 * says "Sending one twice returns 409 ticket_exists rather than creating a
 * duplicate", and `ticket-form.tsx` has a rendering branch for that code -- but
 * `routers/tickets.py::create_ticket` never raises it and never catches the
 * unique violation, so the real answer is a 500 `internal_error`, which the same
 * form renders as `500 internal_error: An internal error occurred.` That is a
 * generic message for a completely foreseeable operator mistake, so it is reported
 * and not pinned by a test here. See the task report.
 *
 * Skipped cleanly where Docker is unavailable, per ADR-0004's pattern (see
 * `stack.ts`), and the per-test skips name which prerequisite was missing rather
 * than reporting a bare "skipped".
 *
 * ## The ordering here is real, and it is not a correctness dependency
 *
 * The tests share one stack in declaration order (`fullyParallel: false`,
 * `workers: 1`), which is what lets the failed run and the refused tool call be
 * two views of one incident rather than three setups. The consequence is worth
 * stating because it is not obvious: **Playwright restarts the worker after a
 * failing test, and `beforeAll` then runs again against a brand-new stack** (the
 * `down -v` in `startStackOrSkip` wipes the volume). So if the 409 test fails,
 * the decided-approval test below is not reading a decided approval -- it is
 * reading a fresh database, and it fails for that reason rather than its own.
 * Tests 3-6 do not have this problem: each submits or creates what it reads.
 */

import { expect, test, type Page } from "@playwright/test";
import {
  recreateWorker,
  startStackOrSkip,
  stopWorker,
  workerReport,
  type StackHandle,
} from "./stack";

// The README's ticket, verbatim -- the same values the form's one-click fill puts
// in the fields, so submitting through that button is still a browser click and
// not a `fetch`.
const SUBJECT = "We were charged twice for invoice INV-2026-384";
const CUSTOMER_EMAIL = "billing@acme.example";

/**
 * A refund ceiling under the golden script's one refund ($129.00).
 *
 * `OPSPILOT_REFUND_CEILING` is ADR-0003's "removes capability, never grants it"
 * knob, and gate 4 evaluates it *before* gate 5 -- so with a ceiling this low the
 * run never reaches the approval gate, and the refund is recorded as `rejected`
 * with `rejection_reason='gate_4_policy_engine'` and
 * `error='refund_above_ceiling'`. Both strings are the system's own: the gate
 * name from `agents/runtime.py::GATE_POLICY` and the reason from
 * `domain/policies.py::REASON_REFUND_ABOVE_CEILING`.
 */
const REFUND_CEILING = "50";

/**
 * The step budget, sized to the golden script rather than to taste.
 *
 * `steps_taken` increments once per `run_step` (`agents/runtime.py:920`), and the
 * golden script proposes exactly four tools: `crm.get_customer`,
 * `billing.get_invoice`, `billing.list_transactions`, `billing.issue_refund`. A
 * budget of 4 therefore lets all four run -- the fourth being the refused one --
 * and the *fifth* PLAN stage trips `max_steps_exceeded` before it asks the fake
 * provider for another proposal.
 *
 * That last clause is the reason the number is not larger. The fake provider
 * replays a recorded scenario and raises `UnmatchedFixtureError` on a call it
 * cannot match, which `drain_once` deliberately does not catch (it is not an SDK
 * error), so a run that asked for a fifth proposal would take the worker process
 * down with it. Failing the run on the budget instead is the system's own
 * terminal answer for "this run cannot finish", and it is what leaves the failed
 * run this file reads the reason off.
 */
const MAX_STEPS = "4";

/** The run driven by the first ticket, parked on the approval gate. */
let goldenRunId: string | null = null;
/** The run submitted while no worker was polling, and then driven to a failure. */
let unclaimedRunId: string | null = null;

let stack: StackHandle | null = null;

test.beforeAll(async ({ browser }) => {
  stack = await startStackOrSkip("failure-states");
  if (stack.skip) {
    return;
  }
  // The knowledge base. The API does not ingest at start-up (`api/app.py` builds
  // the retrieval stack and runs no ingest), so a fresh Postgres holds zero
  // chunks; without this the run would retrieve nothing, abstain, and complete
  // without ever reaching the gates under test. Clicked, like the golden path
  // clicks it, because the control is what is being exercised.
  //
  // `beforeAll` is handed no `page` fixture (Playwright's page fixture is
  // per-test), so this opens one for the setup and closes it again. The `browser`
  // fixture IS worker-scoped and is the supported way to do that.
  const page = await browser.newPage();
  try {
    await page.goto("/knowledge");
    await page.getByRole("button", { name: /Reindex knowledge/i }).click();
    await expect(page.getByText(/\d+ seen/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/documents were found|The API answered/i)).not.toBeVisible();
    goldenRunId = await submitTicketThroughTheForm(page);
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
// The 409: a second click on an approval that has already been decided.
// ---------------------------------------------------------------------------

test("a second click on a decided approval is rendered as the guard working, not as an error", async ({
  page,
  context,
}) => {
  skipUnlessUp();
  const goldenRun = goldenRunId;
  if (goldenRun === null) {
    test.skip(true, "the golden ticket was submitted -- the setup before this file did not reach it");
    return;
  }
  skipUnlessReached("the golden ticket parked on the approval gate", goldenRunId);

  // Wait for the park first, the way `golden-path.spec.ts` does: the Approve
  // button only exists once a run has proposed a refund and stopped at gate 5,
  // so waiting on the button would be waiting on the worker with a 15-second
  // expectation timeout and no diagnosis when it did not come.
  await withWorkerDiagnosis("waiting for the run to park on the approval gate", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${goldenRun}`);
          return (await page.getByText(/Parked\. Nothing has been sent\./).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );

  // Both tabs load the inbox while the card is still PENDING. This is the
  // two-tabs case `decision-actions.tsx`'s docstring is written about: the second
  // tab holds a card the API no longer considers pending, and the only honest
  // thing it can do is click and be told what happened.
  await page.goto("/approvals");
  await expect(page.getByRole("button", { name: /^Approve$/ }).first()).toBeVisible({
    timeout: 30_000,
  });
  const secondTab = await context.newPage();
  await secondTab.goto("/approvals");
  await expect(secondTab.getByRole("button", { name: /^Approve$/ }).first()).toBeVisible({
    timeout: 30_000,
  });

  // Tab one decides. Approval does not execute the refund here; it releases the
  // run to the worker, which is why the inbox emptying is the UI-level proof that
  // the decision was recorded.
  await page.getByRole("button", { name: /^Approve$/ }).first().click();
  await expect(page.getByText(/Nothing is waiting for a decision\./)).toBeVisible({
    timeout: 30_000,
  });

  // Tab two, still holding the card it loaded, clicks again. The Route Handler
  // forwards the 409 with its own status, and this is the whole claim under
  // test: that what reaches the screen is the API's own answer, not a generic
  // failure invented by the dashboard.
  await secondTab.getByRole("button", { name: /^Approve$/ }).first().click();
  // Scoped to the panel by the one control only it has. A bare
  // `getByRole("alert")` is ambiguous on every App Router page: Next.js renders
  // its own `role="alert"` route announcer for accessibility, so there are two.
  const panel = secondTab.locator('div[role="alert"]').filter({ hasText: "Dismiss" });
  await expect(panel).toBeVisible({ timeout: 30_000 });

  // The consequence, not the category. "Conflict" is a word an operator has to
  // look up; "this click granted nothing" is the thing they need to know.
  await expect(panel).toContainText("Already decided — this click granted nothing.");
  // The API's own sentence, verbatim: it names WHO decided and WHEN
  // (`routers/approvals.py::_already_decided`), and those two facts exist nowhere
  // else in the payload -- `details` carries only the id and the status.
  await expect(panel).toContainText(/was already approved by operator at \d{4}-\d{2}-\d{2}T/);
  // And the reading, in the dashboard's own words: the guard held. A second
  // grant was not written.
  await expect(panel).toContainText("This is the guard working, not a failure.");

  // The contract's stable branch key and the status that makes it a 409 at all
  // (`api/routers/approvals/[decision]` forwards `error.status` verbatim).
  await expect(panel).toContainText("HTTP 409");
  await expect(panel).toContainText("code: approval_already_decided");
  // `details.approval_id` and `details.status`, read from the structured half of
  // the envelope rather than scraped out of the prose above.
  await expect(panel).toContainText(/approval_id: [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
  await expect(panel).toContainText("status: approved");

  // The wording §P2-C3 rules out. `describeDecisionFailure`'s `generic` branch is
  // what an unrecognised 409 would read as, and it is the sentence an operator
  // would take away believing the dashboard had lost the decision.
  await expect(panel).not.toContainText("The API refused this decision with 409");
  await expect(panel).not.toContainText("The decision did not go through");
  await expect(panel).not.toContainText("Something went wrong");
});

// ---------------------------------------------------------------------------
// A decided approval: a statement, not a control.
// ---------------------------------------------------------------------------

test("an approval that was already decided is a statement of what happened, not a button", async ({
  page,
}) => {
  skipUnlessUp();
  skipUnlessReached("an approval was decided", goldenRunId);

  await page.goto("/approvals?status=approved");

  // Scoped to the card: the status filter above the list carries a link labelled
  // "Approved", so a page-wide `getByText("Approved")` would match that too.
  const card = page.locator("li").filter({ hasText: "arguments_snapshot" }).first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  // Who decided and when -- the same two facts the 409's message carried, read
  // here from the card's own record rather than from an error.
  //
  // The assertion stops at "at <something>" on purpose. `formatTimestamp` is a
  // `toLocaleString(undefined, ...)` call, and this card is rendered by TWO
  // runtimes: `ApprovalCard` is a Server Component (Node's locale) while
  // `DecidedLine` lives inside the `"use client"` `DecisionActions` (Chromium's
  // locale). On this machine they disagree, and the card showed
  // `parked 2026年10月13日 13:08:36` next to `by operator at Oct 13, 2026,
  // 01:08:38 PM` for the same instant. Pinning either spelling would pin a
  // machine's locale rather than the dashboard's claim; the operator's reading
  // -- someone named, and a moment -- is what is asserted here.
  await expect(card).toContainText("Approved");
  await expect(card).toContainText(/by operator at \S/);

  // No control. A decided approval that still offered Approve would invite the
  // very second click whose 409 the test above reads -- the card is where that
  // invitation has to stop.
  await expect(card.getByRole("button", { name: /^Approve$/ })).toHaveCount(0);
  await expect(card.getByRole("button", { name: /^Reject$/ })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// A run nothing claimed.
// ---------------------------------------------------------------------------

test("a run no worker has claimed says the worker has not claimed it", async ({ page }) => {
  skipUnlessUp();

  // The operational failure this dashboard exists to make legible: the worker
  // process is gone, the API is up, and a ticket is still being submitted. It is
  // the state CI run 38202887580 hit, and it is invisible from the browser unless
  // the screen says it.
  stopWorker();
  const runId = await submitTicketThroughTheForm(page);
  unclaimedRunId = runId;

  await page.goto(`/runs/${runId}`);
  // Never claimed: `received`, with no worker having touched it. The badge is a
  // `<span>` read as one, because "Received" is also the word on the Runs filter
  // and matching that would assert the wrong element.
  await expect(page.locator("span").filter({ hasText: /^Received$/ }).first()).toBeVisible();
  // And the screen distinguishes "no steps yet" from "nothing to show". The hint
  // names the cause -- an unclaimed run and a run with no work are both
  // "Received with no steps", and only one of them is a problem.
  await expect(page.getByText("No steps recorded yet.")).toBeVisible();
  await expect(page.getByText(/the worker has not claimed it yet/)).toBeVisible();
  await expect(page.getByText("0 steps, as the server ordered them")).toBeVisible();
});

// ---------------------------------------------------------------------------
// A run that failed, and a tool call a gate refused -- one run, two screens.
// ---------------------------------------------------------------------------

test("a run that failed shows the reason the worker recorded, on the banner and in the run's own fields", async ({
  page,
}) => {
  skipUnlessUp();
  // Narrowed here rather than asserted with `!`: the tests share one stack in
  // declaration order, so a prerequisite a previous test failed to produce has to
  // skip -- naming which one -- rather than run against a null id.
  const runId = unclaimedRunId;
  if (runId === null) {
    test.skip(
      true,
      "a ticket was submitted while no worker was polling -- an earlier test in this file did not reach it",
    );
    return;
  }

  // Bring the worker back with the golden script's own step budget and a refund
  // ceiling under its one refund. The run submitted above is claimed on the next
  // poll and driven from the start: three reads execute, the refund is refused by
  // gate 4, and the fifth plan round exhausts `MAX_STEPS` and fails the run.
  await recreateWorker({ OPSPILOT_REFUND_CEILING: REFUND_CEILING, MAX_STEPS });

  // The Run Detail page is a pure Server Component with no client polling (no
  // `use client` and no interval under `src/app/runs/`), so the worker's progress
  // is visible on the next navigation -- which is the dashboard's own documented
  // affordance, the one the Tickets form names ("watch it on the run page, or
  // reload in a moment"). So this reloads, exactly as a human would.
  await withWorkerDiagnosis("waiting for the run to reach a terminal state", () =>
    expect
      .poll(
        async () => {
          await page.goto(`/runs/${runId}`);
          return (await page.getByText(/This run failed: /).count()) > 0;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .toBe(true),
  );

  // The banner leads with the reason, not with a category. "This run failed" on
  // its own is the generic form; the sentence after the colon is what tells an
  // operator whether to look at the budget, the provider or the tool servers.
  await expect(page.getByText("This run failed: max_steps_exceeded")).toBeVisible();
  // The same reason again, as a field of the run rather than as a banner -- so a
  // reader who came for the record gets it without reading the prose.
  await expect(
    page.locator("dt").filter({ hasText: "Failure reason" }).locator("xpath=.."),
  ).toContainText("max_steps_exceeded");
  // A failed run says what it did NOT do, in as many words: no reply was
  // composed, and the screen points back at the reason rather than leaving the
  // absence to be inferred.
  await expect(page.getByText("No reply — the run failed.")).toBeVisible();
  await expect(page.getByText(/does not compose an escalation reply/)).toBeVisible();
  await expect(page.getByText(/Parked\. Nothing has been sent\./)).not.toBeVisible();

  // The same fact on the Runs list, in the column an operator filters by. Two
  // surfaces reporting the reason differently -- or one of them not reporting it
  // -- is the failure mode this screen list exists to prevent.
  await page.goto("/runs?status=failed");
  const row = page.locator("tr").filter({ hasText: runId.slice(0, 8) }).first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText("Failed");
  await expect(row).toContainText("max_steps_exceeded");
});

test("a tool call a gate refused is on the trace screen, with the reason the gate recorded", async ({
  page,
}) => {
  skipUnlessUp();
  // Same run as the failure above, read on the screen that shows what the agent
  // actually did. The two tests share it deliberately: a banner and a tool-call
  // list that tell different stories about the same incident is exactly the
  // disagreement this dashboard exists to prevent.
  const runId = unclaimedRunId;
  if (runId === null) {
    test.skip(
      true,
      "a run carrying a refused tool call -- an earlier test in this file did not reach it",
    );
    return;
  }

  await page.goto(`/runs/${runId}`);
  const toolCalls = page.locator("section").filter({ hasText: "Tool calls" }).first();
  await expect(toolCalls).toBeVisible({ timeout: 30_000 });

  // The refused call, by name: the run DID propose a refund, and the operator can
  // see that it proposed one rather than inferring it from a missing approval.
  await expect(toolCalls.getByText("billing.issue_refund", { exact: true })).toBeVisible();
  // Its terminal state, as a word rather than a colour: `rejected`, not
  // `failed`. The two answer different questions and the run's own status
  // (`failed`, above) is a third thing again.
  await expect(toolCalls.getByText("Rejected", { exact: true })).toBeVisible();
  await expect(toolCalls.getByText("Failed", { exact: true })).toHaveCount(0);
  // And why the gate refused it, verbatim from `tool_calls.error`
  // (`domain/policies.py::REASON_REFUND_ABOVE_CEILING`, written by
  // `agents/runtime.py` at the gate-4 denial). This is the answer to "did it try
  // to move money" -- it did not, and the screen says which rule stopped it.
  await expect(toolCalls.getByText("error: refund_above_ceiling")).toBeVisible();
  // The arguments are on the card too, so the amount that tripped the ceiling can
  // be read next to the ceiling's verdict rather than looked up separately.
  await expect(toolCalls.getByText("129", { exact: false }).first()).toBeVisible();

  // NOT asserted, deliberately: which gate refused it. `_reject` and the gate-4
  // denial write `rejection_reason='gate_4_policy_engine'`, but `ToolCallDetail`
  // (`api/schemas.py`) has no such field and `GET /api/runs/{id}` does not
  // project one, so the trace screen has nothing to render it from. Asserting
  // the reason alone is the honest claim about what this screen says today; the
  // missing gate name is reported as a contract gap, not pinned here as if it
  // were rendered.
});

// ---------------------------------------------------------------------------
// A run id that is not here.
// ---------------------------------------------------------------------------

test("a run id this deployment does not have says so, rather than reporting a load failure", async ({
  page,
}) => {
  skipUnlessUp();

  // The pasted-link case the run page's own branch comment names: an id from a
  // colleague's chat, or from another deployment's database. "Could not load
  // this run" would be true and useless -- the API is perfectly reachable, and
  // the reader would go looking for an outage that is not there.
  const missing = "00000000-0000-4000-8000-000000000000";
  await page.goto(`/runs/${missing}`);
  await expect(page.getByText(`No run with id ${missing}.`)).toBeVisible();
  await expect(page.getByText(/The API answered 404\./)).toBeVisible();
  // Not the generic error panel, and not a blank page that could be read as an
  // idle deployment.
  await expect(page.getByText(/unexpected error/)).not.toBeVisible();
  await expect(page.getByRole("heading", { name: "Run detail" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to Runs" })).toBeVisible();
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
 * Skip when an earlier test in this file did not produce what this one reads.
 *
 * The tests share one stack in declaration order (`fullyParallel: false`,
 * `workers: 1`), so the gate refusal, the failed run and the unclaimed run are
 * three views of one incident rather than three independent setups. That ordering
 * is stated, not assumed -- and a test whose prerequisite is missing says so,
 * so a red first test does not turn into four indistinguishable red ones.
 */
function skipUnlessReached(what: string, value: string | null): void {
  if (value === null) {
    test.skip(true, `${what} -- an earlier test in this file did not reach it`);
  }
}

/**
 * Submit the README's ticket through the form and return the run id.
 *
 * The one-click fill plus Submit, asserted rather than bypassed: retyping the
 * values would make this a test of nothing but the API. The run link is read
 * from the success panel, which is the only `/runs/{uuid}` link on this screen --
 * the ticket list below links to `/runs?ticket=...`, which is a filter, not a
 * run -- so the locator is unambiguous even after several tickets have been
 * submitted in one session.
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

/**
 * Run an assertion, and if it fails, append the worker's own account of itself.
 *
 * The same idea as `golden-path.spec.ts`'s copy of this, for the same reason: a
 * timeout waiting for the WORKER says nothing about the worker unless the message
 * carries the worker's container state and its log. Read before `afterAll` runs
 * its `docker compose down -v`, which is the only moment the logs still exist.
 *
 * It changes no assertion and skips nothing. `body` is the unmodified wait.
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
