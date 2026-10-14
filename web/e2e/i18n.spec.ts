/**
 * A non-English locale renders translated copy -- and the copy is still
 * *specific*.
 *
 * ## WHY THIS FILE EXISTS AT ALL
 *
 * `docs/phase2-plan.md` §P2-F names the cost nobody sees coming: the e2e suite
 * asserts English prose in 50 places, several of them written specifically to
 * prove that a failure state says something **true and specific** rather than
 * something generic. `getByText(/the worker has not claimed it yet/)` is not
 * incidental -- it is the assertion that the screen distinguishes "no worker has
 * claimed this" from "a worker holds it and has not written a step yet", and
 * `docs/limitations.md` §2 records the five minutes that distinction was missing.
 *
 * Adding a locale without a test here leaves that guarantee English-only. The
 * English suite would keep passing while a Chinese operator was told something
 * vague, and nothing would notice.
 *
 * So this file makes the guarantee **per locale**: the same claim-lease contrast,
 * on the same page, read in Chinese, with the same shape of assertion.
 *
 * ## WHAT IS PINNED AS A LITERAL, AND WHY
 *
 * The Chinese sentences below are **written out**, not read out of
 * `zh-CN.json`. That is deliberate and it is the whole point of the file: an
 * assertion derived from the catalogue only proves that the catalogue reached
 * the screen, so replacing the Chinese copy with "运行出现问题" would leave the
 * suite green. A literal has to be edited in the same commit as the copy, which
 * puts the degradation in the diff where a reviewer sees it.
 *
 * ## WHAT IS *NOT* PINNED AS A LITERAL
 *
 * The API-side identifiers. `received`, `max_steps_exceeded`,
 * `refund_above_ceiling`, `arguments_snapshot`, `OPSPILOT_OPERATOR_TOKEN` and
 * every `?status=` value are asserted **identical in both locales**, which is
 * the assertion §P2-F actually asks for: switching locale must not change what
 * the API speaks.
 *
 * ## WHAT THE FIRST TWO TESTS GUARD
 *
 * That English is the default with no configuration (DoD D8), and that the
 * switcher is a real control an operator can find and undo -- not a URL
 * parameter only this file knows about.
 */

import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import {
  claimOldestRunThroughTheRepository,
  startStackOrSkip,
  stopWorker,
  type StackHandle,
} from "./stack";

import en from "@/i18n/dictionaries/en.json";
import { LOCALE_COOKIE } from "@/i18n/config";
import { DASHBOARD_URL } from "./dashboard-port";

// The README's ticket, verbatim -- the same values the form's one-click fill puts
// in the fields, so submitting through that button is still a browser click and
// not a `fetch`.
const SUBJECT = "We were charged twice for invoice INV-2026-384";
const CUSTOMER_EMAIL = "billing@acme.example";

/**
 * The holder, which the page has to name.
 *
 * The same stand-in `claim-lease.spec.ts` uses, for the same reason: a test that
 * used the deployment's default worker could not tell a page rendering the
 * holder apart from one rendering a hard-coded string.
 */
const STAND_IN_WORKER = "i18n-stand-in-worker";

/** A lease long enough that the run cannot lapse while the page is read. */
const LIVE_LEASE_SECONDS = 300;

/**
 * The language control's visible names, as they appear in both locales.
 *
 * Each locale names itself in its own script, so the *target* of the click does
 * not change with the current language -- an operator who has accidentally
 * switched to a language they cannot read can still find the way back.
 */
const SWITCH_TO_CHINESE = "中文";
const SWITCH_TO_ENGLISH = "English";

let stack: StackHandle | null = null;
/** The run submitted while no worker was polling, then claimed by this file. */
let runId: string | null = null;

test.beforeAll(async ({ browser }) => {
  stack = await startStackOrSkip("i18n");
  if (stack.skip) {
    return;
  }
  // **No reindex**, for the same reason `claim-lease.spec.ts` does not: nothing
  // here is ever driven. The worker is stopped for the whole run and the claim
  // is taken through `claimOldestRunThroughTheRepository`, which claims without
  // driving. Ingesting the corpus would add a minute of wall clock to prove
  // something about a path this file does not take.
  //
  // The worker is stopped *before* the ticket exists, so the run cannot be
  // claimed in the gap between submitting it and asserting on it.
  const page = await browser.newPage();
  try {
    stopWorker();
    runId = await submitTicketThroughTheForm(page);
  } finally {
    await page.close();
  }
});

test.afterAll(async () => {
  if (stack !== null && !stack.skip) {
    await stack.teardown();
  }
});

/** Skip the calling test (with a reason) when the stack is not up. */
function skipUnlessUp(): void {
  if (stack === null) {
    test.skip(true, "the stack did not start");
  } else if (stack.skip) {
    test.skip(true, stack.reason);
  }
}

