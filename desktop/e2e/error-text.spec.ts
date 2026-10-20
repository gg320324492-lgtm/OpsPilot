/**
 * The wording the operator reads, and the wording the verifier is told to look for.
 *
 * ## Why this file exists
 *
 * `docs/installer-verification.md` tells a person who did not build this
 * application to read the window and compare what it says against a list of
 * expected messages. That list is a copy of strings this package produces, and
 * a copy is a second source of truth: the two drifted, and the drift was found
 * by an audit rather than by a test.
 *
 * The drifted pair, for the record:
 *
 *     the app says   "Docker is not installed"
 *     the doc said   "Docker Desktop is not installed"
 *
 *     the app says   "Docker is installed but not running"
 *     the doc said   "Docker is installed but not responding"
 *
 * Neither is a *wrong* statement about the world — the app is the one that
 * shows the message, and it was telling the truth. But a person verifying
 * against the wrong literal would have reported a failure that did not happen,
 * and a person verifying against the right one would have been told the
 * application was broken when it was working. A verification procedure whose
 * expected strings are wrong is worse than none: it manufactures a finding.
 *
 * So the strings are asserted here, in the project that needs no Electron, no
 * Docker and no display, against the same functions the shell calls. Changing
 * a message now breaks a test instead of silently invalidating a document.
 *
 * ## WHY THE DOCUMENT IS READ AS TEXT, AND WHY ONLY ITS QUOTED PARTS
 *
 * The document is prose, and prose is not a structured artefact. What a reader
 * is misled by is a *quoted* string that the application never displays — a
 * sentence about Docker Desktop the product being absent is not a quotation
 * of what the window says, and the section heading "If Docker Desktop is not
 * installed" is exactly such a sentence.
 *
 * So the check is not "the document must not contain the word". It is "the
 * document must not *quote* a string the application never shows". Every
 * quoted span is extracted — double-quoted, back-ticked, quoted as a Markdown
 * block quote, or inside a fenced code block — and those are what is compared
 * against the real titles. A document that merely omits a message is a weaker
 * document, not a wrong one, and this test does not claim to be a linting tool
 * for prose.
 *
 * Both the English document and its Chinese translation are covered. The
 * translation quotes the same literals, and it is the one a Chinese-reading
 * operator is handed, so a drift in either is the same defect.
 *
 * ## WHAT THE CORPUS OF MESSAGES IS, AND WHY IT IS NOT A LIST TYPED HERE
 *
 * A check of the shape "the document must quote exactly these strings" is a
 * check of a *list*, and a list is a second source of truth — the disease this
 * file exists to cure, written down instead of imported. So every message in
 * the corpus is produced by the code that shows it:
 *
 *   * the shell's titles are imported from `src/lifecycle/errors.ts`, the same
 *     functions the shell calls, so a reworded title breaks this test;
 *   * the risk explanation the approver reads is *read out of the policy
 *     engine's own source* (`src/opspilot/domain/policies.py`), together with
 *     the arguments of the golden path's refund, which are read out of the
 *     committed fixture (`evals/datasets/fixtures/duplicate_charge.json`).
 *
 * The second one is the point of the exercise. The explanation was reworded
 * when the currency stopped being rendered into it — the approval text could
 * name a currency the money would not move in — and the documents went on
 * quoting the old sentence, for a run whose whole point is that an operator
 * reads that sentence before deciding whether money moves. A corpus that only
 * knew about the shell's three titles could not see it: `risk_explanation` was
 * not in the set of messages it compared against, so a stale one was not a
 * stale one, it was a span nobody had an opinion about.
 *
 * ## WHY THE CHECK IS A POSITIVE ONE
 *
 * The interesting direction is "the document must quote the message the
 * application produces", not "the document must not quote a message the
 * application does not produce". The second is not expressible: a document
 * quotes hundreds of spans that are not messages at all — file paths, curl
 * commands, JSON fragments, field names, the names of Windows' own dialogs —
 * and a rule that called every one of them a mistake would be a linter for
 * prose, asserting the documents' writing style rather than the application's
 * behaviour. The narrow version of that rule this file already had — a list of
 * strings the application has *never* displayed — is kept, because those two
 * are the drifted pairs an audit found, but it cannot be the mechanism: a list
 * only catches what somebody remembered to add to it.
 *
 * The positive direction needs no list and cannot miss: the corpus is the
 * application's own output, so a message that changes stops matching the
 * document and the test goes red. That is the property this file wanted and
 * did not have.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  composePluginMissing,
  dockerNotInstalled,
  dockerNotRunning,
} from "../src/lifecycle/errors";

const ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");

/**
 * The policy engine's source, read as text.
 *
 * The explanation the approver reads is written in Python, in a package that
 * has no Node in it, so it cannot be imported here the way the shell's titles
 * are. It can be *read*: the sentences `explain_risk` returns are string
 * literals in that file, and reading them is closer to importing them than
 * typing them out is. `desktop/e2e/menu.spec.ts` reads `web/src/i18n/config.ts`
 * the same way, and for the same reason.
 */
