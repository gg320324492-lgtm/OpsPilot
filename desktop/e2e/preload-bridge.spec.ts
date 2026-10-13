/**
 * The preload's two channel names, against the module that defines them.
 *
 * ## The defect this exists to prevent
 *
 * `src/preload.ts` cannot import from `src/ipc.ts`: **a preload in a sandboxed
 * renderer cannot `require` a relative module.** Electron replaces `require`
 * there with a limited polyfill, and anything outside it fails the whole script
 * with `module not found` — measured on Electron 44.7.1, where it produced a
 * page whose `window.opspilot` was `undefined` and whose console said only
 * "Unable to load preload script".
 *
 * So the two channel names are written out in the preload, which is a second copy
 * of a constant, which this repository treats as a defect. `src/preload.ts`
 * says why at the top of the file; this is the half of the answer that runs in
 * CI.
 *
 * ## Why reading the source as text is the honest check
 *
 * The alternative is loading the preload, which needs a running Electron and a
 * renderer — i.e. `shell.spec.ts`, which does not run in `desktop-check`. A
 * regex over the two declarations is enough to catch the one thing that can go
 * wrong (someone renaming a channel in `ipc.ts` and not in the preload), and it
 * fails *loudly and specifically* rather than as a window with no bridge.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { PROGRESS_CHANNEL, STATE_CHANNEL } from "../src/ipc";

const PRELOAD_SOURCE = path.resolve(__dirname, "..", "src", "preload.ts");

/** The literal a `const NAME = "..."` declaration assigns. */
function declaredValue(source: string, name: string): string | undefined {
  const match = new RegExp(`const ${name} = "([^"]+)"`).exec(source);
  return match?.[1];
}

test.describe("the preload's channel names", () => {
  test("are the ones src/ipc.ts defines", () => {
    const source = readFileSync(PRELOAD_SOURCE, "utf8");

    // Both directions: a stale copy and a drifted definition are the same
    // failure, and either one leaves the window with no bridge at all.
    expect(declaredValue(source, "PROGRESS_CHANNEL")).toBe(PROGRESS_CHANNEL);
    expect(declaredValue(source, "STATE_CHANNEL")).toBe(STATE_CHANNEL);
  });

  test("are the only reason the preload has a dependency graph at all", () => {
    const source = readFileSync(PRELOAD_SOURCE, "utf8");
    // Every import the preload has must be type-only, or erased. A value import
    // of a sibling file is what produces `module not found: ./…` in a sandbox.
    const imports = source.match(/^import .*$/gm) ?? [];
    for (const statement of imports) {
      const typeOnly = /^import type /.test(statement) || /^import \{ type /.test(statement);
      const electronOnly = statement.includes('from "electron"');
      expect(
        typeOnly || electronOnly,
        `\`${statement}\` is a runtime import in the preload; a sandboxed renderer cannot ` +
          `require a relative module and the preload will not load at all.`,
      ).toBe(true);
    }
  });
});