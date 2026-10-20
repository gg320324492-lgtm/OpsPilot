import { describeDecisionFailure, type DecisionFailure } from "@/lib/decision-error";
import type { TFunction } from "@/i18n/format";

/**
 * The one place a failed decision is shown, on both screens that can fail one.
 *
 * ## THE THREE STRINGS, AND WHY ONLY ONE OF THEM IS TRANSLATED
 *
 * `view.headline` and `view.detail` are the dashboard explaining itself, so they
 * are localised. The mono row underneath them is the machine's account of what
 * happened, and every part of it is **verbatim in every locale**:
 *
 * | Rendered | Why it is not translated |
 * |---|---|
 * | `HTTP 409` / `no response` | The status line a bug report quotes. |
 * | `code: approval_already_decided` | `docs/api-contract.md` §6 fixes it; every consumer switches on it. |
 * | `approval_id: …`, `status: …`, `trace_id: …` | Field names and ids, from `details`. |
 * | the API's own `message`, inside `detail` | Out of scope by name in §P2-F: the contract fixes several verbatim and Python tests assert them. |
 *
 * So a Chinese operator reading the 409 sees the Chinese explanation wrapped
 * around an English, greppable `HTTP 409 · code: approval_already_decided` line
 * -- which is the thing they would have had to search for otherwise.
 *
 * `t` arrives as a prop because `describeDecisionFailure` builds the sentences
 * and this component is a Client Component that cannot read the locale cookie;
 * see `decision-actions.tsx` for how the `t` is built.
 */
export function DecisionPanel({
  failure,
  onDismiss,
  t,
}: {
  failure: DecisionFailure;
  onDismiss: () => void;
  /** A `t` over the catalogue sections `DecisionActions` was handed, built by `createScopedT`. */
  t: TFunction;
}) {
  const view = describeDecisionFailure(failure, t);
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
          className="shrink-0 rounded px-1.5 py-0.5 text-xs underline underline-offset-2 opacity-80 hover:opacity-100"
        >
          {t("decision.dismiss")}
        </button>
      </div>
      <p className="mt-1">{view.detail}</p>
      {/* The machine's own account, in monospace and untranslated. See above. */}
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs opacity-80">
        <span>
          {view.status > 0
            ? `HTTP ${view.status}`
            : t("common.noResponse")}{" "}
          {t("decision.codeLabel", { code: view.code })}
        </span>
        {view.approvalId ? (
          <span>{t("decision.approvalIdLabel", { id: view.approvalId })}</span>
        ) : null}
        {view.approvalStatus ? (
          <span>{t("decision.statusLabel", { status: view.approvalStatus })}</span>
        ) : null}
        {view.traceId ? <span>{t("decision.traceIdLabel", { id: view.traceId })}</span> : null}
      </p>
    </div>
  );
}