const POLICY_SOURCE = path.join(ROOT, "src", "opspilot", "domain", "policies.py");

/**
 * The committed golden path: the fixture the deterministic provider replays.
 *
 * It is where the refund's transaction id and amount come from, so the check
 * is about *this* run's explanation rather than about a sentence with the
 * right shape.
 */
const GOLDEN_PATH = path.join(ROOT, "evals", "datasets", "fixtures", "duplicate_charge.json");

/** Every title the shell can put in the window, with the function that makes it. */
const TITLES = [
  dockerNotRunning("stderr").title,
  dockerNotInstalled("docker", undefined).title,
  composePluginMissing("stderr").title,
] as const;

/**
 * The two states the verification procedure walks the operator through.
 *
 * `composePluginMissing` is a third real state — Docker is running but the
 * plugin is absent — and the procedure does not ask about it, so this test
 * does not require the document to mention it. It is in `TITLES` so that a
 * document that *does* mention it is checked against the real string.
 */
const WALKED_STATES = [
  dockerNotRunning("stderr").title,
  dockerNotInstalled("docker", undefined).title,
] as const;

/**
 * The strings a previous version of the documents quoted, and the application
 * has never displayed.
 *
 * Each reads like a plausible message and each is exactly what a copy of
 * slightly-different wording looks like. A document quoting one of these is
 * describing an application that does not exist.
 */
const SUPERSEDED = [
  "Docker Desktop is not installed",
  "Docker is installed but not responding",
] as const;

/**
 * Every quoted span in a document.
 *
 * Four shapes, because the documents use four: `"…"`, `` `…` ``, a Markdown
 * block quote, and a fenced code block. The block quote matters here — the
 * walk-through shows the approval card's two texts as one, and a guard
 * that did not read block quotes was blind to half of what the operator is
 * told to look for.
 *
 * A quoted region is also recorded as a whole, with its whitespace collapsed,
 * because a message the document wraps across lines is one span and not
 * several: the approval card's explanation is three lines in each document,
 * and neither is a different sentence from the one the code writes.
 */
