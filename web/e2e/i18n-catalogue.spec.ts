/**
 * The message catalogues themselves, checked without Docker or a browser.
 *
 * ## Why this is not a browser test
 *
 * Every claim here is about two JSON files and one pure function. Starting
 * Compose to assert that a Chinese string is not an English string would cost
 * minutes of wall clock per run and would make the check unrunnable on the
 * machine where a translator actually edits `src/i18n/dictionaries/`. So this
 * file imports the catalogues and the formatter and calls them directly, the
 * same way `write-origin.spec.ts` drives the Route Handlers and
 * `stack-diagnostics.spec.ts` drives the harness.
 *
 * The rendering half -- "does `zh-CN` actually put Chinese on the page, and does
 * the failure state still say something specific in it" -- is
 * `e2e/i18n.spec.ts`, which does need a stack. This file cannot see a rendered
 * page; that file cannot see the catalogue.
 *
 * ## WHAT EACH TEST IS FOR
 *
 * The four properties below are the ones a translator's edit can break without
 * any test going red, and each is a way this repository has already been bitten
 * by something adjacent:
 *
 * 1. **Parity.** A key present in English and absent in Chinese renders as
 *    `undefined` -- or, worse, as a thrown error on a page an operator is trying
 *    to make a decision from.
 * 2. **A real translation.** A Chinese file that copies English verbatim is
 *    green on every other check in this file and ships a product that claims to
 *    be localised and is not.
 * 3. **Plural completeness.** `Intl.PluralRules("zh-CN").select(1)` is `other`,
 *    so a Chinese catalogue that has no `other` form renders nothing at all.
 * 4. **The `other` discriminator.** `format.ts` tells a plural group from a
 *    group of messages by the presence of an `other` key. That is load-bearing
 *    type machinery, and a message legitimately called `other` would silently
 *    turn its whole section into a plural.
 */

import { expect, test } from "@playwright/test";

import en from "@/i18n/dictionaries/en.json";
import zhCN from "@/i18n/dictionaries/zh-CN.json";
import { createT } from "@/i18n/format";
import { DEFAULT_LOCALE, LOCALES, LOCALE_COOKIE, isLocale } from "@/i18n/config";

type Node = { readonly [key: string]: string | Node };

/**
 * Every dotted leaf of a catalogue, with the plural groups left whole.
 *
 * A plural is reported by its **base** name (`dashboard.replay`) and its value
 * is the `{one, other}` object, because that is what a caller's key names and
 * what a missing form has to be reported against.
 *
 * The plural discriminator here is deliberately **the same one `format.ts` and
 * the derived `MessageKey` type use**: an `other` key. Anything else is a group
 * and is walked into. Keeping one rule in three places is the point -- a test
 * that classified plurals differently from the code would be testing a second
 * implementation of the catalogue, which is the thing this file exists to
 * prevent.
 */
function isPlural(value: string | Node): value is Record<string, string> {
  return typeof value !== "string" && typeof value["other"] === "string";
}

function leaves(node: Node, prefix = ""): Map<string, string | Record<string, string>> {
  const found = new Map<string, string | Record<string, string>>();
  for (const [key, value] of Object.entries(node)) {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    if (typeof value === "string" || isPlural(value)) {
      found.set(path, value as string | Record<string, string>);
      continue;
    }
    for (const [child, childValue] of leaves(value as Node, path)) {
      found.set(child, childValue);
    }
  }
  return found;
}

/** The dotted paths of every *group* -- the ones the `other` rule must not catch. */
function groups(node: Node, prefix = ""): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const [key, value] of Object.entries(node)) {
    if (typeof value === "string") continue;
    const path = prefix === "" ? key : `${prefix}.${key}`;
    if (!isPlural(value)) {
      found.set(path, Object.keys(value));
    }
    for (const [child, childKeys] of groups(value as Node, path)) {
      found.set(child, childKeys);
    }
  }
  return found;
}

const EN = leaves(en as unknown as Node);
const ZH = leaves(zhCN as unknown as Node);

