"use client";

/**
 * The ticket form, and the success panel that names the run it enqueued.
 *
 * ## THE GOLDEN PATH'S VALUES ARE FIXTURES, NOT COPY
 *
 * `GOLDEN_PATH` below is the README's ticket, verbatim, and it is deliberately
 * **not** in the catalogue: the e2e suite submits through the one-click fill and
 * then asserts `toHaveValue(SUBJECT)` against the same literal, and the API
 * fixture `billing@acme.example` is what the fake provider matches on. A
 * localised fixture would be a fixture the suite could not assert and the API
 * could not match.
 *
 * The *labels* and *placeholders* around those fields are copy, and they are in
 * the catalogue. The placeholders are localised; the ones that are themselves
 * data (an email address, an external id) are not, and saying so is the whole of
 * `emailPlaceholder`'s and `externalIdPlaceholder`'s treatment in the catalogue.
 *
 * ## WHY IT TAKES `copy`
 *
 * See `reindex-button.tsx`: a Client Component cannot read the locale cookie,
 * and a catalogue import here would ship the whole dashboard's strings to the
 * browser. The server parent passes the sections down.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createScopedT } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

/**
 * The README's ticket, verbatim -- the same values `golden-path.spec.ts`,
 * `failure-states.spec.ts` and `claim-lease.spec.ts` assert against, and the
 * values the fake provider's recorded scenario matches. A fixture, not copy.
 */
const GOLDEN_PATH = {
  subject: "We were charged twice for invoice INV-2026-384",
  body: "Please investigate and fix it.",
  customer_email: "billing@acme.example",
};

type Submitted = { ticketId: string; runId: string; runStatus: string } | null;

/** Exactly the catalogue sections this component renders. */
export type TicketFormCopy = Pick<typeof en, "common" | "ticketForm">;

export function TicketForm({ copy, locale }: { copy: TicketFormCopy; locale: string }) {
  const router = useRouter();
  const t = createScopedT(copy, locale);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [email, setEmail] = useState("");
  const [externalId, setExternalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState<Submitted>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    setSubmitted(null);
    try {
      const response = await fetch("/api/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject,
          body,
          customer_email: email,
          ...(externalId.trim() ? { external_id: externalId.trim() } : {}),
        }),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        setFailure(describeFailure(payload, response.status, t));
        return;
      }
      const created = payload as { ticket: { id: string }; run: { id: string; status: string } };
      setSubmitted({
        ticketId: created.ticket.id,
        runId: created.run.id,
        // The run's status is a contract enum. It is interpolated **verbatim** --
        // no `runStatusLabel` here -- because it sits inside a sentence about
        // what the run is doing right now, and an operator who is about to watch
        // it on the run page will meet the same token there. A localised
        // `received` here and an untranslated `Received` badge there would be
        // two names for one state.
        runStatus: created.run.status,
      });
      setSubject("");
      setBody("");
      setEmail("");
      setExternalId("");

      // Cleared before the refresh so a half-filled form cannot be submitted a
      // second time while the page re-renders.
      router.refresh();
    } catch {
      setFailure(t("common.unreachable", { endpoint: "/api/tickets" }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("ticketForm.subject")} required>
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
            maxLength={200}
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            placeholder={t("ticketForm.subjectPlaceholder")}
          />
        </Field>
        <Field label={t("ticketForm.customerEmail")} required>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            placeholder={t("ticketForm.emailPlaceholder")}
          />
        </Field>
      </div>

      <Field label={t("ticketForm.body")} required>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
          rows={3}
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          placeholder={t("ticketForm.bodyPlaceholder")}
        />
      </Field>

      <Field label={t("ticketForm.externalId")} hint={t("ticketForm.externalIdHint")}>
        <input
          value={externalId}
          onChange={(e) => setExternalId(e.target.value)}
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          placeholder={t("ticketForm.externalIdPlaceholder")}
        />
      </Field>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {busy ? t("ticketForm.submitting") : t("ticketForm.submit")}
        </button>
        <button
          type="button"
          onClick={() => {
            setSubject(GOLDEN_PATH.subject);
            setBody(GOLDEN_PATH.body);
            setEmail(GOLDEN_PATH.customer_email);
            setExternalId("");
          }}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
        >
          {t("ticketForm.fillExample")}
        </button>
      </div>

      {submitted ? (
        <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
          <p>
            {t("ticketForm.createdTitle", { ticket: submitted.ticketId.slice(0, 8) })}{" "}
            <a
              href={`/runs/${submitted.runId}`}
              className="font-mono underline underline-offset-2"
            >
              {submitted.runId.slice(0, 8)}
            </a>{" "}
            {t("ticketForm.createdIs")} <strong>{submitted.runStatus}</strong>
            {t("ticketForm.createdAfter")}
          </p>
          <p className="mt-1 text-xs">
            {t("ticketForm.createdNote_before")}{" "}
            <a href="/approvals" className="underline underline-offset-2">
              {t("ticketForm.approvalsLink")}
            </a>{" "}
            {t("ticketForm.createdNote_after")}
          </p>
        </div>
      ) : null}

      {failure ? (
        <div
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
        >
          {failure}
        </div>
      ) : null}
    </form>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-600 dark:text-neutral-400">
        {label}
        {required ? <span className="text-red-600 dark:text-red-400"> *</span> : null}
      </span>
      <span className="mt-1 block">{children}</span>
      {hint ? (
        <span className="mt-1 block text-xs text-neutral-500 dark:text-neutral-400">{hint}</span>
      ) : null}
    </label>
  );
}

/**
 * The failure as the API wrote it, with the one case this form has an opinion
 * about appended.
 *
 * `ticket_exists` names a mistake an operator can fix -- they sent an
 * `external_id` the API already has -- so the form adds the instruction. The
 * `code` and the API's `message` stay verbatim in front of it, for the reason
 * `decision-error.ts` gives.
 *
 * Note that `routers/tickets.py::create_ticket` does not currently raise
 * `409 ticket_exists`; it answers a 500 `internal_error` for a duplicate
 * `external_id`. `failure-states.spec.ts` records that as a finding rather than
 * pinning it. This branch is what renders *if* the API ever does raise it, and
 * it is the same branch either way.
 */
function describeFailure(
  payload: unknown,
  status: number,
  t: ReturnType<typeof createScopedT>,
): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string" &&
    "code" in payload.error &&
    typeof payload.error.code === "string"
  ) {
    const { code, message } = payload.error as { code: string; message: string };
    if (code === "ticket_exists") {
      return `${status} ${code}: ${message} ${t("ticketForm.ticketExistsSuffix")}`;
    }
    return `${status} ${code}: ${message}`;
  }
  return t("common.apiUnreadable", { status });
}