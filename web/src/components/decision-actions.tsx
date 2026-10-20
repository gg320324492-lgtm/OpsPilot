"use client";

/**
 * The Approve / Reject buttons, and the one place their outcome is rendered.
 *
 * **Why this is shared.** Approve appears on two screens -- the pending inbox,
 * where an operator works through a queue, and Run Detail, where they arrive
 * from a specific run. Two implementations of the same button would mean two
 * implementations of the 409, and the 409 is the response that stops a
 * double-click becoming a double grant. One implementation means the copy that
 * explains it is written once and cannot be softened on one screen by accident.
 *
 * **What happens on a second click.** `POST /api/approvals/{id}/approve` is a
 * conditional update (`WHERE id = ? AND status = 'pending'`, contract §5). The
 * second click matches no row and the API answers 409
 * `approval_already_decided`. The Route Handler forwards that status unchanged
 * and this component renders it through `DecisionPanel` -- amber, saying the
 * guard worked and nothing was granted twice. It is never swallowed, never
 * turned into a success, and never collapsed into "something went wrong".
 *
 * **No optimistic update.** The buttons do not grey out the card before the
 * response lands. An approval card that visibly leaves the screen on the first
 * click teaches the operator the click worked, which is exactly the belief the
 * two-tabs case punishes; the truth is the server's, and it arrives in the
 * response.
 *
 * ## WHY IT TAKES `copy` AND NOT A LOCALE
 *
 * This is a Client Component. It cannot read the locale cookie, and importing a
 * catalogue to find out what language it is would put every string in the
 * dashboard into the browser bundle for the sake of the ten this component
 * renders. So the Server Component that renders it (`approval-card.tsx` and
 * `approval-summary.tsx`) passes the catalogue sections this component renders,
 * and `createScopedT` builds the same `t` the server builds. The prop is typed
 * as a `Pick` of the English catalogue, so a key renamed in the catalogue is a
 * compile error here rather than a blank button.
 *
 * **Three sections, not one: `decision`, `common` and `status`.** A `t` built
 * over `decision` alone covered none of the keys the other two hold, and each
 * of them is a key this component really asks for:
 *
 * - `status` — `approvalStatusLabel` and `runStatusLabel` look the *label* of a
 *   status up in `status.approval.*` and `status.run.*`. Without the section
 *   the lookup misses, `labelFor` falls back to `humanise`, and a Chinese
 *   operator is shown an English "Approved" on a card whose every other word
 *   is Chinese. The fallback is right for a value this build has no label
 *   for; it is wrong for a label that exists and was not shipped.
 * - `common` — `common.unreachable` (this component's transport failure) and
 *   `common.apiUnreadable` (`decision-error.ts`'s fallback for a body that is
 *   not an envelope). Both threw, and the throw *was* the error: the failure
 *   panel never rendered, and the `catch` meant to report an unreachable
 *   dashboard died inside itself.
 * - `decision` — the buttons, the sentences and the panel's own labels.
 *
 * The `status` gap is the one that made itself visible rather than fatal: a
 * missing `common` key throws loudly, while a missing `status` key degrades
 * into English inside a Chinese sentence — the quieter, and therefore worse,
 * failure.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DecisionPanel } from "@/components/decision-panel";
import { formatTimestamp } from "@/lib/format";
import { createScopedT } from "@/i18n/format";
import { approvalStatusLabel, runStatusLabel } from "@/i18n/enums";
import {
  parseDecisionFailure,
  transportFailure,
  type Decision,
  type DecisionFailure,
} from "@/lib/decision-error";

import type { TFunction } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

/** Exactly the catalogue sections this component renders. Nothing else crosses. */
export type DecisionCopy = Pick<typeof en, "common" | "decision" | "status">;

/** The 200 body's fields this component needs, narrowed on arrival. */
type Decided = {
  status: string;
  decidedAt: string | null;
  decidedBy: string | null;
  runStatus: string | null;
};

