/**
 * The panel that renders a failed or refused decision.
 *
 * This component is the 409 made visible, and it is the acceptance criterion
 * "a second click surfaces the 409" reduced to a place on screen. Two tones,
 * deliberately:
 *
 * - **Amber (`conflict`).** The API refused because the guard held. Nothing
 *   failed; the system declined to write a second grant and said so. Rendering
 *   this in the red failure styling would teach an operator that a correctly
 *   refused double-click is a bug, and the next thing they do is click again.
 * - **Red (`failure`).** The request did not do its job: 401, 404, 500.
 *
 * The `code` and the status are always shown. `code` is the contract's stable
 * branch key (§6) and is what a bug report needs; `message` is shown because
 * the API writes it for humans and it is the only place the deciding actor and
 * moment appear. `details.approval_id` and `details.status` are shown
 * separately, as structured values, because they are the two facts the caller
 * can actually rely on.
 */

import { describeDecisionFailure, type DecisionFailure } from "@/lib/decision-error";

export function DecisionPanel({
  failure,
  onDismiss,
}: {
  failure: DecisionFailure;
  onDismiss: () => void;
}) {
  const view = describeDecisionFailure(failure);
  const conflict = view.tone === "conflict";

  return (
    <div
      role="alert"
      className={
        conflict
          ? "rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-500 dark:bg-amber-950/40 dark:text-amber-100"
          : "rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
      }
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-semibold">{view.headline}</p>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded px-1.5 text-xs underline underline-offset-2 opacity-80 hover:opacity-100"
        >
          Dismiss
        </button>
      </div>
      <p className="mt-1">{view.detail}</p>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs opacity-80">
        <span>
          {view.status > 0 ? `HTTP ${view.status}` : "no response"} &middot; code: {view.code}
        </span>
        {view.approvalId ? <span>approval_id: {view.approvalId}</span> : null}
        {view.approvalStatus ? <span>status: {view.approvalStatus}</span> : null}
        {view.traceId ? <span>trace_id: {view.traceId}</span> : null}
      </p>
    </div>
  );
}