/**
 * Choose a language the way an operator does.
 *
 * Through the visible control rather than by setting the cookie directly,
 * because the control is the thing being tested: a locale switch that only the
 * test suite can perform is not a locale switch. The cookie is still set here
 * as a *precondition*, so that a test which only cares about rendering does not
 * have to navigate twice.
 */
async function chooseLocale(page: Page, label: string, returnTo = "/"): Promise<void> {
  await page.goto(returnTo);
  await languageSwitcher(page).getByRole("link", { name: label }).click();
  await page.waitForLoadState("domcontentloaded");
}

/**
 * The language control, by its own accessible name.
 *
 * The main nav and the switcher are **sibling** landmarks rather than nested
 * ones (`nav.tsx` says why), so the switcher is reachable by its own
 * `aria-label` -- and the label is itself localised, so it is matched in either
 * language.
 */
function languageSwitcher(page: Page) {
  return page.getByRole("navigation", { name: /^(Language|语言)$/ });
}

/** Put the browser in a locale without going through the control. */
async function setLocaleCookie(context: BrowserContext, locale: string): Promise<void> {
  await context.addCookies([
    { name: LOCALE_COOKIE, value: locale, url: DASHBOARD_URL, httpOnly: true },
  ]);
}

/**
 * Submit the README's ticket through the form and return the run id.
 *
 * Duplicated from `claim-lease.spec.ts` rather than hoisted into `stack.ts`,
 * for that file's stated reason: coupling two specs' assertions to one another's
 * edits is worse than the duplication. The one-click fill plus Submit is
 * asserted rather than bypassed, so this stays a test of the dashboard.
 */
async function submitTicketThroughTheForm(page: Page): Promise<string> {
  await page.goto("/tickets");
  await page.getByRole("button", { name: /填入 README 中的工单|Fill with the README/i }).click();
  await expect(page.getByLabel(/主题|Subject/i)).toHaveValue(SUBJECT);
  await expect(page.getByLabel(/客户邮箱|Customer email/i)).toHaveValue(CUSTOMER_EMAIL);
  await page.getByRole("button", { name: /^(提交工单|Submit ticket)$/i }).click();

  const runLink = page.locator('a[href^="/runs/"]').first();
  await expect(runLink).toBeVisible({ timeout: 30_000 });
  const href = await runLink.getAttribute("href");
  expect(href).toMatch(/^\/runs\/[0-9a-f-]{36}$/);
  return href!.replace("/runs/", "");
}

// ---------------------------------------------------------------------------
// The default: English, with no configuration. (DoD D8)
// ---------------------------------------------------------------------------

test("a fresh browser renders English, with no cookie and no configuration", async ({
  page,
  context,
}) => {
  skipUnlessUp();

  // Nothing set it, so nothing reads it. Asserted from the jar rather than
  // assumed, because "the test forgot to clean up" and "the app defaulted
  // correctly" look identical on a green run otherwise.
  expect(
    (await context.cookies()).filter((cookie) => cookie.name === LOCALE_COOKIE),
    "a fresh Playwright context must carry no locale cookie; one left by an " +
      "earlier test in this run would make this pass for the wrong reason",
  ).toEqual([]);

  await page.goto("/");

  // The `lang` attribute is how a screen reader picks a voice and how the
  // browser picks a font fallback. It is the one attribute that would be wrong
  // rather than merely incomplete if it were hard-coded.
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  // The nav, which is on every page. Pinned as literals from en.json for the
  // reason this file's docstring gives: a test derived from the catalogue
  // proves only that the catalogue reached the screen.
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText([
    "Dashboard",
    "Tickets",
    "Runs",
    "Approvals",
    "Knowledge",
  ]);
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
  await expect(
    page.getByText("What needs a human right now, what is broken, and what has been happening."),
  ).toBeVisible();
  // And the source catalogue is the English one, unchanged by having a second.
  expect(en.nav.runs).toBe("Runs");
});

// ---------------------------------------------------------------------------
// The switcher, as an operator uses it.
// ---------------------------------------------------------------------------

