import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, StatusBadge, Time } from "@/components/ui";
import { formatRelative } from "@/lib/format";
import {
  loadApprovals,
  loadKnowledge,
  loadRecentRuns,
  loadStatusCounts,
} from "@/lib/data";

/**
 * The Dashboard answers one question: **is the system working right now?**
 *
 * So it leads with what would need a human right now, then with what is broken,
 * and only then with activity. A wall of counts with "3 runs, 2 completed" at
 * the top is a wallpaper: it reads the same whether the system is healthy or
 * has been refusing refunds for an hour. The ordering *is* the argument -- a
 * parked refund and a failed run are the two facts that change what an operator
 * does next, so they are the two things above the fold.
 *
 * Every count is tallied server-side, one request per status, rather than by
 * counting a fetched list. `GET /api/runs` is paginated (contract §10), so
 * counting a page would report "the number of rows on screen" wearing the label
 * "total" -- which is the kind of small lie that makes a dashboard untrustworthy
 * precisely when it is needed.
 */
export default async function DashboardPage() {
  // Concurrent on purpose: four round trips in series would make the slowest one
  // the page's latency, and none of these depends on another.
  const [statusCounts, recent, approvals, knowledge] = await Promise.all([
    loadStatusCounts(),
    loadRecentRuns(),
    loadApprovals("pending"),
    loadKnowledge(),
  ]);

  const { counts } = statusCounts;
  const needsHuman = counts.waiting_approval;
  const failed = counts.failed;
  const inFlight =
    counts.received +
    counts.classifying +
    counts.retrieving +
    counts.planning +
    counts.executing +
    counts.responding;

  // Only groups where the *same proposal* is on screen twice. A run holding two
  // genuinely different refunds is two proposals and needs no warning here --
  // calling that a replay would be the dashboard inventing a claim.
  const replayGroups = approvals.groups.filter(
    (group) => group.duplicated && group.identicalArguments,
  );

  const docs = knowledge.data?.items ?? [];
  const chunks = docs.reduce((total, doc) => total + doc.chunk_count, 0);
  const zeroChunks = docs.filter((doc) => doc.chunk_count === 0).length;

  return (
    <Page
      title="Dashboard"
      description="What needs a human right now, what is broken, and what has been happening."
    >
      <div className="space-y-6">
        {/* -- the two facts that change what an operator does next ---------- */}
        <div className="grid gap-4 md:grid-cols-2">
          <Headline
            tone={needsHuman > 0 ? "warn" : "ok"}
            label="Waiting for approval"
            value={needsHuman}
            hint={
              needsHuman > 0
                ? "A tool call that can move money is parked until a person decides. Nothing has been sent."
                : "Nothing is parked. No risky write is waiting on a decision."
            }
            href="/approvals"
            cta="Open Approvals"
          />
          <Headline
            tone={failed > 0 ? "danger" : "ok"}
            label="Failed runs"
            value={failed}
            hint={
              failed > 0
                ? "OpsPilot did not finish the job. Open a run to read its failure_reason."
                : "No run has ended in FAILED."
            }
            href="/runs?status=failed"
            cta="Show failed runs"
          />
        </div>

        {/* -- the thing a reader must not have to discover themselves ------ */}
        {replayGroups.length > 0 ? (
          <div className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-500 dark:bg-amber-950/50 dark:text-amber-100">
            <p className="font-semibold">
              {replayGroups.length === 1
                ? "One refund is on screen twice."
                : `${replayGroups.length} refunds are each on screen twice.`}
            </p>
            <p className="mt-1">
              A run resumed after an interruption re-proposes the same refund under a new tool
              call id, so it holds two pending approvals.{" "}
              <strong>The money moves only once</strong> &mdash; the idempotency key blocks the
              replay &mdash; but both cards are real and either can be decided. Approving one does
              not decide the other.
            </p>
            <ul className="mt-2 space-y-1">
              {replayGroups.map((group) => (
                <li key={group.runId}>
                  <Link href={`/runs/${group.runId}`} className="font-mono underline underline-offset-2">
                    run {group.runId.slice(0, 8)}
                  </Link>{" "}
                  &mdash; {group.approvals.length} pending cards with identical arguments
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* -- activity ----------------------------------------------------- */}
        <div className="grid gap-6 lg:grid-cols-3">
          <Panel title="Run status" aside={inFlight > 0 ? `${inFlight} in flight` : undefined}>
            <dl className="space-y-2 text-sm">
              <Row
                label="In flight"
                value={inFlight}
                hint="Claimed by the worker and moving: received, classifying, retrieving, planning, executing, responding."
              />
              <Row
                label="Waiting for approval"
                value={counts.waiting_approval}
                hint="Parked. The worker released the row; a person has to decide."
              />
              <Row label="Completed" value={counts.completed} hint="Finished." />
              <Row
                label="Failed"
                value={counts.failed}
                hint="OpsPilot did not finish the job. Each carries a failure_reason."
              />
            </dl>
            <p className="mt-4 text-xs text-neutral-500 dark:text-neutral-400">
              Tallied per status from the API, up to its 200-row maximum page. The{" "}
              <Link href="/runs" className="underline underline-offset-2">
                Runs
              </Link>{" "}
              screen lists them.
            </p>
          </Panel>

          <div className="lg:col-span-2">
            <Panel title="Recent runs" aside={recent.data ? `${recent.data.total} total` : undefined}>
              {recent.error ? (
                <ErrorPanel error={recent.error} title="Could not load recent runs" />
              ) : recent.data && recent.data.items.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                        <th className="py-2 pr-4 font-medium">Run</th>
                        <th className="py-2 pr-4 font-medium">Status</th>
                        <th className="py-2 pr-4 font-medium">Model</th>
                        <th className="py-2 pr-4 font-medium">Created</th>
                        <th className="py-2 font-medium">Failure reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.data.items.map((run) => (
                        <tr
                          key={run.id}
                          className="border-b border-neutral-100 last:border-0 dark:border-neutral-800"
                        >
                          <td className="py-2 pr-4">
                            <Link
                              href={`/runs/${run.id}`}
                              className="font-mono text-xs underline underline-offset-2"
                            >
                              {run.id.slice(0, 8)}
                            </Link>
                          </td>
                          <td className="py-2 pr-4">
                            <StatusBadge status={run.status} />
                          </td>
                          <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                            {run.model_provider}/{run.model_name}
                          </td>
                          <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                            <Time value={run.created_at}>{formatRelative(run.created_at)}</Time>
                          </td>
                          <td className="py-2 text-xs">
                            {run.failure_reason ? (
                              <span className="text-red-700 dark:text-red-300">{run.failure_reason}</span>
                            ) : (
                              <span className="text-neutral-400">&mdash;</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="No runs yet"
                  hint="Submit a ticket and a run is enqueued for it. The worker claims it on its next poll."
                />
              )}
            </Panel>
          </div>
        </div>

        {/* -- the knowledge index, so an empty one is never silent ---------- */}
        <Panel title="Knowledge index">
          {knowledge.error ? (
            <ErrorPanel error={knowledge.error} title="Could not read the index" />
          ) : (
            <p className="text-sm text-neutral-700 dark:text-neutral-300">
              {docs.length} document{docs.length === 1 ? "" : "s"} indexed, {chunks} chunks.
              {" "}
              {zeroChunks > 0 ? (
                <span className="text-amber-700 dark:text-amber-300">
                  {zeroChunks} report zero chunks, which means retrieval cannot cite them
                  &mdash; a run citing one of them would show no sources.
                </span>
              ) : (
                "Every document has chunks, so every document is citable."
              )}{" "}
              <Link href="/knowledge" className="underline underline-offset-2">
                Manage the index
              </Link>
              .
            </p>
          )}
        </Panel>
      </div>
    </Page>
  );
}

/**
 * A number with the sentence that makes it actionable.
 *
 * The colour is the message: amber means *a person is needed*, red means *the
 * system did not do its job*, green means *nothing to do*. Those are three
 * different situations and the whole reason this row exists rather than a
 * plain count.
 */
function Headline({
  tone,
  label,
  value,
  hint,
  href,
  cta,
}: {
  tone: "ok" | "warn" | "danger";
  label: string;
  value: number;
  hint: string;
  href: string;
  cta: string;
}) {
  const ring =
    tone === "danger"
      ? "border-red-300 dark:border-red-800"
      : tone === "warn"
        ? "border-amber-300 dark:border-amber-700"
        : "border-emerald-300 dark:border-emerald-800";
  const number =
    tone === "danger"
      ? "text-red-700 dark:text-red-300"
      : tone === "warn"
        ? "text-amber-700 dark:text-amber-300"
        : "text-emerald-700 dark:text-emerald-300";
  return (
    <div className={`rounded-lg border bg-white px-5 py-4 dark:bg-neutral-900 ${ring}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        {label}
      </p>
      <p className={`mt-1 text-4xl font-semibold tabular-nums ${number}`}>{value}</p>
      <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">{hint}</p>
      <Link href={href} className="mt-3 inline-block text-xs font-medium underline underline-offset-2">
        {cta} &rarr;
      </Link>
    </div>
  );
}

function Row({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-neutral-100 pb-2 last:border-0 dark:border-neutral-800">
      <div>
        <dt className="font-medium">{label}</dt>
        <dd className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">{hint}</dd>
      </div>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}