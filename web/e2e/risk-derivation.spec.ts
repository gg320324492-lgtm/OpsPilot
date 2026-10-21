/**
 * The risk level on an approval card, and the sentence that says where it came from.
 *
 * ## Why this is not a browser test
 *
 * Every claim here is about one pure function and one table. Starting Compose to
 * assert that `deriveRisk("read", "billing.issue_refund")` returns `read` would
 * cost minutes of wall clock per run for a question that has no browser in it.
 * So this file imports the module and calls it directly, the same way
 * `i18n-catalogue.spec.ts` drives the catalogues and `write-origin.spec.ts`
 * drives the Route Handlers.
 *
 * ## WHAT THIS IS FOR
 *
 * The approval card shows a risk level that the API never sent, and the card
 * also states where that level came from. Those two facts have to agree: a
 * level the dashboard derived, presented as though the API had sent it, is the
 * dashboard inventing evidence.
 *
 * `web/src/lib/risk.ts` derives the level from two sources — the tool call's
 * `permission` field, and a transcription of the tool registry used as a
 * fallback — and returns a provenance alongside it. Three properties:
 *
 * 1. **The field wins.** When the two sources disagree, the tool call's
 *    `permission` is the one that is shown, because that is the permission the
 *    call was *gated under*, and the approver is approving that call.
 * 2. **The fallback is a fallback.** The table is consulted only when the field
 *    is absent, and it is a transcription of `TOOL_REGISTRY` — held to it by
 *    `tests/unit/test_frontend_copies_match_the_backend.py`.
 * 3. **An unknown tool says "unknown".** A name in neither source resolves to
 *    `null`, not to `read`, so the card cannot quietly claim a tool is a read.
 *
 * The first of these had a docstring that said the opposite — "the *higher* of
 * two answers wins" — and the code did not do that. The docstring was wrong,
 * and this file is why it can stay wrong no longer: the rule the code
 * implements is now the rule the code states.
 */

import { expect, test } from "@playwright/test";

import { deriveRisk, permissionForTool, type Permission } from "@/lib/risk";

test.describe("the risk level on an approval card", () => {
  test("the tool call's permission is used when it is there", async () => {
    const risk = deriveRisk("high_risk_write", "billing.issue_refund");

    expect(risk.level).toBe("high_risk_write");
    expect(risk.source).toBe("tool call");
  });

  test("the transcribed table is the fallback, and says so", async () => {
    const risk = deriveRisk(null, "billing.issue_refund");

    expect(risk.level).toBe("high_risk_write");
    expect(risk.source).toBe("tool name");
  });

  test("when the two sources disagree, the field wins", async () => {
    // The tool call was gated under `read`; the table says the tool is a
    // high-risk write. The field is the permission the call passed, and the
    // approver is approving that call -- so the field is what is shown.
    //
    // This is the case the module docstring used to get wrong ("the *higher*
    // of two answers wins"). The docstring now says what the code does.
    const risk = deriveRisk("read", "billing.issue_refund");

    expect(risk.level).toBe("read");
    expect(risk.source).toBe("tool call");
  });

  test("a permission that is not one of the three levels is not a permission", async () => {
    // A row written by a future version of the backend, say. Treating it as
    // absent is right: the fallback is a fallback, and a value the dashboard
    // does not recognise is not a permission it may display.
    const risk = deriveRisk("superuser", "billing.issue_refund");

    expect(risk.level).toBe("high_risk_write");
    expect(risk.source).toBe("tool name");
  });

  test("an unknown tool says unknown rather than read", async () => {
    const risk = deriveRisk(null, "crm.not_a_tool");

    expect(risk.level).toBeNull();
    expect(risk.source).toBe("unknown");
  });

  test("nothing at all says unknown rather than read", async () => {
    const risk = deriveRisk(undefined, null);

    expect(risk.level).toBeNull();
    expect(risk.source).toBe("unknown");
  });
});

test.describe("the transcribed permission table", () => {
  test("every tool the backend registers has an entry", async () => {
    // The table is a copy of `TOOL_REGISTRY`, and the copy is held to the
    // original by `tests/unit/test_frontend_copies_match_the_backend.py`.
    // This asserts the shape of the copy that matters here -- that a name in
    // it resolves, and that a name not in it does not.
    expect(permissionForTool("crm.get_customer")).toBe("read");
    expect(permissionForTool("billing.issue_refund")).toBe("high_risk_write");
    expect(permissionForTool("issues.create")).toBe("safe_write");
    expect(permissionForTool("crm.not_a_tool")).toBeNull();
  });

  test("every entry is one of the three levels the backend defines", async () => {
    const levels: Permission[] = ["read", "safe_write", "high_risk_write"];

    for (const name of [
      "crm.get_customer",
      "crm.get_account",
      "crm.get_subscription",
      "billing.get_invoice",
      "billing.list_transactions",
      "knowledge.search",
      "billing.issue_refund",
      "issues.search",
      "issues.create",
    ]) {
      const level = permissionForTool(name);
      expect(level, `${name} has no entry in the transcribed table`).not.toBeNull();
      expect(levels, `${name} resolves to ${level}`).toContain(level);
    }
  });
});