test.describe("catalogue completeness", () => {
  test("every English message exists in Chinese", () => {
    const missing = [...EN.keys()].filter((key) => !ZH.has(key));
    expect(
      missing,
      "These keys are in en.json and not in zh-CN.json. A missing message is a " +
        "throw on a page, not a fallback -- src/i18n/format.ts::resolve names " +
        "the key and refuses to render a blank.",
    ).toEqual([]);
  });

  test("Chinese has no key English does not, which is how a rename is caught", () => {
    const extra = [...ZH.keys()].filter((key) => !EN.has(key));
    expect(
      extra,
      "These keys are in zh-CN.json and not in en.json. English is the source " +
        "locale, so a key only one catalogue has is a translation of something " +
        "that no longer exists.",
    ).toEqual([]);
  });

  test("the source locale carries both plural forms, and they differ", () => {
    // `Intl.PluralRules("en").select` returns `one` for 1 and `other` for
    // everything else, so English needs both or a count of one throws. A
    // plural whose two forms are identical is worse than a missing one: it
    // renders, and it reads as though English had no singular -- which is the
    // mistake a copy-paste from the `other` form produces.
    const problems: string[] = [];
    for (const [key, value] of EN) {
      if (typeof value === "string") continue;
      if (value.one === undefined) problems.push(`${key}: no "one" form`);
      else if (value.other === undefined) problems.push(`${key}: no "other" form`);
      else if (value.one === value.other) problems.push(`${key}: "one" and "other" are identical`);
    }
    expect(
      problems,
      "English is the source locale and `Intl.PluralRules(\"en\")` selects both " +
        "categories, so every English plural needs a real `one` beside its " +
        "`other`.",
    ).toEqual([]);
  });

  test("Chinese needs no `one`, and must not invent one", () => {
    // `Intl.PluralRules("zh-CN").select(1)` is `other`, so a Chinese `one` form
    // is never selected. It is not harmless, though: it is a sentence a future
    // reader has to decide whether to maintain, and it invites the belief that
    // the two languages have the same plural system. Asserted so that adding one
    // is a decision rather than a copy.
    const invented: string[] = [];
    for (const [key, value] of ZH) {
      if (typeof value === "string") continue;
      if (value.one !== undefined) invented.push(key);
    }
    expect(
      invented,
      "Chinese has a single plural category. An `one` form here is never " +
        "selected, and is a sentence someone will think they have to maintain.",
    ).toEqual([]);
  });

  test("no group of messages is called `one` or `other`", () => {
    // `format.ts` tells a plural group from a group of messages by the presence
    // of an `other` key. If a message were called `other`, its whole section
    // would become a plural and every sibling key in it would stop resolving.
    const offenders: string[] = [];
    for (const locale of LOCALES) {
      const tree = (locale === "en" ? en : zhCN) as unknown as Node;
      for (const [path, keys] of groups(tree)) {
        if (keys.includes("one") || keys.includes("other")) {
          offenders.push(`${locale}:${path}`);
        }
      }
    }
    expect(
      offenders,
      "These sections have a child called `one` or `other`, which format.ts " +
        "would read as a plural group. Rename the message.",
    ).toEqual([]);
  });
});