test("the language control renders Chinese copy and can undo itself", async ({ page }) => {
  skipUnlessUp();

  await chooseLocale(page, SWITCH_TO_CHINESE);

  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  // The nav, translated. Asserted as literals from zh-CN.json written out here,
  // not read from the file -- see the module docstring.
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link")).toHaveText([
    "总览",
    "工单",
    "运行",
    "审批",
    "知识库",
  ]);
  await expect(page.getByRole("heading", { name: "总览", exact: true })).toBeVisible();
  await expect(
    page.getByText("当前需要人工处理的事项、已经出现的问题，以及正在发生的活动。"),
  ).toBeVisible();
  // The English is gone. A page that renders both is a page whose *reading*
  // language did not change, which is the failure a half-localised catalogue
  // produces.
  await expect(page.getByText("What needs a human right now")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Dashboard" })).toHaveCount(0);

  // And the control names itself in its own script in both directions, so an
  // operator who has switched into a language they cannot read can still get
  // out. That is why `nav.locale.*` is in the catalogue at all rather than in
  // `config.ts`.
  const switcher = languageSwitcher(page);
  await expect(switcher.getByRole("link", { name: SWITCH_TO_CHINESE })).toBeVisible();
  await expect(switcher.getByRole("link", { name: SWITCH_TO_ENGLISH })).toBeVisible();

  // Undo it, through the control, and land back where the operator started.
  await switcher.getByRole("link", { name: SWITCH_TO_ENGLISH }).click();
  await page.waitForLoadState("domcontentloaded");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
});

test("the control returns the operator to the screen they were reading", async ({ page }) => {
  skipUnlessUp();

  // `returnTo` is the difference between a switch and a reset. Landing on the
  // dashboard after choosing a language on a parked run would throw away the
  // reason the operator opened it.
  await setLocaleCookie(page.context(), "zh-CN");
  await page.goto("/knowledge");
  await languageSwitcher(page).getByRole("link", { name: SWITCH_TO_ENGLISH }).click();
  await page.waitForLoadState("domcontentloaded");

  await expect(page).toHaveURL(/\/knowledge$/);
  await expect(page.getByRole("heading", { name: "Knowledge", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "知识库", exact: true })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// The claim, in Chinese: the specific sentence, not a generic one.
// ---------------------------------------------------------------------------

test("the unclaimed-run hint is specific in Chinese, and names the same cause", async ({
  page,
}) => {
  skipUnlessUp();
  if (runId === null) {
    test.skip(true, "a ticket was submitted while no worker was polling -- the setup did not reach it");
    return;
  }

  await setLocaleCookie(page.context(), "zh-CN");
  await page.goto(`/runs/${runId}`);

  // The title, in Chinese.
  await expect(page.getByText("尚未记录任何步骤。")).toBeVisible();
  await expect(page.getByText("No steps recorded yet.")).toHaveCount(0);

  // **The specificity assertion.** The English sentence
  // ("...the worker has not claimed it yet") is asserted by
  // `failure-states.spec.ts` and `claim-lease.spec.ts` because it names the
  // *cause*. The Chinese one has to name the same cause, or a Chinese operator
  // is told only that there are no steps -- which is true of a run that is
  // progressing fine and is exactly the claim this project refuses to make.
  //
  // So: it says the worker has not claimed it, and it names the status the run
  // is still in. A generic rewrite cannot carry both.
  await expect(
    page.getByText(/工作进程尚未领取它/),
  ).toBeVisible();
  await expect(page.getByText(/仍处于 `received` 状态/)).toBeVisible();

  // And the panel's own count line, which is the second half of "no steps yet
  // means nothing at all" rather than "there is nothing to show".
  await expect(page.getByText("0 个步骤，按服务端给定的顺序")).toBeVisible();

  // The run's own field agrees, in Chinese.
  const claim = page.locator("dt").filter({ hasText: "领取" }).locator("xpath=..");
  await expect(claim).toContainText("未被领取");
  // Nothing on this page claims a holder. The defect was a holder the page
  // denied, so the absence is asserted rather than assumed.
  await expect(page.getByText(/由 .* 持有至/)).toHaveCount(0);
});

test("a claimed run stops saying so in Chinese, and names the holder", async ({ page }) => {
  skipUnlessUp();
  if (runId === null) {
    test.skip(true, "a ticket was submitted while no worker was polling -- the setup did not reach it");
    return;
  }

  // The same claim, the same run, through the production method a worker calls.
  // The worker is still stopped, so nothing else can take the row.
  const claimed = claimOldestRunThroughTheRepository({
    workerId: STAND_IN_WORKER,
    leaseSeconds: LIVE_LEASE_SECONDS,
  });
  expect(claimed, "no run was claimable, so nothing claimed this one").not.toBeNull();
  expect(claimed!.id).toBe(runId);

  await setLocaleCookie(page.context(), "zh-CN");
  await page.goto(`/runs/${runId}`);

  // The unclaimed sentence is *gone*. Keeping it alongside the new one would
  // leave the page still telling an operator that nobody had claimed a run a
  // worker is holding -- the defect `docs/limitations.md` §2 measured.
  await expect(page.getByText(/工作进程尚未领取它/)).toHaveCount(0);
  await expect(page.getByText(/the worker has not claimed it yet/)).toHaveCount(0);

  // **The specificity assertion, again.** The replaced sentence has to name the
  // holder and the bound, in Chinese, by the *worker's own id* -- which a
  // generic sentence cannot do, and which is half of what makes the message
  // actionable: the operator learns which container to go and look at.
  await expect(
    page.getByText(new RegExp(`一个工作进程（${STAND_IN_WORKER}）已领取该运行`)),
  ).toBeVisible();
  await expect(page.getByText(/该领取将保持到/)).toBeVisible();

  // The run's own field names the same holder, in Chinese.
  const claim = page.locator("dt").filter({ hasText: "领取" }).locator("xpath=..");
  await expect(claim).toContainText(`由 ${STAND_IN_WORKER} 持有至`);
});

// ---------------------------------------------------------------------------
// What must NOT change with the locale.
// ---------------------------------------------------------------------------

test("switching locale changes nothing the API or an operator's link speaks", async ({
  page,
}) => {
  skipUnlessUp();

  // **The filter links.** Every one of these is a query the API has to accept
  // verbatim. A localiser who translated a filter's *value* rather than its
  // label would send `?status=等待审批` and get a `400 validation_error` from a
  // status the API does not have. Asserted as a set of hrefs rather than as
  // screen text, because the href is the thing that has to survive.
  const readFilterHrefs = async (locale: "en" | "zh-CN"): Promise<string[]> => {
    await setLocaleCookie(page.context(), locale);
    await page.goto("/runs");
    const links = page.locator('a[href^="/runs?status="]');
    const count = await links.count();
    const hrefs: string[] = [];
    for (let index = 0; index < count; index += 1) {
      hrefs.push((await links.nth(index).getAttribute("href")) ?? "");
    }
    hrefs.sort();
    return hrefs;
  };

  const englishHrefs = await readFilterHrefs("en");
  const chineseHrefs = await readFilterHrefs("zh-CN");

  expect(englishHrefs.length).toBeGreaterThan(0);
  expect(chineseHrefs, "the same filters must produce the same query in both locales").toEqual(
    englishHrefs,
  );
  // Named rather than merely equal, so a change to the *set* is caught too.
  expect(englishHrefs).toContain("/runs?status=waiting_approval");
  expect(englishHrefs).toContain("/runs?status=failed");

  // **The API requests the server makes.** Every read the dashboard performs is
  // a server-side fetch to a path the contract fixes. If a locale ever leaked
  // into a URL, this is where it would show up -- and it is asserted as a set of
  // *paths* because a locale would arrive as a query parameter or a header, and
  // neither appears.
  const apiPathsFor = async (locale: "en" | "zh-CN"): Promise<string[]> => {
    await setLocaleCookie(page.context(), locale);
    const seen: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.port === "8000") seen.push(`${url.pathname}${url.search}`);
    });
    await page.goto("/approvals");
    await expect(page.getByRole("heading", { name: /^(Approvals|审批)$/ })).toBeVisible();
    page.off("request", () => {});
    return [...new Set(seen)].sort();
  };

  expect(await apiPathsFor("zh-CN")).toEqual(await apiPathsFor("en"));
});

