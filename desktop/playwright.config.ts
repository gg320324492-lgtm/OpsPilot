/**
 * Playwright configuration for the desktop shell.
 *
 * ## Why Playwright, and not a new framework
 *
 * `web/` has no test framework beyond Playwright, and the brief for P2-D1 says
 * to match what is there rather than add one. Two things follow from taking
 * that seriously:
 *
 *   * The **lifecycle** specs run in this file, with no browser at all. That is
 *     the same shape as `web/e2e/stack-diagnostics.spec.ts` and
 *     `write-origin.spec.ts`: the module under test is imported and called
 *     directly, and "the guard rejected it" is an observation rather than an
 *     inference from a container's mood. Because no project below names a
 *     browser, nothing here downloads or launches Chromium — which is why the
 *     CI job for this package is a lint, a typecheck and a `tsc` away from a
 *     green run on a runner with no display.
 *   * The **shell** spec does launch the real Electron app, through
 *     Playwright's `_electron` support. It needs no browser binary either: the
 *     binary is Electron's own. It does need a display, so it skips cleanly
 *     rather than failing when there is none — the same ADR-0004 discipline
 *     `web/e2e/stack.ts` applies to Docker.
 *
 * ## Two projects, not two files
 *
 * The split is by *precondition*, so each spec states what it needs and CI can
 * run the cheap one everywhere: `npm run test:lifecycle` needs nothing, and
 * `npm run test:shell` needs a machine that can open a window.
 *
 * ## Three projects, since the menu needed one of its own
 *
 * The `menu` project is the middle: it launches a real Electron — so it needs
 * the binary, but not a display on Windows and no Docker — and it needs
 * `dist/menu.js`, so it is run through `npm run test:menu`, which builds
 * first. It is separate from `shell` because the shell needs a whole Compose
 * stack and this needs a cookie, and a test that needs a stack to prove
 * something about a menu is a test that will not be run.
 */

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // The shell spec drives one real stack and one real window. Two concurrent
  // workers would both try to bind the dashboard port and both would try to
  // start a Compose project.
  workers: 1,
  // A first launch on a cold machine includes an image pull. Generous, for the
  // reason `playwright.config.ts` in web/ is generous.
  timeout: 8 * 60_000,
  expect: { timeout: 20_000 },
  // No retries. A start-up that passes on the second attempt is a claim about
  // the second attempt.
  retries: 0,
  reporter: [["list"]],
  projects: [
    {
      // Pure functions, fakes, no Electron and no Docker. Runs everywhere.
      name: "lifecycle",
      testMatch: /.*\.spec\.ts/,
      testIgnore: /shell\.spec\.ts|menu-electron\.spec\.ts/,
    },
    {
      // A real Electron and a real cookie store, with no stack behind it.
      // Needs the compiled `dist/menu.js`, so it is run through
      // `npm run test:menu`, which builds first.
      name: "menu",
      testMatch: /menu-electron\.spec\.ts/,
    },
    {
      // The real app, in a real window.
      name: "shell",
      testMatch: /shell\.spec\.ts/,
    },
  ],
});