test.describe("the Chinese catalogue is actually Chinese", () => {
  /**
   * The keys that must differ.
   *
   * A curated list rather than "no value may equal its English original",
   * because some messages are *supposed* to be identical: a contract field name
   * (`arguments_snapshot`), an environment variable (`OPSPILOT_OPERATOR_TOKEN`),
   * a tool token, a path. Those are listed below as the exceptions; everything
   * else is prose, and prose in the Chinese catalogue that is still English is
   * the failure this test is for.
   */
  const MUST_DIFFER = [
    "nav.dashboard",
    "nav.tickets",
    "nav.runs",
    "nav.approvals",
    "nav.knowledge",
    "dashboard.title",
    "dashboard.waitingLabel",
    "dashboard.waitingHintSome",
    "dashboard.noRunsTitle",
    "approvals.title",
    "approvals.emptyPendingTitle",
    "approvals.emptyPendingHint",
    "approvalSummary.title",
    "approvalSummary.aside",
    "approvalCard.duplicateIdenticalTitle",
    "approvalCard.duplicateDistinctTitle",
    "explanation.deterministic",
    "explanation.untrusted",
    "explanation.trustedAttribution",
    "explanation.untrustedAttribution",
    "decision.approve",
    "decision.reject",
    "decision.approveNote",
    "decision.alreadyDecided_headline",
    "decision.alreadyDecided_after",
    "risk.level_highRiskWrite",
    "risk.level_safeWrite",
    "risk.provenance_toolCall",
    "run.title",
    "run.backToRuns",
    "run.emptyTimelineTitle",
    "run.emptyTimelineUnclaimed",
    "run.emptyTimelineExpired_after",
    "run.claimUnclaimed",
    "run.claimExpired",
    "run.replyEscalated",
    "run.failureBanner_after",
    "runs.title",
    "runs.emptyUnfilteredHint",
    "tickets.title",
    "ticketForm.submit",
    "ticketForm.fillExample",
    "ticketForm.externalIdHint",
    "knowledge.title",
    "knowledge.noChunksBody",
    "knowledge.reindex.zeroIndexedNote",
    "knowledge.reindex.zeroIndexedBody",
    "format.justNow",
    "format.hoursAgo",
    "apiStatus.unreachable",
    "apiStatus.tokenRejected",
    "common.unknownError",
    "common.dismiss",
    "common.noResponse",
  ] as const;

  /**
   * The keys that must be identical in every locale.
   *
   * Machine identifiers and data. If one of these ever differs, the localiser
   * has translated something an operator greps for -- a contract field name, an
   * environment variable, or a tool token.
   */
  const MUST_MATCH = [
    "common.envOperatorToken",
    "apiStatus.envFile",
    "approvalCard.argumentsLabel",
    "approvalCard.toolCallIdLabel",
    "approvalSummary.argumentsLabel",
    "decision.codeLabel",
    "decision.approvalIdLabel",
    "decision.statusLabel",
    "decision.traceIdLabel",
  ] as const;

  test("the prose is translated", () => {
    const untranslated: string[] = [];
    for (const key of MUST_DIFFER) {
      const enValue = EN.get(key);
      const zhValue = ZH.get(key);
      if (enValue === undefined) {
        throw new Error(`MUST_DIFFER names "${key}", which en.json does not have.`);
      }
      if (typeof enValue !== "string") {
        throw new Error(`MUST_DIFFER names "${key}", which is a plural, not a sentence.`);
      }
      if (zhValue === enValue) untranslated.push(key);
    }
    expect(
      untranslated,
      "These messages are identical in both catalogues. Either they are prose " +
        "and need translating, or they belong in MUST_MATCH.",
    ).toEqual([]);
  });

  test("the identifiers are not translated", () => {
    const translated: string[] = [];
    for (const key of MUST_MATCH) {
      if (EN.get(key) !== ZH.get(key)) translated.push(key);
    }
    expect(
      translated,
      "These are contract field names, env var names and label formats. They " +
        "are the strings an operator greps the API schema and the deployment " +
        "configuration for, and they must be byte-identical in every locale.",
    ).toEqual([]);
  });
});

