import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { loadTickets } from "@/lib/data";
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
 */
export default async function TicketsPage() {
  const tickets = await loadTickets();

  return (
    <Page
      title="Tickets"
      description="Submitting a ticket enqueues an agent run in the same transaction. The worker picks it up on its next poll and it appears in Runs."
    >
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Panel
            title="Tickets"
            aside={tickets.data ? `${tickets.data.total} total` : undefined}
          >
            {tickets.error ? (
              <ErrorPanel error={tickets.error} title="Could not load tickets" />
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
                        runs
                      </Link>
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                title="No tickets yet"
                hint="Use the form to submit one. A run is enqueued with it, in the same transaction, so a ticket can never exist without having been investigated."
              />
            )}
          </Panel>
        </div>

        <div className="lg:col-span-2">
          <Panel title="Submit a ticket">
            <TicketForm />
          </Panel>
        </div>
      </div>
    </Page>
  );
}