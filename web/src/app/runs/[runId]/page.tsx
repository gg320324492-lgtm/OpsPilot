import Link from "next/link";
import { Alert } from "@heroui/react";
import {
  EmptyState,
  ErrorPanel,
  Page,
  Panel,
  Pill,
  StatusBadge,
  Time,
} from "@/components/ui";
import {
  formatClock,
  formatLatency,
  formatRelative,
  formatTimestamp,
  humanise,
} from "@/lib/format";
import {
  isNotFound,
  loadRunDetail,
  type Citation,
  type ClaimLease,
  type ToolCall,
} from "@/lib/data";
import {
  isApprovedAwaitingExecution,
  isReplayBlockedCall,
  lastActivityAt,
  latestModelFailures,
  shouldAutoRefresh,
  type ModelFailure,
} from "@/lib/run-state";
import { permissionLabel, toolCallStatusLabel } from "@/i18n/enums";
import { getCopy, getT } from "@/i18n/server";
import type { TFunction } from "@/i18n/format";
import { ApprovalSummaryCard } from "./approval-summary";
import { RunRefresher } from "./run-refresher";
import { ExportButton } from "./export-button";
import { RecreateRunButton } from "./recreate-button";

/**
 * Run Detail: the screen that answers "what did OpsPilot actually do?".
 *
 * A dynamic route rather than a query parameter because the run id *is* the
 * resource: `/runs/{run_id}` mirrors `GET /api/runs/{run_id}`, so the URL an
 * operator pastes into a colleague's chat identifies the same thing the API
 * call does.
 *
 * ## What on this screen is a label and what is an identifier
 *
 * The longest argument in this file's history is about the empty timeline, and
 * the sentences that decide it are copy -- so they are in the catalogue, in all
 * three forms the lease can take, and `claim-lease.spec.ts` plus
 * `e2e/i18n.spec.ts` both read them off the rendered page.
 *
 * Against them, verbatim in every locale:
 *
 * - **`step.detail`** -- the server assembled it, and an empty `detail` is a
 *   backend gap that has to stay visible rather than be smoothed over.
 * - **`step.step_type`** -- rendered through `humanise`, never through a label
 *   table. `docs/agent-state-machine.md` §3 names the types; an operator reads
 *   this screen against that document.
 * - **`call.error`** -- `refund_above_ceiling` and its siblings are policy
 *   engine constants (`domain/policies.py`).
 * - **`detail.failure_reason`** and **`call.idempotency_key`** -- enum and key.
 * - the run and ticket ids, and the `crm.get_customer`-style tool names.
 */
