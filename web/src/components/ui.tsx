/**
 * The shared visual vocabulary: page frame, panel, badge, empty and error states.
 *
 * These exist so the six screens agree on what a panel looks like and, more
 * importantly, on what an *empty* one means. An operator dashboard has one
 * failure mode that no other application has: rendering an empty list in a
 * broken deployment looks exactly like an idle one. So `EmptyState` always
 * says which of the two it is, and `ErrorPanel` always names the failure rather
 * than leaving a reader to infer it from a blank page.
 */

import type { ReactNode } from "react";
import { ApiError } from "@/lib/api/client";
import { humanise, statusTone } from "@/lib/format";

/** The `max-w-7xl` frame every screen's content sits in, matching the nav. */
export function Page({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description ? (
            <div className="mt-1 max-w-3xl text-sm text-neutral-600 dark:text-neutral-400">
              {description}
            </div>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

/** A titled card. `aside` carries a count or a one-line summary. */
export function Panel({
  title,
  aside,
  children,
  className,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900 ${className ?? ""}`}
    >
      <header className="flex items-baseline justify-between gap-4 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside ? (
          <span className="text-xs text-neutral-500 dark:text-neutral-400">{aside}</span>
        ) : null}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** A run status pill. */
export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${statusTone(status)}`}
    >
      {humanise(status)}
    </span>
  );
}

/**
 * A neutral pill for anything that is not a run status.
 *
 * `tone` is one of the four levels rather than an arbitrary colour, so a
 * permission cannot be rendered in whatever colour the caller felt like.
 */
export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "warn" | "danger" | "ok";
}) {
  const tones: Record<string, string> = {
    neutral:
      "bg-neutral-100 text-neutral-800 ring-neutral-500/20 dark:bg-neutral-800 dark:text-neutral-200 dark:ring-neutral-400/30",
    warn: "bg-amber-100 text-amber-900 ring-amber-600/30 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/40",
    danger:
      "bg-red-100 text-red-900 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30",
    ok: "bg-emerald-100 text-emerald-900 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * The state a reader hits when a list is genuinely empty.
 *
 * `hint` is not decoration: every use here is a statement about what an empty
 * list *means* in that context, so a reader can tell "no tickets have ever
 * arrived" from "the filter you applied matches nothing".
 */
export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-neutral-300 px-4 py-8 text-center dark:border-neutral-700">
      <p className="text-sm font-medium text-neutral-700 dark:text-neutral-300">{title}</p>
      {hint ? (
        <p className="mx-auto mt-1 max-w-md text-xs text-neutral-500 dark:text-neutral-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Render a thrown value as a named failure.
 *
 * The distinction that matters is *authentication* versus everything else.
 * `ApiError.isAuthFailure` exists for exactly this: a wrong token makes every
 * request fail, and an operator seeing an empty dashboard plus a console error
 * concludes the system is idle, which is a plausible-looking lie. The token
 * case therefore gets its own message and is never rendered as a generic
 * failure.
 *
 * The status line and code are shown rather than the stack: `code` is the
 * contract's stable branch key (`docs/api-contract.md` §6) and is what a bug
 * report needs. `message` is shown because it is written for humans -- the
 * contract's own examples name the approval and the operator in it.
 */
export function ErrorPanel({ error, title }: { error: unknown; title?: string }) {
  if (error instanceof ApiError) {
    if (error.isAuthFailure) {
      return (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <p className="font-medium">
            {title ? `${title}: ` : ""}the API rejected the operator token
          </p>
          <p className="mt-1">
            Every request to the API failed authentication, so nothing on this screen could be
            loaded. Set <code>OPSPILOT_OPERATOR_TOKEN</code> in the dashboard&apos;s environment to
            the value the API process was started with, then reload.
          </p>
        </div>
      );
    }
    return (
      <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
        <p className="font-medium">
          {title ? `${title}: ` : ""}the API returned {error.status}
        </p>
        <p className="mt-1">{error.message}</p>
        <p className="mt-1 font-mono text-xs opacity-80">code: {error.code}</p>
      </div>
    );
  }

  if (error instanceof Error) {
    return (
      <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
        <p className="font-medium">{title ? `${title}: ` : ""}unexpected error</p>
        <p className="mt-1">{error.message}</p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
      <p className="font-medium">{title ? `${title}: ` : ""}unexpected error</p>
    </div>
  );
}

/**
 * A `<time>` element for a timestamp.
 *
 * A `title` attribute carrying the absolute value on hover is not polish: the
 * relative label in most of these screens is computed at render time on the
 * server, so a page left open for ten minutes shows a "3 minutes ago" that has
 * not moved. The machine-readable `dateTime` is what makes the row copyable
 * into a bug report without an operator retyping it from the local rendering.
 */
export function Time({ value, children }: { value: string; children: ReactNode }) {
  return (
    <time dateTime={value} title={value}>
      {children}
    </time>
  );
}