test("contract field names and operator data read the same in every locale", async ({
  page,
}) => {
  skipUnlessUp();
  if (runId === null) {
    test.skip(true, "a ticket was submitted while no worker was polling -- the setup did not reach it");
    return;
  }

  // **An operator's own words are not copy.** The ticket submitted in `beforeAll`
  // is data this system received and will hand back to the customer's support
  // team; translating it would be a data-corruption bug wearing a localisation
  // feature's clothes. Asserted in Chinese against the same literal the English
  // specs assert in `toHaveValue`, so the two locales can be read against each
  // other.
  await setLocaleCookie(page.context(), "zh-CN");
  await page.goto("/tickets");
  await expect(page.getByText(SUBJECT)).toBeVisible();
  await expect(page.getByText("Please investigate and fix it.")).toBeVisible();
  await expect(page.getByText(CUSTOMER_EMAIL)).toBeVisible();

  await setLocaleCookie(page.context(), "en");
  await page.goto(`/runs/${runId}`);
  // The screen's own chrome is translated back, and the run id -- which is the
  // resource the URL names -- is not.
  await expect(
    page.getByRole("heading", { name: new RegExp(`^Run ${runId.slice(0, 8)}$`) }),
  ).toBeVisible();
  await expect(page.getByText("尚未记录任何步骤。")).toHaveCount(0);

  await setLocaleCookie(page.context(), "zh-CN");
  await page.goto(`/runs/${runId}`);
  // The run status label is localised; the raw value it stands for is not, and
  // the URL that selects it is not either. `received` is the status the hint
  // tells the operator to compare against, so it stays inside the sentence.
  await expect(page.getByText(/已接收/, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/^Received$/)).toHaveCount(0);

  // And the run id, which is the resource the URL names, renders as itself.
  await expect(page.getByRole("heading", { name: new RegExp(`^运行 ${runId.slice(0, 8)}$`) })).toBeVisible();
});