export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  // Async `params` is required from Next.js 16.
  const [{ runId }, t, { locale, copy: exportCopy }, { copy: recreateCopy }] =
    await Promise.all([params, getT(), getCopy("runExport"), getCopy("run")]);
  const { run, trace, audit } = await loadRunDetail(runId);

  // 404 has its own branch rather than falling into the generic error panel: a
  // run id pasted from a colleague's chat that this deployment does not have is
  // a different problem from the API being down, and "it does not exist" is the
  // only honest thing to say about it.
  if (run.error) {
    return (
      <Page title={t("run.title")}>
        {isNotFound(run.error) ? (
          <div className="space-y-4">
            <Alert status="warning" className="text-sm">
              <Alert.Content>
                <Alert.Title>{t("run.notFoundTitle", { id: runId })}</Alert.Title>
                <Alert.Description>{t("run.notFoundBody")}</Alert.Description>
              </Alert.Content>
            </Alert>
            <Link href="/runs" className="text-sm underline underline-offset-2">
              {t("run.backToRuns")}
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            <ErrorPanel error={run.error} title={t("run.couldNotLoad")} />
            <Link href="/runs" className="text-sm underline underline-offset-2">
              {t("run.backToRuns")}
            </Link>
          </div>
        )}
      </Page>
    );
  }

  // `run.data` is non-null on this branch, but TypeScript cannot narrow a
  // discriminated union through a component's early return in a separate JSX
  // block. Asserting here is honest about that -- the `if (run.error) return`
  // above is the proof, and this line records it rather than making a reader
  // re-derive it.
  const detail = run.data!;
  const pending = detail.pending_approval;
  const isParked = detail.status === "waiting_approval";
  const isFailed = detail.status === "failed";
  // The follow-up path's states: a run only ever leaves the queue for good by
  // reaching one of the two terminal states, and only there is the "did the
  // original write execute" answer a settled fact (the API computes it, and
  // sends `null` for anything still in flight).
  const isTerminalRun = isFailed || detail.status === "completed";
  const citations = detail.citations ?? [];
  const toolCalls = detail.tool_calls ?? [];
  const failedCalls = detail.failed_tool_calls ?? [];
  // M2-01 state distinctions, decided once here and handed to the render below.
  // The predicates live in `lib/run-state.ts` so `e2e/run-state.spec.ts` can
  // test them without a browser; the page only decides what to render.
  const approvedAwaitingExecution = isApprovedAwaitingExecution(detail);
  const replayBlocked = toolCalls.some(isReplayBlockedCall);
  const modelFailures = latestModelFailures(audit.data?.events);
  const lastUpdated = lastActivityAt(detail, trace.data);
  // The tool call the parked approval is about, matched by `tool_call_id`.
  //
  // **The approver card needs it, and it is already on this page.** The run
  // detail carries the run's `tool_calls[]` (contract §3), each with the tool
  // name and the `permission` the call was gated under -- and the approval
  // names the call it is about. So the lookup is a `.find` over rows this page
  // has already read: no second request, and no card that renders without the
  // tool it is asking an operator to approve.
  //
  // It can still come back empty -- a run whose tool-call rows the deployment
  // did not return -- and the card says so rather than inventing a risk level.
  const pendingCall = pending
    ? toolCalls.find((call) => call.id === pending.tool_call_id)
    : undefined;
  // The claim lease, resolved once here. An absent field means the API predates
  // it (`docs/api-contract.md` §3), and the honest reading of "this deployment
  // does not tell me" is still "nothing has claimed it" -- which is what the
  // page said before the field existed.
  const lease = detail.claim_lease;

  return (
    <Page
      title={t("run.titleWithId", { id: detail.id.slice(0, 8) })}
      description={
        <>
          {t("run.descriptionTicket")}{" "}
          <Link
            href={`/tickets?ticket=${detail.ticket_id}`}
            className="font-mono underline underline-offset-2"
          >
            {detail.ticket_id.slice(0, 8)}
          </Link>{" "}
          &middot; {detail.model_provider}/{detail.model_name}
        </>
      }
      actions={
        <>
          <StatusBadge status={detail.status} t={t} />
          {/* M2-01: a non-terminal run moves on its own; refresh this page so
              an operator reading it sees the progress without reaching for the
              reload button. A terminal run stops here -- see `shouldAutoRefresh`
              and the docstring on `RunRefresher`. Rendered as its own component
              because an async Server Component cannot hold client effects. */}
          {shouldAutoRefresh(detail.status) ? <RunRefresher /> : null}
          {/* The M2-06 evidence export: one redacted JSON document for this
              run. The client control is here because a download needs an event
              handler and a credential, and neither belongs in a Server
              Component; see `export-button.tsx`. */}
          <ExportButton runId={detail.id} copy={exportCopy} locale={locale} />
        </>
      }
    >
      <div className="space-y-6">
        {/* -- the state of the run, stated once, in words ------------------- */}
        {isFailed && detail.failure_reason ? (
          <FailureBanner reason={detail.failure_reason} t={t} />
        ) : null}

        {isParked && pending ? (
          <Alert status="warning">
            <Alert.Content>
              <Alert.Title>{t("run.parkedTitle")}</Alert.Title>
              <Alert.Description>
                <p className="mt-1">
                  {t("run.parkedBody_before")}{" "}
                  <Link href="/approvals" className="underline underline-offset-2">
                    {t("run.parkedBody_link")}
                  </Link>{" "}
                  {t("run.parkedBody_after")}
                </p>
              </Alert.Description>
            </Alert.Content>
          </Alert>
        ) : null}

        {/* M2-01: "approved but not executing" is its own state, not a flavour
            of parked. The worker was re-armed by an approval and has not got
            here yet -- the money has not moved and nothing is progressing. It
            needs its own sentence, because both neighbouring banners would be
            false about it: the parked banner says a decision is still owed
            (it was given), and nothing else on the page says the run is one
            worker poll away from continuing. */}
        {approvedAwaitingExecution ? (
          <Alert status="accent">
            <Alert.Content>
              <Alert.Title>{t("run.approvedTitle")}</Alert.Title>
              <Alert.Description>{t("run.approvedBody")}</Alert.Description>
            </Alert.Content>
          </Alert>
        ) : null}

        {/* -- the human follow-up path (Phase 3 M2-03) ---------------------- */}
        {isTerminalRun && (
          <RecreateRunButton
            runId={detail.id}
            copy={recreateCopy}
            locale={locale}
            writeStatus={detail.original_write_status ?? "unknown"}
          />
        )}

        {/* -- the run's own fields ---------------------------------------- */}
        <Panel title={t("run.runPanel")}>
          <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t("run.fieldStatus")}>
              <StatusBadge status={detail.status} t={t} />
            </Field>
            <Field label={t("run.fieldCreated")}>
              <Time value={detail.created_at}>{formatTimestamp(detail.created_at)}</Time>
            </Field>
            <Field label={t("run.fieldStarted")}>
              {detail.started_at ? formatTimestamp(detail.started_at) : "—"}
            </Field>
            <Field label={t("run.fieldCompleted")}>
              {detail.completed_at ? formatTimestamp(detail.completed_at) : "—"}
            </Field>
            {/* M2-01 "most recent update": the newest of completion, last
                trace step, start and creation, shown absolute (tooltip) and
                relative (visible text), the same pairing the approval card
                uses. A run with no activity yet renders the dash, never a
                fabricated time. */}
            <Field label={t("run.fieldLastActivity")}>
              {lastUpdated ? (
                <Time value={lastUpdated}>
                  {formatTimestamp(lastUpdated)}{" "}
                  <span className="text-muted">
                    ({formatRelative(lastUpdated, t)})
                  </span>
                </Time>
              ) : (
                "—"
              )}
            </Field>
            <Field label={t("run.fieldClaim")}>
              <ClaimCell lease={lease} t={t} />
            </Field>
            {detail.failure_reason ? (
              <Field label={t("run.fieldFailureReason")}>
                <span className="text-danger">{detail.failure_reason}</span>
              </Field>
            ) : null}
          </dl>
        </Panel>

        {/* -- model failures, from the audit ledger ------------------------ */}
        <ModelFailurePanel failures={modelFailures} auditError={audit.error} t={t} />

        {/* -- the replay, named -------------------------------------------- */}
        {replayBlocked ? (
          <div
            role="status"
            className="rounded-md border border-border bg-surface-secondary p-4 text-sm text-foreground"
          >
            <p className="font-semibold">{t("run.replayTitle")}</p>
            <p className="mt-1">{t("run.replayBody")}</p>
          </div>
        ) : null}

        {failedCalls.length > 0 ? (
          <div
            role="alert"
            className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm text-foreground"
          >
            <p className="font-semibold">
              {t("run.failedCalls", { count: failedCalls.length })}
              {detail.status === "completed" ? t("run.failedCallsStillCompleted") : ""}.
            </p>
            <p className="mt-1">
              {t("run.failedCallsList_before")} <em>{t("run.failedCallsList_emphasis")}</em>{" "}
              {t("run.failedCallsList_after")}{" "}
              {failedCalls
                .map(
                  (call) =>
                    `${call.tool_name} (${call.error ?? t("run.unknownErrorSuffix")})`,
                )
                .join(", ")}
              {t("run.failedCallsListFooter")}
            </p>
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <Timeline trace={trace} lease={lease} t={t} />
            <ToolCalls calls={toolCalls} t={t} />
          </div>

          <div className="space-y-6">
            {pending ? <ApprovalSummaryCard approval={pending} toolCall={pendingCall} /> : null}
            <Citations citations={citations} t={t} />
            <CustomerReply reply={detail.customer_reply} status={detail.status} t={t} />
          </div>
        </div>
      </div>
    </Page>
  );
}

