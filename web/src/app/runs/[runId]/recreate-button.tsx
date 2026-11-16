"use client";

/**
 * The "re-create this run" button, and the one place its outcome is rendered.
 *
 * **Where it appears, and why only there.** Phase 3 M2-03's entry point is the
 * Run Detail screen's failed and completed states: an operator has read a
 * finished run and decided it needs another pass. A finished run cannot be
 * un-finished, so the action this button takes is *create a sibling* -- a new
 * `received` run on the same ticket, which inherits nothing and must win its
 * own gates and its own approval before anything moves. The button exists so
 * that decision is one click, not a ticket-form detour.
 *
 * **The confirmation is doing real work.** The POST is not free: it enqueues a
 * run that a worker will drive, and on a refunded case it will re-propose a
 * refund that only a *second* human decision can execute. The dialog states
 * that -- what a new run is, what it does not inherit, and what the original's
 * write status was -- so the click is an informed one. `window.confirm` would
 * be a wall of browser chrome around a sentence this dashboard should own, and
 * it cannot carry the write-status line at all; this component renders its own
 * overlay instead, in the same visual language as `DecisionPanel`.
 *
 * **The outcomes are rendered here, all of them.** A 201 redirects to the new
 * run (`/runs/{id}` -- the same URL the API's `run.id` names). A 409
 * `run_not_recreatable` is the guard that keeps two agents off one ticket, and
 * it is rendered as the statement of what happened -- amber, like the 409 the
 * decision buttons show -- never swallowed, never turned into a success, never
 * collapsed into "something went wrong". A transport failure says the
 * dashboard could not reach itself.
 *
 * ## WHY IT TAKES `copy` AND NOT A LOCALE
 *
 * Same reason `DecisionActions` does (see its module docstring): this is a
 * Client Component, it cannot read the locale cookie, and importing the whole
 * catalogue to learn the locale would put every string in the dashboard into
 * the browser bundle. The Server Component passes the `run` catalogue section
 * this component renders, and `createScopedT` builds the same `t`.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createScopedT } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

/** Exactly the catalogue sections this component renders. Nothing else crosses. */
export type RecreateCopy = Pick<typeof en, "run">;

/** The write-status values the API's `original_write_status` can carry. */
type OriginalWriteStatus = "executed" | "not_executed" | "unknown";

/** The 201 body's fields this component needs, narrowed on arrival. */
type RecreateCreated = {
  originalRunId: string;
  originalWriteStatus: OriginalWriteStatus;
  runId: string;
};

type RecreateFailure = {
  status: number;
  code: string;
  message: string;
};

function createdFrom(payload: unknown): RecreateCreated | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const { original_run_id, original_write_status, run } = payload as {
    original_run_id?: unknown;
    original_write_status?: unknown;
    run?: { id?: unknown };
  };
  if (
    typeof original_run_id !== "string" ||
    typeof original_write_status !== "string" ||
    !isWriteStatus(original_write_status) ||
    !run ||
    typeof run.id !== "string"
  ) {
    return null;
  }
  return {
    originalRunId: original_run_id,
    originalWriteStatus: original_write_status,
    runId: run.id,
  };
}

function isWriteStatus(value: string): value is OriginalWriteStatus {
  return value === "executed" || value === "not_executed" || value === "unknown";
}

/** The envelope a non-2xx body is narrowed to, defensively. */
function failureFrom(status: number, payload: unknown): RecreateFailure {
  const body = payload && typeof payload === "object" ? (payload as { error?: unknown }) : null;
  const error =
    body && typeof body.error === "object" && body.error !== null
      ? (body.error as { code?: unknown; message?: unknown })
      : null;
  return {
    status,
    code: typeof error?.code === "string" ? error.code : "error",
    message:
      typeof error?.message === "string"
        ? error.message
        : "The API did not answer with an error envelope.",
  };
}

export function RecreateRunButton({
  runId,
  copy,
  locale,
  writeStatus,
}: {
  /** The finished run this button re-creates from; the URL segment, not a uuid object. */
  runId: string;
  /** The catalogue section this component renders, passed down from the Server Component. */
  copy: RecreateCopy;
  /** The locale the copy is in, which is also the plural rule. */
  locale: string;
  /** The original run's write status, already computed server-side (contract §3). */
  writeStatus: OriginalWriteStatus;
}) {
  const router = useRouter();
  const t = createScopedT(copy, locale);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<RecreateFailure | null>(null);

  async function recreate() {
    setBusy(true);
    setFailure(null);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/recreate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      // The body is read defensively: a handler that died before it could
      // answer sends HTML, and parsing that as JSON would throw inside this
      // `catch` and be reported as a transport failure -- with the real status
      // code lost.
      const payload: unknown = await response.json().catch(() => null);
      if (response.ok) {
        const created = createdFrom(payload);
        if (created) {
          router.push(`/runs/${created.runId}`);
          return;
        }
        setFailure(failureFrom(response.status, null));
        return;
      }
      setFailure(failureFrom(response.status, payload));
    } catch {
      setFailure({
        status: 0,
        code: "transport",
        message: t("run.recreateUnreachable", { endpoint: `/api/runs/${runId}/recreate` }),
      });
    } finally {
      setBusy(false);
    }
  }

  const writeStatusLine = t(`run.recreateWriteStatus_${writeStatus}`);

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={() => {
          setFailure(null);
          setOpen(true);
        }}
        disabled={busy}
        className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
      >
        {busy ? t("run.recreateWorking") : t("run.recreateButton")}
      </button>

      {failure ? (
        <div
          role="alert"
          className={
            failure.status === 409
              ? "rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-sm "
              : "rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm "
          }
        >
          <p className="font-semibold">{t("run.recreateFailedTitle")}</p>
          <p className="mt-0.5">{failure.message}</p>
          <p className="mt-1 font-mono text-xs">
            {failure.status === 0 ? "no response" : `HTTP ${failure.status}`} · code:{" "}
            {failure.code}
          </p>
          <button
            type="button"
            onClick={() => setFailure(null)}
            className="mt-1 text-xs underline underline-offset-2"
          >
            {t("run.recreateDismiss")}
          </button>
        </div>
      ) : null}

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-backdrop p-4"
          onClick={
            busy
              ? undefined
              : () => {
                  setOpen(false);
                }
          }
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("run.recreateDialogTitle")}
            className="w-full max-w-md space-y-3 rounded-lg border border-separator bg-white p-4 shadow-lg  "
            onClick={(event) => event.stopPropagation()}
          >
            <p className="font-semibold">{t("run.recreateDialogTitle")}</p>
            <p className="text-sm">{t("run.recreateDialogBody")}</p>
            <p className="rounded-md border border-separator bg-surface-secondary px-3 py-2 text-xs  ">
              {writeStatusLine}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => recreate()}
                disabled={busy}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-50"
              >
                {busy ? t("run.recreateWorking") : t("run.recreateConfirm")}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
              >
                {t("run.recreateCancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
