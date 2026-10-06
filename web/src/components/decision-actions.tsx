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
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DecisionPanel } from "@/components/decision-panel";
import { formatTimestamp, humanise } from "@/lib/format";
import {
  parseDecisionFailure,
  transportFailure,
  type Decision,
  type DecisionFailure,
} from "@/lib/decision-error";

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
}: {
  approvalId: string;
  /** Set when the API already reports this approval as decided. */
  alreadyDecided?: Decided | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<Decision | null>(null);
  const [failure, setFailure] = useState<DecisionFailure | null>(null);
  const [decided, setDecided] = useState<Decided | null>(null);

  // A decided approval renders as a statement, not as a control. Keeping the
  // buttons on a decided row would invite the second click whose whole purpose
  // here is to be shown what the second click does.
  const settled = decided ?? alreadyDecided ?? null;
  if (settled) {
    return <DecidedLine decided={settled} />;
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
        // The status is forwarded verbatim by the Route Handler, so a 409
        // arrives here as a 409 and `parseDecisionFailure` reads the envelope
        // the API wrote -- including the actor and the moment in `message`.
        setFailure(parseDecisionFailure(payload, response.status));
        return;
      }
      setDecided(asDecided(payload));
      // The card is still on the server's page. A refresh is what removes it
      // from the pending filter; without it the screen would keep showing a row
      // the API no longer considers pending.
      router.refresh();
    } catch {
      setFailure(
        transportFailure(
          "The dashboard could not reach its own decision endpoint. Check that the Next.js server is still running.",
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
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() => decide("reject")}
          disabled={busy !== null}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium disabled:opacity-50 dark:border-neutral-700"
        >
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </button>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          Approving does not run the refund here &mdash; it releases the run and the worker
          performs it.
        </span>
      </div>
      {failure ? <DecisionPanel failure={failure} onDismiss={() => setFailure(null)} /> : null}
    </div>
  );
}

function DecidedLine({ decided }: { decided: Decided }) {
  return (
    <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm dark:border-neutral-800 dark:bg-neutral-900">
      <p className="font-medium">{humanise(decided.status)}</p>
      <p className="mt-0.5 text-xs text-neutral-600 dark:text-neutral-400">
        {decided.decidedBy ? `by ${decided.decidedBy}` : "by an operator"}
        {decided.decidedAt ? ` at ${formatTimestamp(decided.decidedAt)}` : ""}
        {decided.runStatus ? ` · the run moved to ${humanise(decided.runStatus)}` : ""}
      </p>
    </div>
  );
}

/**
 * Narrow the 200 body.
 *
 * `ApprovalDecisionResponse` is a generated type, but this file is a Client
 * Component and the body arrives as `unknown` over `fetch`, so the shape is
 * asserted field by field rather than cast wholesale -- a cast would compile
 * while the API changed underneath it.
 */
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