/**
 * The timeline, from `GET /api/runs/{id}/trace`.
 *
 * **Rendered in exactly the order the server sent it, with `label` and `detail`
 * shown verbatim.** Both of those are acceptance criteria, and the reason is
 * load-bearing rather than ceremonial:
 *
 * - The handler sorts by `sequence` and assembles both strings server-side
 *   (`api/routers/runs.py::_to_trace_step`), precisely so the dashboard and the
 *   README screenshot show the same thing (`docs/api-contract.md` §4). Sorting
 *   or re-deriving them here would make the client disagree with the API about
 *   what happened -- which is the entire failure mode this project exists to
 *   avoid, reproduced in the UI layer.
 * - If a `detail` ever comes back empty, that is the **server's** statement and
 *   is rendered as such, rather than being papered over with a client-side
 *   guess. An empty detail is a backend gap (M7a found two of them) and
 *   hiding it would remove the signal that catches the next one.
 *
 * - The empty *timeline* reads the claim lease, because "no steps yet" is the
 *   state the lease was made observable for. See `noStepsHint`; the short
 *   version is that a claim takes a run without changing its status, so a run a
 *   worker holds and a run nothing claimed render as the same page until one of
 *   them says otherwise.
 *
 * The two sentences this component *does* own -- the panel title, the "no steps"
 * title, and the three lease hints -- are localised. The steps are not.
 */
