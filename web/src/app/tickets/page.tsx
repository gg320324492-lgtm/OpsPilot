import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { isNotFound, loadTicketList, loadTickets } from "@/lib/data";
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
 *
 * ## `?ticket=` -- the filter the links on other screens actually mean
 *
 * A run's ticket id is a link, and it links here. **A link that points at a
 * filter the screen does not apply is a promise the code does not keep**, and
 * this one was pointed at by two screens: the Runs table and the Run Detail
 * page. So the query parameter is honoured -- `GET /api/tickets/{id}` is read
 * and that one ticket is what the list shows.
 *
 * It shows one ticket rather than a filtered list because that is what the
 * link is for: the operator clicked a ticket id and wants *that* ticket, not a
 * list to re-scan. The row is the same row the unfiltered list draws, so
 * there is one rendering of a ticket rather than two.
 */
export default async function TicketsPage({
  searchParams,
}: {
  searchParams: Promise<{ ticket?: string }>;
}) {
  const [{ ticket }, { t, locale, copy }] = await Promise.all([
    searchParams,
    getCopy("common", "ticketForm"),
  ]);
  const ticketId = typeof ticket === "string" && ticket.length > 0 ? ticket : undefined;
  const tickets = ticketId ? await loadTicketList(ticketId) : await loadTickets();
  const noSuchTicket = ticketId !== undefined && isNotFound(tickets.error);

  return (
    <Page title={t("tickets.title")} description={t("tickets.description")}>
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Panel
            title={t("tickets.title")}
            aside={
              tickets.data
                ? ticketId
                  ? t("tickets.filteredToTicket", { id: ticketId.slice(0, 8) })
                  : `${tickets.data.total} ${t("tickets.totalSuffix")}`
                : undefined
            }
          >
            {noSuchTicket ? (
              /* A ticket this deployment does not have has its own branch,
                 rather than falling into the generic error panel: "there is no
                 such ticket" and "the API is down" are different problems, and
                 an empty list would say neither. */
              <div className="space-y-4">
                <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                  <p className="font-medium">{t("tickets.notFoundTitle")}</p>
                  <p className="mt-1">{t("tickets.notFoundBody", { id: ticketId.slice(0, 8) })}</p>
                </div>
                <Link href="/tickets" className="text-sm underline underline-offset-2">
                  {t("tickets.backToTickets")}
                </Link>
              </div>
            ) : tickets.error ? (
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