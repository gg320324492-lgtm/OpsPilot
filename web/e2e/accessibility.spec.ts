import { AxeBuilder } from "@axe-core/playwright";

import { expect, test } from "@playwright/test";

import { startStackOrSkip } from "./stack";

/**
 * F4-D accessibility audit (plan §F4-D: "Playwright/axe or equivalent checks
 * covering landmark, label, button name, dialog, focus order and status
 * announcement").
 *
 * Runs axe-core against the golden-path pages on the shared stack. The bar is
 * "no serious or critical violations"; moderate/minor findings are recorded in
 * the report rather than failing the run, so the freeze report can carry them
 * honestly.
 */

let stack: Awaited<ReturnType<typeof startStackOrSkip>> | null = null;

test.beforeAll(async () => {
  test.setTimeout(1_800_000);
  stack = await startStackOrSkip("golden-path", { apiHostPort: 18000 });
});

test.afterAll(() => {
  if (stack !== null && !stack.skip) {
    return stack.teardown();
  }
});

const PAGES = ["/", "/tickets", "/runs", "/approvals", "/knowledge"] as const;

for (const path of PAGES) {
  test(`axe: no serious/critical violations on ${path}`, async ({ page }) => {
    skipUnlessUp();

    await page.goto(path);
    await expect(page.getByRole("navigation")).toBeVisible();

    const results = await new AxeBuilder({ page }).analyze();
    const blocking = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    const others = results.violations
      .filter((v) => v.impact !== "serious" && v.impact !== "critical")
      .map((v) => `${v.id} (${v.impact}) x${v.nodes.length}`);

    // Moderate/minor issues are reported into the log for the freeze report,
    // but do not fail the run.
    if (others.length > 0) {
      console.log(`[axe] ${path} non-blocking: ${others.join(", ")}`);
    }

    expect(
      blocking,
      `${path} has serious/critical axe violations: ` +
        blocking.map((v) => `${v.id} x${v.nodes.length}`).join(", "),
    ).toHaveLength(0);
  });
}

function skipUnlessUp(): void {
  test.skip(stack?.skip ?? false, "Docker is not usable on this runner");
}