export function DecisionActions({
  approvalId,
  alreadyDecided,
  copy,
  locale,
}: {
  approvalId: string;
  /**
   * Set when the approval is already decided, so the server-rendered card shows
   * the statement of what happened instead of the buttons. The 409's own
   * rendering of the same fact goes through `DecisionPanel` below.
   */
  alreadyDecided?: Decided | null;
  /** The catalogue sections this component renders, passed down from the Server Component. */
  copy: DecisionCopy;
  /** The locale the copy is in, which is also the plural rule and the status label set. */
  locale: string;
}) {
  const router = useRouter();
  // The whole `copy`, not a hand-built subset: `createScopedT` is given the
  // sections the server resolved for this request's locale, and the label
  // lookups below need `status` among them. See the module docstring.
  const t = createScopedT(copy, locale);
  const [busy, setBusy] = useState<Decision | null>(null);
  const [failure, setFailure] = useState<DecisionFailure | null>(null);
  const [decided, setDecided] = useState<Decided | null>(null);

  const settled = decided ?? alreadyDecided ?? null;
  if (settled) {
    return <DecidedLine decided={settled} t={t} />;
  }

  async function decide(decision: Decision) {
    setBusy(decision);
    setFailure(null);
    try {
      const response = await fetch(`/api/approvals/${approvalId}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        // The 409 reaches here with the Route Handler's own status and the
        // API's own `code`; nothing in this component rewrites either.
        setFailure(parseDecisionFailure(payload, response.status, t));
        return;
      }
      setDecided(asDecided(payload));

      // The refresh is what removes the card from the pending inbox. Without it
      // the card would still be on screen with no buttons and no statement, and
      // an operator could not tell whether the click landed.
      router.refresh();
    } catch {
      setFailure(
        transportFailure(
          t("common.unreachable", { endpoint: `/api/approvals/${approvalId}/${decision}` }),
        ),
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => decide("approve")}
          disabled={busy !== null}
          className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {busy === "approve" ? t("decision.approving") : t("decision.approve")}
        </button>
        <button
          type="button"
          onClick={() => decide("reject")}
          disabled={busy !== null}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium disabled:opacity-50 dark:border-neutral-700"
        >
          {busy === "reject" ? t("decision.rejecting") : t("decision.reject")}
        </button>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {t("decision.approveNote")}
        </span>
      </div>
      {failure ? (
        <DecisionPanel failure={failure} onDismiss={() => setFailure(null)} t={t} />
      ) : null}
    </div>
  );
}

/**
 * The decided state as a statement rather than a control.
 *
 * `decidedBy` is an operator id from the ledger, not copy, and is interpolated
 * verbatim; `runStatus` is a contract enum whose *label* is localised through
 * the same rule as the badge on the runs list.
 */
function DecidedLine({
  decided,
  t,
}: {
  decided: Decided;
  /** The `t` this component built from its `copy` slice; see the component. */
  t: TFunction;
}) {
  return (
    <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm dark:border-neutral-800 dark:bg-neutral-900">
      <p className="font-medium">{approvalStatusLabel(decided.status, t)}</p>
      <p className="mt-0.5 text-xs text-neutral-600 dark:text-neutral-400">
        {decided.decidedBy
          ? t("decision.decidedBy", { who: decided.decidedBy })
          : t("decision.decidedByUnknown")}
        {decided.decidedAt
          ? ` ${t("decision.decidedAt", { time: formatTimestamp(decided.decidedAt) })}`
          : ""}
        {decided.runStatus
          ? ` ${t("decision.runMovedTo", { status: runStatusLabel(decided.runStatus, t) })}`
          : ""}
      </p>
    </div>
  );
}

function asDecided(payload: unknown): Decided {
  if (!payload || typeof payload !== "object") {
    return { status: "decided", decidedAt: null, decidedBy: null, runStatus: null };
  }
  const { status, decided_at: decidedAt, decided_by: decidedBy, run } = payload as {
    status?: unknown;
    decided_at?: unknown;
    decided_by?: unknown;
    run?: { status?: unknown };
  };
  return {
    status: typeof status === "string" ? status : "decided",
    decidedAt: typeof decidedAt === "string" ? decidedAt : null,
    decidedBy: typeof decidedBy === "string" ? decidedBy : null,
    runStatus: typeof run?.status === "string" ? run.status : null,
  };
}