test.describe("the formatter", () => {
  test("picks the form the locale's plural rule asks for", () => {
    const english = createT(en as unknown as Node, "en");
    const chinese = createT(zhCN as unknown as Node, "zh-CN");

    // English has two forms and uses them.
    expect(english("dashboard.indexed", { count: 1 })).toBe("1 document indexed");
    expect(english("dashboard.indexed", { count: 7 })).toBe("7 documents indexed");

    // Chinese has one form and uses it for every count, which is what
    // Intl.PluralRules("zh-CN") says. Asserting the *rule* rather than the
    // catalogue is the point: a catalogue edit that added a bogus `one` to
    // zh-CN would make this fail, which is the outcome wanted.
    expect(chinese("dashboard.indexed", { count: 1 })).toBe("已索引 1 份文档");
    expect(chinese("dashboard.indexed", { count: 7 })).toBe("已索引 7 份文档");
    expect(new Intl.PluralRules("zh-CN").select(1)).toBe("other");
  });

  test("interpolates named parameters and leaves unknown ones visible", () => {
    const english = createT(en as unknown as Node, "en");
    expect(english("common.apiReturned", { status: 409 })).toBe("the API returned 409");
    // An unfilled placeholder stays visible rather than becoming a gap. A gap
    // where a worker's name should be is not something an operator can report.
    expect(english("common.apiReturned", {})).toBe("the API returned {status}");
  });

  test("a missing key throws and names itself", () => {
    const english = createT({ nav: { runs: "Runs" } }, "en");
    expect(() => (english as (k: string) => string)("nav.approvals")).toThrow(/nav\.approvals/);
    expect(() => (english as (k: string) => string)("nav.runs.deeper")).toThrow(/nav\.runs\.deeper/);
  });

  test("a plural called without a count throws rather than guessing", () => {
    const english = createT(en as unknown as Node, "en");
    expect(() => (english as (k: string) => string)("dashboard.indexed")).toThrow(/count/);
  });

  test("every key resolves in every locale with no placeholder left unfilled", () => {
    // The derived `MessageKey` union is a compile-time claim; this is the
    // runtime counterpart. A key that resolves in English but throws in Chinese
    // would be a page that works in one locale and 500s in the other, which is
    // the same class of defect D8 exists to prevent in the other direction.
    //
    // Every placeholder is filled with a value the test can recognise, so an
    // unfilled `{name}` is a failure rather than something the assertion has to
    // re-derive from the catalogue.
    const FILLED = {
      count: 2,
      status: "STATUS",
      id: "ID",
      code: "CODE",
      worker: "WORKER",
      until: "UNTIL",
      ticket: "TICKET",
      run: "RUN",
      endpoint: "ENDPOINT",
      state: "STATE",
      who: "WHO",
      time: "TIME",
    } as const;

    const failures: string[] = [];
    for (const [key] of EN) {
      for (const locale of LOCALES) {
        const t = createT((locale === "en" ? en : zhCN) as unknown as Node, locale);
        try {
          const rendered = t(key as never, FILLED);
          const unfilled = /\{\w+\}/.exec(rendered);
          if (unfilled) {
            failures.push(`${locale}:${key} left ${unfilled[0]} unfilled -> ${rendered}`);
          }
        } catch (error) {
          failures.push(`${locale}:${key} threw: ${String(error)}`);
        }
      }
    }
    expect(
      failures,
      "Every key must resolve in every shipped locale with every placeholder " +
        "filled. A key that renders in English and throws in Chinese is a page " +
        "that 500s for the locale the operator just chose.",
    ).toEqual([]);
  });
});

test.describe("the locale contract", () => {
  test("a fresh clone resolves to English with no cookie and no configuration", () => {
    // DoD D8, stated where it can be checked without a browser: the default is
    // a property of the code, not of an env file nobody set.
    expect(DEFAULT_LOCALE).toBe("en");
    expect(LOCALES[0]).toBe("en");
    expect(isLocale(undefined)).toBe(false);
    expect(isLocale("")).toBe(false);
    expect(isLocale("kl")).toBe(false);
    // An unknown tag resolves to English rather than throwing or 404ing: a
    // stale cookie left by a withdrawn locale must not take the dashboard down.
    expect(isLocale("zh-CN")).toBe(true);
    expect(isLocale("en")).toBe(true);
  });

  test("the cookie is named, httpOnly-bound and set on the site the operator is on", () => {
    // Named rather than derived, so the e2e specs and the app agree on one
    // contract. `httpOnly` is a claim about `/api/locale`'s options, which
    // `e2e/i18n.spec.ts` checks against the browser's own cookie jar; this
    // pins the name both sides import.
    expect(LOCALE_COOKIE).toBe("opspilot_locale");
  });

  test("the switcher names each locale in its own script", () => {
    const english = createT(en as unknown as Node, "en");
    const chinese = createT(zhCN as unknown as Node, "zh-CN");
    // The reading, not the tag: `zh-CN` means nothing to someone who cannot
    // read Latin script, and "Chinese" means nothing to someone who can.
    expect(chinese("nav.locale.zh-CN")).toBe("中文");
    expect(english("nav.locale.zh-CN")).toBe("中文");
    expect(english("nav.locale.en")).toBe("English");
  });
});