import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { loadTickets } from "@/lib/data";
import { getCopy } from "@/i18n/server";
import { TicketForm } from "./ticket-form";

/**
 * Tickets: the list, and the form that starts work.
 *
 * The form is on this screen rather than behind a "new ticket" link because
 * `POST /api/tickets` is the only write that starts a run (contract §2), and
 * the golden path a reader is asked to walk starts here. Putting it one click
 * away means the README's demo is something an operator does rather than
 * something they read about.
 *
 * The response says `status: "received"` and returns immediately -- the worker
 * claims the run on its next poll (contract §2). So the form says so, because a
 * reader who submits and sees an unmoved run has to be able to tell "the
 * dashboard is broken" from "the worker has not come back yet".
 *
 * Everything a ticket carries -- `subject`, `body`, `customer_email`,
 * `external_id`, the id -- is the operator's own input and the database's own
 * value, rendered verbatim in every locale. Translating a customer's own words
 * back to them would be a data-corruption bug wearing a localisation feature's
 * clothes. The screen's own copy is the headings, the empty state, and the
 * `runs` link label.
 */
export default async function TicketsPage() {
  const [tickets, { t, locale, copy }] = await Promise.all([
    loadTickets(),
    getCopy("common", "ticketForm"),
  ]);

  return (
    <Page title={t("tickets.title")} description={t("tickets.description")}>
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Panel
            title={t("tickets.title")}
            aside={tickets.data ? `${tickets.data.total} ${t("tickets.totalSuffix")}` : undefined}
          >
            {tickets.error ? (
              <ErrorPanel error={tickets.error} title={t("tickets.couldNotLoad")} />
            ) : tickets.data && tickets.data.items.length > 0 ? (
              <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {tickets.data.items.map((ticket) => (
                  <li key={ticket.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="font-medium">{ticket.subject}</p>
                      <span className="text-xs text-neutral-500 dark:text-neutral-400">
                        <Time value={ticket.created_at}>{formatTimestamp(ticket.created_at)}</Time>
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
                      {ticket.body}
                    </p>
                    <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                      <span className="font-mono">{ticket.id.slice(0, 8)}</span> &middot;{" "}
                      {ticket.customer_email}
                      {ticket.external_id ? (
                        <>
                          {" "}
                          &middot;{" "}
                          <span className="font-mono">{ticket.external_id}</span>
                        </>
                      ) : null}{" "}
                      &middot;{" "}
                      <Link
                        href={`/runs?ticket=${ticket.id}`}
                        className="underline underline-offset-2"
                      >
                        {t("tickets.runsLink")}
                      </Link>
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title={t("tickets.noTicketsTitle")} hint={t("tickets.noTicketsHint")} />
            )}
          </Panel>
        </div>

        <div className="lg:col-span-2">
          <Panel title={t("tickets.submitPanel")}>
            <TicketForm copy={copy} locale={locale} />
          </Panel>
        </div>
      </div>
    </Page>
  );
}