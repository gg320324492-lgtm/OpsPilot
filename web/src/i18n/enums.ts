/**
 * Labelling the contract's enums, per locale, without ever changing them.
 *
 * ## THE RULE, AND WHY IT IS DRAWN HERE
 *
 * `docs/api-contract.md` fixes a set of machine identifiers: error `code`s,
 * trace `step_type`s, `RunStatus`, `Permission`, approval and tool-call
 * statuses. Four things in this dashboard render them, and they are not the
 * same kind of thing:
 *
 * | Rendered as | Examples | Localised? |
 * |---|---|---|
 * | A **quoted value** an operator greps or pastes into a bug report | `approval_already_decided`, `origin_foreign`, `refund_above_ceiling`, `state_change` | **No.** Verbatim, in every locale. |
 * | A **label for a situation** -- the dashboard's own vocabulary | run status, approval status, tool-call status, permission | **Yes.** This module. |
 *
 * The difference is that a label answers "which situation is this?", while a
 * quoted value answers "what exactly did the system say?". Localising the first
 * helps the operator who has to act; localising the second breaks the person who
 * has to find it. §P2-F says the same thing of `code`s and `step_type`s, and
 * `e2e/i18n.spec.ts` asserts the "No" row in both locales so a future edit to
 * this rule has to notice it.
 *
 * ## LOCALISING A LABEL STILL DOES NOT TOUCH THE VALUE
 *
 * Nothing here changes what is compared, transmitted or stored. `statusTone()`
 * (`src/lib/format.ts`) still switches on the raw string; the Runs filter still
 * puts `?status=waiting_approval` in the href; `toolStatusTone()` still matches
 * `"executed"`. The audit ledger records the same approval row whichever label
 * an operator was reading, and `GET /api/runs?status=` accepts exactly the same
 * query it always did.
 *
 * ## UNKNOWN VALUES
 *
 * `humanise()` is the fallback and the reason these functions are total: a
 * status this build has no label for renders as it did before i18n arrived
 * (`waiting_for_thing` -> `Waiting for thing`), rather than throwing on a page
 * an operator is trying to make a decision from.
 */

import { humanise } from "@/lib/format";
import type { TFunction } from "@/i18n/format";

/** The enum groups that carry a per-locale label. */
export type LabelGroup = "run" | "approval" | "toolCall" | "permission";

/**
 * The label for a contract enum value in this locale, or `humanise(value)`.
 *
 * The `as MessageKey` cast is deliberate and load-bearing in one direction only:
 * `value` comes off the wire and is not statically known, so it cannot be
 * checked against the union -- and must not be, because an unrecognised value is
 * a real possibility on a deployment running a newer API than this dashboard.
 * The `catch` is what makes that safe: an unlisted value falls through to
 * `humanise` instead of failing the render.
 */
export function labelFor(group: LabelGroup, value: string, t: TFunction): string {
  try {
    return t(`status.${group}.${value}` as never);
  } catch {
    return humanise(value);
  }
}

/** `received` -> `Received`, in this locale. */
export function runStatusLabel(value: string, t: TFunction): string {
  return labelFor("run", value, t);
}

/** `pending` / `approved` / `rejected` / `expired`, in this locale. */
export function approvalStatusLabel(value: string, t: TFunction): string {
  return labelFor("approval", value, t);
}

/** `executed` / `awaiting_approval`, in this locale. */
export function toolCallStatusLabel(value: string, t: TFunction): string {
  return labelFor("toolCall", value, t);
}

/** `read` / `safe_write` / `high_risk_write`, in this locale. */
export function permissionLabel(value: string, t: TFunction): string {
  return labelFor("permission", value, t);
}