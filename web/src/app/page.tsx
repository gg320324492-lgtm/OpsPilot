import Link from "next/link";
import { EmptyState, ErrorPanel, Page, Panel, StatusBadge, Time } from "@/components/ui";
import { formatRelative } from "@/lib/format";
import {
  loadApprovals,
  loadKnowledge,
  loadRecentRuns,
  loadStatusCounts,
} from "@/lib/data";
import { getT } from "@/i18n/server";

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
 *
 * ## The replay notice is localised, the run ids inside it are not
 *
 * "A run resumed after an interruption..." and the emphasis on "the money moves
 * only once" are copy and are in the catalogue. The `run {id}` link and the
 * `failure_reason` values in the table are not: an id is an id, and
 * `failure_reason` is the contract's own enum. `statusTone` still switches on the
 * raw status, so the colours on this screen mean exactly what they meant before.
 */
export default async function DashboardPage() {
  // Concurrent on purpose: four round trips in series would make the slowest one
  // the page's latency, and none of this depends on another.
  const [t, statusCounts, recent, approvals, knowledge] = await Promise.all([
    getT(),
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
    <Page title={t("dashboard.title")} description={t("dashboard.description")}>
      <div className="space-y-6">
        {/* -- the two facts that change what an operator does next ---------- */}
        <div className="grid gap-4 md:grid-cols-2">
          <Headline
            tone={needsHuman > 0 ? "warn" : "ok"}
            label={t("dashboard.waitingLabel")}
            value={needsHuman}
            hint={
              needsHuman > 0
                ? t("dashboard.waitingHintSome")
                : t("dashboard.waitingHintNone")
            }
            href="/approvals"
            cta={t("dashboard.waitingCta")}
          />
          <Headline
            tone={failed > 0 ? "danger" : "ok"}
            label={t("dashboard.failedLabel")}
            value={failed}
            hint={failed > 0 ? t("dashboard.failedHintSome") : t("dashboard.failedHintNone")}
            href="/runs?status=failed"
            cta={t("dashboard.failedCta")}
          />
        </div>

        {/* -- the thing a reader must not have to discover themselves ------ */}
        {replayGroups.length > 0 ? (
          <div className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-500 dark:bg-amber-950/50 dark:text-amber-100">
            <p className="font-semibold">
              {t("dashboard.replay", { count: replayGroups.length })}
            </p>
            <p className="mt-1">
              {t("dashboard.replayBody_before")} <strong>{t("dashboard.replayEmphasis")}</strong>{" "}
              {t("dashboard.replayBody_after")}
            </p>
            <ul className="mt-2 space-y-1">
              {replayGroups.map((group) => (
                <li key={group.runId}>
                  <Link
                    href={`/runs/${group.runId}`}
                    className="font-mono underline underline-offset-2"
                  >
                    {t("dashboard.replayRunPrefix")} {group.runId.slice(0, 8)}
                  </Link>{" "}
                  {t("dashboard.replayRunSuffix", { count: group.approvals.length })}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* -- activity ----------------------------------------------------- */}
        <div className="grid gap-6 lg:grid-cols-3">
          <Panel
            title={t("dashboard.runStatusPanel")}
            aside={inFlight > 0 ? `${inFlight} ${t("dashboard.inFlightSuffix")}` : undefined}
          >
            <dl className="space-y-2 text-sm">
              <Row
                label={t("dashboard.rowInFlight")}
                value={inFlight}
                hint={t("dashboard.rowInFlightHint")}
              />
              <Row
                label={t("dashboard.rowWaiting")}
                value={counts.waiting_approval}
                hint={t("dashboard.rowWaitingHint")}
              />
              <Row
                label={t("dashboard.rowCompleted")}
                value={counts.completed}
                hint={t("dashboard.rowCompletedHint")}
              />
              <Row
                label={t("dashboard.rowFailed")}
                value={counts.failed}
                hint={t("dashboard.rowFailedHint")}
              />
            </dl>
            <p className="mt-4 text-xs text-neutral-500 dark:text-neutral-400">
              {t("dashboard.tallyNote_before")}{" "}
              <Link href="/runs" className="underline underline-offset-2">
                {t("nav.runs")}
              </Link>{" "}
              {t("dashboard.tallyNote_after")}
            </p>
          </Panel>

          <div className="lg:col-span-2">
            <Panel
              title={t("dashboard.recentRuns")}
              aside={recent.data ? `${recent.data.total} ${t("dashboard.totalSuffix")}` : undefined}
            >
              {recent.error ? (
                <ErrorPanel error={recent.error} title={t("dashboard.couldNotLoadRuns")} />
              ) : recent.data && recent.data.items.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                        <th className="py-2 pr-4 font-medium">{t("dashboard.colRun")}</th>
                        <th className="py-2 pr-4 font-medium">{t("dashboard.colStatus")}</th>
                        <th className="py-2 pr-4 font-medium">{t("dashboard.colModel")}</th>
                        <th className="py-2 pr-4 font-medium">{t("dashboard.colCreated")}</th>
                        <th className="py-2 font-medium">{t("dashboard.colFailureReason")}</th>
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
                            <StatusBadge status={run.status} t={t} />
                          </td>
                          <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                            {run.model_provider}/{run.model_name}
                          </td>
                          <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                            <Time value={run.created_at}>
                              {formatRelative(run.created_at, t)}
                            </Time>
                          </td>
                          {/* `failure_reason` is the contract's own enum, rendered
                              verbatim. See the module docstring. */}
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
                <EmptyState title={t("dashboard.noRunsTitle")} hint={t("dashboard.noRunsHint")} />
              )}
            </Panel>
          </div>
        </div>

        {/* -- the knowledge index, so an empty one is never silent ---------- */}
        <Panel title={t("dashboard.knowledgePanel")}>
          {knowledge.error ? (
            <ErrorPanel error={knowledge.error} title={t("dashboard.couldNotReadIndex")} />
          ) : (
            <p className="text-sm text-neutral-700 dark:text-neutral-300">
              {t("dashboard.indexed", { count: docs.length })}, {chunks}{" "}
              {t("dashboard.chunksSuffix")}.{" "}
              {zeroChunks > 0 ? (
                <span className="text-amber-700 dark:text-amber-300">
                  {t("dashboard.zeroChunks", { count: zeroChunks })}
                </span>
              ) : (
                t("dashboard.allCitable")
              )}{" "}
              <Link href="/knowledge" className="underline underline-offset-2">
                {t("dashboard.manageIndex")}
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
 *
 * `label`, `hint` and `cta` arrive already resolved: every sentence this
 * component shows is chosen by the page above it from a branch on a count, and
 * the branch is where the locale belongs.
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