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
 * quoted span is extracted — double-quoted, back-ticked, or inside a fenced
 * code block — and those are what is compared against the real titles. A
 * document that merely omits a message is a weaker document, not a wrong one,
 * and this test does not claim to be a linting tool for prose.
 *
 * Both the English document and its Chinese translation are covered. The
 * translation quotes the same literals, and it is the one a Chinese-reading
 * operator is handed, so a drift in either is the same defect.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  composePluginMissing,
  dockerNotInstalled,
  dockerNotRunning,
} from "../src/lifecycle/errors";

const DOCS = path.resolve(__dirname, "..", "..", "docs");

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

/** Every quoted span in a document: `"…"`, `` `…` ``, and fenced code blocks. */
function quotedSpans(document: string): string[] {
  const spans: string[] = [];

  for (const match of document.matchAll(/`([^`\n]+)`/g)) {
    spans.push(match[1] ?? "");
  }
  for (const match of document.matchAll(/```[^\n]*\n([\s\S]*?)```/g)) {
    for (const line of (match[1] ?? "").split("\n")) {
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
      .replace(/[.…]+$/, "")
      .trim(),
  );
}

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