function Timeline({
  trace,
  lease,
  t,
}: {
  trace: Awaited<ReturnType<typeof loadRunDetail>>["trace"];
  lease: ClaimLease | undefined;
  t: TFunction;
}) {
  return (
    <Panel
      title={t("run.timelineTitle")}
      aside={
        trace.data
          ? t("run.timelineAside", { count: trace.data.steps?.length ?? 0 })
          : undefined
      }
    >
      {trace.error ? (
        <ErrorPanel error={trace.error} title={t("run.couldNotLoadTrace")} />
      ) : !trace.data || (trace.data.steps?.length ?? 0) === 0 ? (
        <EmptyState title={t("run.emptyTimelineTitle")} hint={noStepsHint(lease, t)} />
      ) : (
        <ol className="space-y-0">
          {(trace.data.steps ?? []).map((step, index) => (
            <li key={step.sequence} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-muted" />
                {index < (trace.data?.steps?.length ?? 0) - 1 ? (
                  <span className="w-px flex-1 bg-separator" />
                ) : null}
              </div>
              <div className="min-w-0 flex-1 pb-4">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-xs text-muted">
                    {step.sequence}
                  </span>
                  <span className="text-sm font-medium">{step.label}</span>
                  {/* `step_type` verbatim, through `humanise` and never a label
                      table. See the module docstring. */}
                  <Pill>{humanise(step.step_type)}</Pill>
                  <span className="text-xs text-muted">
                    <Time value={step.at}>{formatClock(step.at)}</Time>
                  </span>
                  <LatencyCell ms={step.latency_ms} stepType={step.step_type} t={t} />
                </div>
                {/* An empty detail is rendered, visibly, rather than skipped.
                    See this component's docstring. */}
                {step.detail ? (
                  <p className="mt-0.5 text-sm text-muted">
                    {step.detail}
                  </p>
                ) : (
                  <p className="mt-0.5 text-sm italic text-warning">
                    {t("run.noStepDetail")}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

/**
 * What an empty timeline is *because*.
 *
 * **This sentence was unconditional and was false half the time.** A claim takes
 * a run without changing its status (`SqlRunStore.claim_next`), so before the
 * lease was on the wire a run a worker was holding and a run nothing had claimed
 * rendered character for character the same, and this hint said the second thing
 * about both. Measured for the full 300 s of `WORKER_CLAIM_LEASE_SECONDS` against
 * a healthy worker (`docs/limitations.md` §2).
 *
 * The unclaimed branch keeps its wording **verbatim**, because that is the case
 * it was written for and `failure-states.spec.ts` asserts on it for a run it
 * produces by stopping the worker — a run that really has never been claimed.
 * The two branches are the whole point: if the claimed branch merely appended to
 * the old sentence, a claimed run would still be described as unclaimed.
 *
 * Plain strings, not JSX. `EmptyState` renders the hint inside a single `<p>`,
 * and Playwright's `getByText` matches on an element's whole text — splitting the
 * sentences across nodes would give two elements matching the same substring.
 *
 * `received` stays in backticks inside the sentence in every locale. It is the
 * status an operator has to compare this page against, and it is the value the
 * API is holding.
 */
function noStepsHint(lease: ClaimLease | undefined, t: TFunction): string {
  if (lease?.state === "held") {
    return (
      t("run.emptyTimelineHeld_before", {
        worker: lease.claimed_by ?? t("run.emptyTimelineHeldUnnamed"),
      }) +
      " " +
      t("run.emptyTimelineHeld_after", { until: formatTimestamp(lease.claimed_until) })
    );
  }
  if (lease?.state === "expired") {
    return (
      t("run.emptyTimelineExpired_before", {
        until: formatTimestamp(lease.claimed_until),
      }) + " " + t("run.emptyTimelineExpired_after")
    );
  }
  return t("run.emptyTimelineUnclaimed");
}

/**
 * The run's claim, as one field beside Started and Completed.
 *
 * The empty-timeline hint above is the *notice*; this is the *record*, so the
 * lease stays readable after the timeline fills in and on a run that is moving
 * normally. Rendered in the panel's own words rather than as a raw timestamp,
 * because "not claimed" and "claimed, nobody is holding it" are the two facts
 * an operator compares against how long the run has been sitting there.
 *
 * `claimed_by` is a `WORKER_ID`, i.e. deployment configuration, and is
 * interpolated verbatim -- `claim-lease.spec.ts` asserts a stand-in worker's name
 * in this exact sentence, and the name is the half of it that tells an operator
 * which container to go and look at.
 */
function ClaimCell({ lease, t }: { lease: ClaimLease | undefined; t: TFunction }) {
  if (lease?.state === "held") {
    return (
      <span>
        {t("run.claimHeld", {
          worker: lease.claimed_by ?? t("run.claimHeldUnnamed"),
          until: formatTimestamp(lease.claimed_until),
        })}
      </span>
    );
  }
  if (lease?.state === "expired") {
    return (
      <span className="text-warning">
        {t("run.claimExpired", { until: formatTimestamp(lease.claimed_until) })}
      </span>
    );
  }
  return <span className="text-muted">{t("run.claimUnclaimed")}</span>;
}

/**
 * One step's latency.
 *
 * `—` is the honest rendering for a `state_change`, and it is labelled rather
 * than left to be misread. M7a measured `latency_ms` onto every *work* step and
 * deliberately left it NULL on state changes, because between two statuses
 * there is no external work and the number would measure a database UPDATE. A
 * dash on fourteen of twenty-one rows is the M7a fix working; a `0` there would
 * be a regression that this component exists to make visible.
 *
 * `stepType` is compared against `"state_change"` here, which is the one place
 * in the dashboard where the raw `step_type` is load-bearing in a *decision*
 * rather than a label -- and it is compared, never rendered. The tooltip is the
 * copy; the comparison is the contract.
 */
function LatencyCell({
  ms,
  stepType,
  t,
}: {
  ms: number | null | undefined;
  stepType: string;
  t: TFunction;
}) {
  if (ms === null || ms === undefined) {
    return (
      <span
        className="text-xs text-muted"
        title={
          stepType === "state_change"
            ? t("run.latencyNotMeasured")
            : t("run.latencyMissing")
        }
      >
        &mdash;
      </span>
    );
  }
  return <span className="text-xs tabular-nums text-muted">{formatLatency(ms)}</span>;
}

function ToolCalls({ calls, t }: { calls: ToolCall[]; t: TFunction }) {
  return (
    <Panel title={t("run.toolCallsTitle")} aside={t("run.toolCallsAside", { count: calls.length })}>
      {calls.length === 0 ? (
        <EmptyState title={t("run.toolCallsEmptyTitle")} hint={t("run.toolCallsEmptyHint")} />
      ) : (
        <ul className="space-y-3">
          {calls.map((call) => (
            <li key={call.id} className="rounded-md border border-separator p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-medium">{call.tool_name}</span>
                <Pill tone={permissionTone(call.permission)}>{permissionLabel(call.permission, t)}</Pill>
                <Pill tone={toolStatusTone(call.status)}>{toolCallStatusLabel(call.status, t)}</Pill>
                {/* M2-01: name the replay on the call itself, beside its
                    rejected pill. The idempotency key below is the evidence;
                    this pill is the reading of it. */}
                {isReplayBlockedCall(call) ? (
                  <Pill tone="warn">{t("run.replayPill")}</Pill>
                ) : null}
                <LatencyCell ms={call.latency_ms} stepType="tool_call" t={t} />
              </div>
              <p className="mt-2 text-xs text-muted">
                {t("run.argumentsLabel")}
              </p>
              <pre className="mt-0.5 overflow-x-auto rounded bg-surface-secondary p-2 font-mono text-xs">
                {JSON.stringify(call.arguments, null, 2)}
              </pre>
              {call.idempotency_key ? (
                <p className="mt-2 text-xs text-muted">
                  {t("run.idempotencyKeyLabel")}{" "}
                  <span className="font-mono text-foreground">
                    {call.idempotency_key}
                  </span>
                </p>
              ) : null}
              {call.result ? (
                <>
                  <p className="mt-2 text-xs text-muted">
                    {t("run.resultLabel")}
                  </p>
                  <pre className="mt-0.5 max-h-64 overflow-auto rounded bg-surface-secondary p-2 font-mono text-xs">
                    {JSON.stringify(call.result, null, 2)}
                  </pre>
                </>
              ) : null}
              {call.error ? (
                <p className="mt-2 text-xs text-danger">
                  {t("run.errorLabel")} {call.error}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/**
 * The Sources panel: the run's `Citation` rows.
 *
 * All four contract fields are shown, because each answers a different question
 * a reader actually asks: `document` *what was consulted*, `chunk` *exactly
 * where*, `score` *how strongly it matched*, `rank` *what order retrieval
 * returned it in*.
 *
 * `chunk` is `"{document_slug}#{anchor}"` (`docs/api-contract.md` §3), rendered
 * in a monospace font precisely so an operator can select it and grep
 * `knowledge/` for the heading. Rendering it as prose would break that, and the
 * string exists to be greppable -- which is why it is in neither catalogue.
 */
function Citations({ citations, t }: { citations: Citation[]; t: TFunction }) {
  return (
    <Panel
      title={t("run.sourcesTitle")}
      aside={citations.length > 0 ? t("run.sourcesAside", { count: citations.length }) : undefined}
    >
      {citations.length === 0 ? (
        <EmptyState title={t("run.sourcesEmptyTitle")} hint={t("run.sourcesEmptyHint")} />
      ) : (
        <ol className="space-y-2">
          {citations.map((citation) => (
            <li key={`${citation.rank}-${citation.chunk}`} className="text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-xs text-muted">
                  #{citation.rank}
                </span>
                <span
                  className="text-xs tabular-nums text-muted"
                  title={t("run.similarityTitle")}
                >
                  {citation.score.toFixed(4)}
                </span>
              </div>
              <p className="font-medium">{citation.document}</p>
              <p className="break-all font-mono text-xs text-muted">
                {citation.chunk}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

/**
 * The customer-visible reply, for a completed run.
 *
 * The escalation flag is shown prominently and not as a footnote. The two
 * outcomes are the same sentence shape with opposite meanings -- "we could not
 * do this, a human is on it" versus "here is your refund" -- and an operator
 * reading an escalated reply as a completed one has been misled about whether
 * the customer's money moved.
 *
 * `reply.body` is the model's output verbatim in every locale, for the reason
 * `explanation.tsx`'s docstring gives: it is text a customer will receive, and a
 * translation of it is a different reply.
 */
function CustomerReply({
  reply,
  status,
  t,
}: {
  reply: { body: string; escalated: boolean } | null | undefined;
  status: string;
  t: TFunction;
}) {
  if (status === "failed") {
    return (
      <Panel title={t("run.replyTitle")}>
        <EmptyState title={t("run.replyFailedTitle")} hint={t("run.replyFailedHint")} />
      </Panel>
    );
  }
  if (!reply) {
    return (
      <Panel title={t("run.replyTitle")}>
        <EmptyState
          title={
            status === "completed" ? t("run.replyNoneCompletedTitle") : t("run.replyNonePendingTitle")
          }
          hint={
            status === "completed"
              ? t("run.replyNoneCompletedHint")
              : t("run.replyNonePendingHint")
          }
        />
      </Panel>
    );
  }
  return (
    <Panel title={t("run.replyTitle")}>
      {reply.escalated ? (
        <p className="mb-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs font-medium text-foreground">
          {t("run.replyEscalated")}
        </p>
      ) : null}
      <p className="whitespace-pre-wrap text-sm">{reply.body}</p>
    </Panel>
  );
}

/**
 * The failed-run banner.
 *
 * `reason` is `failure_reason` -- a contract enum, `max_steps_exceeded` and its
 * siblings -- so it is interpolated verbatim and the sentence around it is the
 * copy that is localised. `failure-states.spec.ts` asserts both halves on this
 * banner, in English and (in `e2e/i18n.spec.ts`) in Chinese.
 */
function FailureBanner({ reason, t }: { reason: string; t: TFunction }) {
  return (
    <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-foreground">
      <p className="font-semibold">
        {t("run.failureBanner_before")} {reason}
      </p>
      <p className="mt-1">{t("run.failureBanner_after")}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">
        {label}
      </dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function permissionTone(permission: string): "neutral" | "warn" | "danger" | "ok" {
  if (permission === "high_risk_write") return "danger";
  if (permission === "safe_write") return "warn";
  return "neutral";
}

function toolStatusTone(status: string): "neutral" | "warn" | "danger" | "ok" {
  if (status === "executed") return "ok";
  if (status === "failed" || status === "rejected") return "danger";
  if (status === "awaiting_approval") return "warn";
  return "neutral";
}

/**
 * The model-failure panel (M2-01: "模型错误……可读").
 *
 * A model failure lives in the audit ledger as a `model_failed` event, not on
 * the run and not on the trace: the run's `failure_reason` is a single coarse
 * token (`model_error` and its siblings), and the trace has no error column.
 * So this panel reads `GET /api/runs/{id}/audit` -- the endpoint the dashboard
 * had never bound -- through `loadRunDetail`, and renders the run's failed
 * model calls newest first.
 *
 * Three things are deliberately NOT done here:
 * - **The category is not translated.** `error_category` is a machine value
 *   from the contract's closed vocabulary (`docs/api-contract.md` §4.1), and
 *   `src/i18n/enums.ts` draws exactly this line: situations get labels,
 *   recorded values stay verbatim. An operator greps the ledger for the same
 *   string they read on the page.
 * - **A failed read is not an empty panel.** `auditError` distinguishes "the
 *   API did not answer" from "no model failed"; rendering the second for the
 *   first would be the empty-panel defect `ui.tsx`'s docstring names, on the
 *   exact screen where an operator is already debugging something.
 * - **The summary is the ledger's own sentence.** `error_summary` was
 *   sanitised and truncated server-side (M1-03); re-wrapping it here would add
 *   a second opinion to a fact.
 */
function ModelFailurePanel({
  failures,
  auditError,
  t,
}: {
  failures: ModelFailure[];
  auditError: unknown;
  t: TFunction;
}) {
  return (
    <Panel title={t("run.modelErrorsTitle")}>
      {auditError ? (
        <EmptyState title={t("run.modelErrorsUnreadableTitle")} hint={t("run.modelErrorsUnreadableHint")} />
      ) : failures.length === 0 ? (
        <EmptyState title={t("run.modelErrorsEmptyTitle")} hint={t("run.modelErrorsEmptyHint")} />
      ) : (
        <ul className="space-y-3">
          {failures.map((failure, index) => (
            <li
              key={`${failure.at}-${index}`}
              className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm"
            >
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-xs text-danger">
                  {failure.category}
                </span>
                <span className="text-xs text-muted">
                  {t("run.modelErrorsStage_before")} {failure.stage}
                  {t("run.modelErrorsStage_after")}
                </span>
                <Time value={failure.at}>{formatClock(failure.at)}</Time>
                <span className="text-xs text-muted">
                  {formatRelative(failure.at, t)}
                </span>
              </div>
              {failure.summary ? (
                <p className="mt-1 break-words text-sm text-foreground">
                  {failure.summary}
                </p>
              ) : null}
              <p className="mt-1 text-xs text-muted">
                {t("run.modelErrorsMeta", {
                  attempt: failure.attempt ?? t("run.modelErrorsUnknown"),
                  status: failure.httpStatus ?? t("run.modelErrorsUnknown"),
                })}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}