function quotedSpans(document: string): string[] {
  const spans: string[] = [];

  for (const match of document.matchAll(/`([^`\n]+)`/g)) {
    spans.push(match[1] ?? "");
  }
  for (const match of document.matchAll(/```[^\n]*\n([\s\S]*?)```/g)) {
    const block = match[1] ?? "";
    for (const line of block.split("\n")) {
      spans.push(line);
    }
    spans.push(block);
  }
  // Block quotes. The fenced blocks are removed first, because a code sample
  // may contain a line that begins with `>` and that line is not a quotation
  // of anything the application says.
  const withoutCode = document.replace(/```[\s\S]*?```/g, "");
  for (const match of withoutCode.matchAll(/^((?:>[^\n]*\n?)+)/gm)) {
    const quoted = (match[1] ?? "")
      .split("\n")
      .map((line) => line.replace(/^>\s?/, ""))
      .join("\n");
    spans.push(quoted);
    for (const line of quoted.split("\n")) {
      spans.push(line);
    }
  }
  // Double-quoted spans, including the ones the Markdown wraps across lines
  // (`"Docker is not\ninstalled"`). The whitespace is collapsed so that a
  // wrapped quotation and an unwrapped one compare equal.
  for (const match of document.matchAll(/"([^"]+)"/g)) {
    spans.push((match[1] ?? "").replace(/\s+/g, " "));
  }

  return spans.map((span) =>
    span
      .replace(/\s+/g, " ")
      .trim()
      // A trailing ellipsis means "the message starts with this", which is how
      // the Chinese document quotes it (`Docker is not installed…`). It is not
      // part of the message, so it is not part of the comparison.
      //
      // A sentence-final *period* is not an ellipsis and is left alone. It was
      // being stripped as well — the character class had a full stop in it
      // next to the ellipsis — and that is invisible until a message ends in
      // one, at which point every comparison against it fails for a reason
      // that has nothing to do with the message: the risk explanation ends in
      // a full stop, so the span it produced never matched the sentence the
      // policy engine writes.
      .replace(/(\.{3,}|…)+$/, "")
      .trim(),
  );
}

/**
 * The sentences `explain_risk` can produce, read out of the module that
 * produces them.
 *
 * The function's own body is read: the string literals in it are the sentences
 * it returns. Python concatenates adjacent string literals, and the sentence
 * the approver reads is written across three of them, so adjacent literals
 * are joined; a literal separated from the last one by anything but
 * whitespace is a sentence of its own.
 *
 * The function's *docstring* is skipped. It is prose about the function, and
 * it quotes an example of the output — which is a copy, and the whole reason
 * this file does not type the sentence out.
 */
function explanationTemplates(source: string): string[] {
  const start = source.indexOf("def explain_risk(");
  if (start === -1) {
    throw new Error("policies.py has no explain_risk to read the messages from");
  }
  const next = source.indexOf("\ndef ", start + 1);
  const body = source.slice(start, next === -1 ? source.length : next);

  const open = body.indexOf('"""');
  const close = body.indexOf('"""', open + 3);
  if (open === -1 || close === -1) {
    throw new Error("explain_risk has no docstring, so its body cannot be located");
  }
  const code = body.slice(close + 3);

  const literal = /f?"((?:[^"\\]|\\.)*)"/g;
  const found: { text: string; from: number; to: number }[] = [];
  let match: RegExpExecArray | null;
  while ((match = literal.exec(code)) !== null) {
    found.push({
      text: match[1] ?? "",
      from: match.index,
      to: match.index + match[0].length,
    });
  }

  const templates: string[] = [];
  let current = "";
  let previousTo = -1;
  for (const entry of found) {
    const between = previousTo === -1 ? "" : code.slice(previousTo, entry.from);
    if (previousTo !== -1 && /^\s*$/.test(between)) {
      current += entry.text;
    } else {
      if (current !== "") templates.push(current);
      current = entry.text;
    }
    previousTo = entry.to;
  }
  if (current !== "") templates.push(current);

  return templates.map((template) => template.trim()).filter((template) => template !== "");
}

/**
 * A `{name}` in a template: a value the code filled in.
 *
 * The capturing group is the point: `String.split` *drops* what it matched
 * unless the pattern captures it, so a slot that is not captured leaves the
 * literal parts on either side of it adjacent, and a template built from the
 * halves matches half of every sentence it is meant to match.
 */
const SLOT = /(\{[A-Za-z_][A-Za-z0-9_]*\})/g;

/** The characters a regular expression treats specially. */
const REGEXP_METACHARACTERS = /[.*+?^${}()|[\]\\]/g;

/**
 * A template as a matcher.
 *
 * The filled-in values become a single token — an amount and a transaction id
 * are each one thing with no space in it — which is also why a message that
 * carries a currency in that slot cannot match: `$129.00 USD` is two tokens,
 * and the explanation the code writes has no currency in it to begin with.
 */
function templatePattern(template: string): RegExp {
  const parts = template.split(SLOT);
  const pattern = parts
    .map((part, index) =>
      index % 2 === 1 ? "[^ ]+" : part.replace(REGEXP_METACHARACTERS, "\\$&"),
    )
    .join("");
  return new RegExp(pattern);
}

/** The refund the golden path proposes: the transaction and the amount. */
function goldenPathRefund(fixture: string): { transactionId: string; amount: number } {
  const parsed = JSON.parse(fixture) as {
    calls?: {
      response?: {
        value?: { tool_name?: string; arguments?: Record<string, unknown> };
      };
    }[];
  };
  for (const call of parsed.calls ?? []) {
    const value = call.response?.value;
    if (value?.tool_name !== "billing.issue_refund") continue;
    const proposed = value.arguments ?? {};
    const transactionId = proposed["transaction_id"];
    const amount = proposed["amount"];
    if (typeof transactionId !== "string" || typeof amount !== "number") {
      throw new Error("the golden path's refund proposal names no transaction and no amount");
    }
    return { transactionId, amount };
  }
  throw new Error("the golden path fixture proposes no billing.issue_refund call");
}

/** The explanation sentences the policy engine can produce. */
const TEMPLATES = explanationTemplates(readFileSync(POLICY_SOURCE, "utf8"));

/** The same sentences, as matchers. */
const EXPLANATIONS = TEMPLATES.map(templatePattern);

/** The refund the golden path parks for approval. */
const REFUND = goldenPathRefund(readFileSync(GOLDEN_PATH, "utf8"));

test.describe("the operator-facing messages", () => {
  test("are the ones the shell actually shows", () => {
    // A sanity check on the fixture itself: if `dockerNotRunning` stopped
    // being the function the shell calls, this table would be asserting
    // strings nobody displays, and it would still pass.
    expect([...TITLES]).toEqual([
      "Docker is installed but not running",
      "Docker is not installed",
      "Docker Compose is not available",
    ]);
  });

  test("include the ones the policy engine writes", () => {
    // The corpus is read out of the source, so it cannot be empty by accident:
    // a change that moved the explanation somewhere else would leave this
    // file with nothing to compare against, and a check that compares against
    // nothing passes.
    expect(TEMPLATES.length).toBeGreaterThan(0);
    // And the sentence the golden path's refund produces must be among them.
    // It is the one that names the transaction it refunds, so the extraction
    // has to have found it.
    expect(
      TEMPLATES.some((template) => template.includes("refunding transaction")),
      "no explanation read out of policies.py names the transaction it refunds, so the " +
        "extraction found something other than the sentences explain_risk returns",
    ).toBe(true);
  });
});

test.describe("the verification documents", () => {
  for (const file of ["installer-verification.md", "installer-verification.zh-CN.md"]) {
    const document = readFileSync(path.join(DOCS, file), "utf8");
    const quoted = quotedSpans(document);

    test(`${file} quotes the messages the app shows`, () => {
      for (const title of WALKED_STATES) {
        expect(
          quoted,
          `${file} must quote "${title}" — the window shows exactly that, and a verifier ` +
            `comparing against anything else reports a failure that did not happen.`,
        ).toContain(title);
      }
    });

    test(`${file} quotes the approval explanation the policy engine produces`, () => {
      // The approval card shows two sentences: the model's own reason, which
      // is untrusted and says whatever the model said, and the deterministic
      // explanation, which is written by the policy engine and is the string
      // the approver is told to trust. Only the second one is checked here,
      // and it is checked against the sentences the engine can produce — so a
      // document that quotes the sentence the engine used to write is quoting
      // something the application no longer shows, and the check fails.
      const quotedExplanations = quoted.filter((span) =>
        EXPLANATIONS.some((pattern) => pattern.test(span)),
      );
      expect(
        quotedExplanations,
        `${file} must quote the risk explanation the policy engine produces for the ` +
          `golden path's refund. An operator reads that sentence before deciding ` +
          `whether money moves, so a document that quotes a different one is ` +
          `describing an application that does not exist.`,
      ).not.toEqual([]);

      // And it must be *this* refund's explanation: the transaction the
      // golden path proposes and the amount it moves.
      const said = quotedExplanations.join(" ");
      expect(said, `${file} quotes an explanation about another transaction`).toContain(
        REFUND.transactionId,
      );
      expect(
        said,
        `${file} quotes an explanation about another amount than the golden path moves`,
      ).toMatch(new RegExp(`\\b${REFUND.amount}\\b`));
    });

    test(`${file} does not quote a message the app never shows`, () => {
      for (const stale of SUPERSEDED) {
        expect(
          quoted,
          `${file} still quotes "${stale}", which the application has never displayed. The ` +
            `title it replaced is one of the two Docker states the procedure walks through.`,
        ).not.toContain(stale);
      }
    });
  }
});
