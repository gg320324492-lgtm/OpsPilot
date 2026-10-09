import Link from "next/link";
import {
  EmptyState,
  ErrorPanel,
  Page,
  Panel,
  Pill,
  StatusBadge,
  Time,
} from "@/components/ui";
import { formatClock, formatLatency, formatTimestamp, humanise } from "@/lib/format";
import { loadRunDetail, type Citation, type ToolCall } from "@/lib/data";
import { ApprovalSummaryCard } from "./approval-summary";

/**
 * Run Detail: the screen that answers "what did OpsPilot actually do?".
 *
 * A dynamic route rather than a query parameter because the run id *is* the
 * resource: `/runs/{run_id}` mirrors `GET /api/runs/{run_id}`, so the URL an
 * operator pastes into a colleague's chat identifies the same thing the API
 * call does.
 */
export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  // Async `params` is required from Next.js 16.
  const { runId } = await params;
  const { run, trace } = await loadRunDetail(runId);

  // 404 has its own branch rather than falling into the generic error panel: a
  // run id pasted from a colleague's chat that this deployment does not have is
  // a different problem from the API being down, and "it does not exist" is the
  // only honest thing to say about it.
  if (run.error) {
    return (
      <Page title="Run detail">
        {isNotFound(run.error) ? (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
              <p className="font-medium">No run with id {runId}.</p>
              <p className="mt-1">
                The API answered 404. If this id came from another deployment&apos;s database it
                will not exist here.
              </p>
            </div>
            <Link href="/runs" className="text-sm underline underline-offset-2">
              Back to Runs
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            <ErrorPanel error={run.error} title="Could not load this run" />
            <Link href="/runs" className="text-sm underline underline-offset-2">
              Back to Runs
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
  const citations = detail.citations ?? [];
  const toolCalls = detail.tool_calls ?? [];
  const failedCalls = detail.failed_tool_calls ?? [];

  return (
    <Page
      title={`Run ${detail.id.slice(0, 8)}`}
      description={
        <>
          Ticket{" "}
          <Link
            href={`/tickets?ticket=${detail.ticket_id}`}
            className="font-mono underline underline-offset-2"
          >
            {detail.ticket_id.slice(0, 8)}
          </Link>{" "}
          &middot; {detail.model_provider}/{detail.model_name}
        </>
      }
      actions={<StatusBadge status={detail.status} />}
    >
      <div className="space-y-6">
        {/* -- the state of the run, stated once, in words ------------------- */}
        {isFailed && detail.failure_reason ? (
          <FailureBanner reason={detail.failure_reason} />
        ) : null}

        {isParked && pending ? (
          <div className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-500 dark:bg-amber-950/50 dark:text-amber-100">
            <p className="font-semibold">Parked. Nothing has been sent.</p>
            <p className="mt-1">
              The run proposed a tool call that can move money and stopped at the approval gate.
              The worker has released the row; it will not proceed until someone decides on the{" "}
              <Link href="/approvals" className="underline underline-offset-2">
                Approvals
              </Link>{" "}
              screen. Approving does not execute the refund inside the request &mdash; it makes the
              run claimable again and the worker performs it.
            </p>
          </div>
        ) : null}

        {/* -- the run's own fields ---------------------------------------- */}
        <Panel title="Run">
          <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Status">
              <StatusBadge status={detail.status} />
            </Field>
            <Field label="Created">
              <Time value={detail.created_at}>{formatTimestamp(detail.created_at)}</Time>
            </Field>
            <Field label="Started">
              {detail.started_at ? formatTimestamp(detail.started_at) : "—"}
            </Field>
            <Field label="Completed">
              {detail.completed_at ? formatTimestamp(detail.completed_at) : "—"}
            </Field>
            {detail.failure_reason ? (
              <Field label="Failure reason">
                <span className="text-red-700 dark:text-red-300">{detail.failure_reason}</span>
              </Field>
            ) : null}
          </dl>
        </Panel>

        {failedCalls.length > 0 ? (
          <div
            role="alert"
            className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100"
          >
            <p className="font-semibold">
              {failedCalls.length} tool call{failedCalls.length === 1 ? "" : "s"} did not complete
              {detail.status === "completed" ? ", and this run still reported completed" : ""}.
            </p>
            <p className="mt-1">
              Anything listed below did <em>not</em> happen:{" "}
              {failedCalls
                .map((call) => `${call.tool_name} (${call.error ?? "unknown error"})`)
                .join(", ")}
              . If a customer reply is shown, it was composed without these effects.
            </p>
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <Timeline trace={trace} />
            <ToolCalls calls={toolCalls} />
          </div>

          <div className="space-y-6">
            {pending ? <ApprovalSummaryCard approval={pending} /> : null}
            <Citations citations={citations} />
            <CustomerReply reply={detail.customer_reply} status={detail.status} />
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
 */
function Timeline({
  trace,
}: {
  trace: Awaited<ReturnType<typeof loadRunDetail>>["trace"];
}) {
  return (
    <Panel
      title="Timeline"
      aside={
        trace.data
          ? `${trace.data.steps?.length ?? 0} steps, as the server ordered them`
          : undefined
      }
    >
      {trace.error ? (
        <ErrorPanel error={trace.error} title="Could not load the trace" />
      ) : !trace.data || (trace.data.steps?.length ?? 0) === 0 ? (
        <EmptyState
          title="No steps recorded yet."
          hint="A run writes a step as it works. If it is still `received`, the worker has not claimed it yet."
        />
      ) : (
        <ol className="space-y-0">
          {(trace.data.steps ?? []).map((step, index) => (
            <li key={step.sequence} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-neutral-400 dark:bg-neutral-500" />
                {index < (trace.data?.steps?.length ?? 0) - 1 ? (
                  <span className="w-px flex-1 bg-neutral-200 dark:bg-neutral-700" />
                ) : null}
              </div>
              <div className="min-w-0 flex-1 pb-4">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-xs text-neutral-500 dark:text-neutral-400">
                    {step.sequence}
                  </span>
                  <span className="text-sm font-medium">{step.label}</span>
                  <Pill>{humanise(step.step_type)}</Pill>
                  <span className="text-xs text-neutral-500 dark:text-neutral-400">
                    <Time value={step.at}>{formatClock(step.at)}</Time>
                  </span>
                  <LatencyCell ms={step.latency_ms} stepType={step.step_type} />
                </div>
                {/* An empty detail is rendered, visibly, rather than skipped.
                    See this component's docstring. */}
                {step.detail ? (
                  <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                    {step.detail}
                  </p>
                ) : (
                  <p className="mt-0.5 text-sm italic text-amber-700 dark:text-amber-400">
                    The server sent no detail for this step type.
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
 * One step's latency.
 *
 * `—` is the honest rendering for a `state_change`, and it is labelled rather
 * than left to be misread. M7a measured `latency_ms` onto every *work* step and
 * deliberately left it NULL on state changes, because between two statuses
 * there is no external work and the number would measure a database UPDATE. A
 * dash on fourteen of twenty-one rows is the M7a fix working; a `0` there would
 * be a regression that this component exists to make visible.
 */
function LatencyCell({ ms, stepType }: { ms: number | null | undefined; stepType: string }) {
  if (ms === null || ms === undefined) {
    return (
      <span
        className="text-xs text-neutral-400"
        title={
          stepType === "state_change"
            ? "Not measured: a state change is a database update, not work the agent did."
            : "The API reported no latency for this step."
        }
      >
        &mdash;
      </span>
    );
  }
  return <span className="text-xs tabular-nums text-neutral-500 dark:text-neutral-400">{formatLatency(ms)}</span>;
}

function ToolCalls({ calls }: { calls: ToolCall[] }) {
  return (
    <Panel title="Tool calls" aside={`${calls.length} proposed`}>
      {calls.length === 0 ? (
        <EmptyState
          title="No tool calls."
          hint="A run that reaches an answer from retrieved policy alone makes no calls. That is abstention or a policy-only answer, not a failure."
        />
      ) : (
        <ul className="space-y-3">
          {calls.map((call) => (
            <li key={call.id} className="rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-medium">{call.tool_name}</span>
                <Pill tone={permissionTone(call.permission)}>{humanise(call.permission)}</Pill>
                <Pill tone={toolStatusTone(call.status)}>{humanise(call.status)}</Pill>
                <LatencyCell ms={call.latency_ms} stepType="tool_call" />
              </div>
              <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">arguments</p>
              <pre className="mt-0.5 overflow-x-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
                {JSON.stringify(call.arguments, null, 2)}
              </pre>
              {call.idempotency_key ? (
                <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
                  idempotency key{" "}
                  <span className="font-mono text-neutral-700 dark:text-neutral-300">
                    {call.idempotency_key}
                  </span>
                </p>
              ) : null}
              {call.result ? (
                <>
                  <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">result</p>
                  <pre className="mt-0.5 max-h-64 overflow-auto rounded bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
                    {JSON.stringify(call.result, null, 2)}
                  </pre>
                </>
              ) : null}
              {call.error ? (
                <p className="mt-2 text-xs text-red-700 dark:text-red-300">error: {call.error}</p>
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
 * string exists to be greppable.
 */
function Citations({ citations }: { citations: Citation[] }) {
  return (
    <Panel title="Sources" aside={citations.length > 0 ? `${citations.length} chunks` : undefined}>
      {citations.length === 0 ? (
        <EmptyState
          title="No citations."
          hint="Abstention is a supported outcome, not an error: the run found no knowledge strong enough to answer from and escalated instead (docs/agent-state-machine.md §3). A completed run with no sources answered from retrieved policy alone only if it cites nothing -- read the timeline to see which."
        />
      ) : (
        <ol className="space-y-2">
          {citations.map((citation) => (
            <li key={`${citation.rank}-${citation.chunk}`} className="text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-xs text-neutral-500 dark:text-neutral-400">
                  #{citation.rank}
                </span>
                <span
                  className="text-xs tabular-nums text-neutral-500 dark:text-neutral-400"
                  title="Cosine similarity between the query and this chunk."
                >
                  {citation.score.toFixed(4)}
                </span>
              </div>
              <p className="font-medium">{citation.document}</p>
              <p className="break-all font-mono text-xs text-neutral-600 dark:text-neutral-400">
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
 */
function CustomerReply({
  reply,
  status,
}: {
  reply: { body: string; escalated: boolean } | null | undefined;
  status: string;
}) {
  if (status === "failed") {
    return (
      <Panel title="Customer reply">
        <EmptyState
          title="No reply — the run failed."
          hint="A failed run does not compose an escalation reply; the failure_reason above says why."
        />
      </Panel>
    );
  }
  if (!reply) {
    return (
      <Panel title="Customer reply">
        <EmptyState
          title={status === "completed" ? "No reply recorded." : "Not composed yet."}
          hint={
            status === "completed"
              ? "The run completed without persisting a response step, so there is nothing to show."
              : "The reply is composed when the run reaches COMPLETED."
          }
        />
      </Panel>
    );
  }
  return (
    <Panel title="Customer reply">
      {reply.escalated ? (
        <p className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          Escalated. The run did not complete the request; it handed it to a human. No refund was
          issued.
        </p>
      ) : null}
      <p className="whitespace-pre-wrap text-sm">{reply.body}</p>
    </Panel>
  );
}

function FailureBanner({ reason }: { reason: string }) {
  return (
    <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
      <p className="font-semibold">This run failed: {reason}</p>
      <p className="mt-1">
        A FAILED run means OpsPilot did not finish the job. Nothing is waiting for approval and
        nothing will be executed. Read the timeline below for where it stopped.
      </p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
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

function isNotFound(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    (error as { status: number }).status === 404
  );
}