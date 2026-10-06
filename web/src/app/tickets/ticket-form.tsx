"use client";

/**
 * The ticket submission form, and the client component that owns it.
 *
 * A Client Component because it needs state and an event handler -- everything
 * else on this screen is a Server Component that reads. The split is the one
 * the whole app is built on: **this file never calls the API.** It POSTs to a
 * Route Handler on the same origin, and that handler calls the API with the
 * server-side token. That is what keeps `OPSPILOT_OPERATOR_TOKEN` out of the
 * browser bundle; a `fetch` here would either have no token or tempt someone
 * into adding a `NEXT_PUBLIC_` prefix and shipping a refund-capable credential
 * to anyone who can view the page. See the module comment on
 * `lib/api/client.ts`.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * The README's ticket, verbatim.
 *
 * Offered as a one-click fill rather than left in the docs as an instruction,
 * because "submit the ticket from the README and watch it park" is the
 * golden path this dashboard exists to demonstrate -- and a demo a reader has
 * to retype is a demo most readers get wrong.
 */
const GOLDEN_PATH = {
  subject: "We were charged twice for invoice INV-2026-384",
  body: "Please investigate and fix it.",
  customer_email: "billing@acme.example",
};

type Submitted = { ticketId: string; runId: string; runStatus: string } | null;

export function TicketForm() {
  const router = useRouter();
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
        setFailure(describeFailure(payload, response.status));
        return;
      }
      const created = payload as { ticket: { id: string }; run: { id: string; status: string } };
      setSubmitted({
        ticketId: created.ticket.id,
        runId: created.run.id,
        runStatus: created.run.status,
      });
      setSubject("");
      setBody("");
      setEmail("");
      setExternalId("");
      // The list on this page now has a row that only the server knows about.
      // `refresh()` rather than `router.refresh()` because the list is rendered
      // by the same server component that owns the data, and re-fetching it is
      // the only way the new ticket appears.
      router.refresh();
    } catch {
      setFailure(
        "The dashboard could not reach its own submission endpoint. Check that the Next.js server is still running.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Subject" required>
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
            maxLength={200}
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            placeholder="Charged twice for invoice INV-2026-384"
          />
        </Field>
        <Field label="Customer email" required>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            placeholder="billing@acme.example"
          />
        </Field>
      </div>

      <Field label="Body" required>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
          rows={3}
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          placeholder="We were charged twice for invoice INV-2026-384. Please investigate and fix it."
        />
      </Field>

      <Field
        label="External id"
        hint="Optional. Your own ticket reference. Sending one twice returns 409 ticket_exists rather than creating a duplicate."
      >
        <input
          value={externalId}
          onChange={(e) => setExternalId(e.target.value)}
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          placeholder="ZD-55102"
        />
      </Field>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {busy ? "Submitting…" : "Submit ticket"}
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
          Fill with the README&rsquo;s ticket
        </button>
      </div>

      {submitted ? (
        <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
          <p>
            Ticket <span className="font-mono">{submitted.ticketId.slice(0, 8)}</span> created. Run{" "}
            <a
              href={`/runs/${submitted.runId}`}
              className="font-mono underline underline-offset-2"
            >
              {submitted.runId.slice(0, 8)}
            </a>{" "}
            is <strong>{submitted.runStatus}</strong>.
          </p>
          <p className="mt-1 text-xs">
            The worker claims it on its next poll, so it will not have moved yet. Watch it on the
            run page, or reload in a moment. A refund proposal parks for approval &mdash; go to{" "}
            <a href="/approvals" className="underline underline-offset-2">
              Approvals
            </a>{" "}
            when it does.
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
      {hint ? <span className="mt-1 block text-xs text-neutral-500 dark:text-neutral-400">{hint}</span> : null}
    </label>
  );
}

/**
 * Turn the contract's error envelope into one sentence.
 *
 * `docs/api-contract.md` §6: `code` is the stable branch key and `message` is
 * for humans. Both are shown. A 409 is named explicitly because the most likely
 * cause is submitting the same `external_id` twice, and "conflict" alone would
 * not tell an operator what to change.
 *
 * The response is parsed as `unknown` and then narrowed by hand: this file is
 * a Client Component and has no compile-time tie to the generated error shape,
 * so an unchecked cast here would be exactly the hand-written-copy problem the
 * generated types exist to prevent.
 */
function describeFailure(payload: unknown, status: number): string {
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
      return `409 ${code}: ${message} — this external_id already has a ticket. Clear the field to create a genuinely new one.`;
    }
    return `${status} ${code}: ${message}`;
  }
  return `The API answered ${status} with a body this page could not read.`;
}