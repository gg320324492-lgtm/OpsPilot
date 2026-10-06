import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";

/**
 * `POST /api/approvals/{id}/approve` and `/reject`, proxied from the browser.
 *
 * Same reason as `app/api/tickets/route.ts`, and it matters more here: the
 * button that decides a refund cannot hold a token, because a token in a browser
 * bundle is a refund-capable credential published to anyone who can fetch the
 * bundle. So the browser posts here, same-origin, and this handler calls the API
 * with the token it reads from the server environment.
 *
 * **The 409 is forwarded with its own status, verbatim.** This is the single
 * most important line in the file. `POST .../approve` answers 409
 * `approval_already_decided` on a second click, and that status is what stops a
 * double-click becoming a double grant. Re-wrapping it as a 500, or collapsing
 * it into `{ok: false}`, would deliver a dashboard where the guard fires and the
 * operator is told something went wrong -- the exact outcome the acceptance
 * criterion rules out. The status, the `code`, and `details` all survive the hop
 * untouched, because `approval_id` and `status` are the structured half of the
 * answer and `message` is the half that names who decided and when.
 *
 * A 200 is passed through unmodified too, so the client gets
 * `ApprovalDecisionResponse` with the transitioned run and can say where the run
 * went without a follow-up fetch (contract §5).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ approvalId: string; decision: string }> },
) {
  const { approvalId, decision } = await params;

  if (decision !== "approve" && decision !== "reject") {
    return NextResponse.json(
      {
        error: {
          code: "validation_error",
          message: `'${decision}' is not a decision. The API accepts 'approve' and 'reject'.`,
          details: null,
        },
      },
      { status: 400 },
    );
  }

  let body: unknown = {};
  try {
    const text = await request.text();
    if (text) {
      body = JSON.parse(text) as unknown;
    }
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "validation_error",
          message: "The request body was not valid JSON.",
          details: null,
        },
      },
      { status: 400 },
    );
  }

  try {
    const decided =
      decision === "approve"
        ? await api.approve(approvalId, asDecisionRequest(body))
        : await api.reject(approvalId, asDecisionRequest(body));
    return NextResponse.json(decided, { status: 200 });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        {
          error: {
            code: error.code,
            message: error.message,
            details: error.details,
          },
        },
        // `error.status`, not a fixed 500. See the module comment: the 409 is
        // the reason this route exists in the shape it does.
        { status: error.status },
      );
    }
    // A plain `Error` from `apiFetch` means the dashboard has no token
    // configured -- a dashboard misconfiguration rather than an API failure, so
    // it says which of the two it is instead of reporting a 500 from a server
    // that was never reached.
    const message =
      error instanceof Error
        ? error.message
        : "The dashboard could not record the decision.";
    return NextResponse.json(
      { error: { code: "internal_error", message, details: null } },
      { status: 500 },
    );
  }
}

/**
 * Narrow the forwarded body to `ApprovalDecisionRequest`.
 *
 * `decided_by` and `note` are the only fields the schema allows, and the request
 * model sets `extra: "forbid"` -- so anything else is dropped here rather than
 * forwarded. Sending an unknown field would make the API's 422 about this
 * handler's mistake instead of about the operator's input.
 */
function asDecisionRequest(body: unknown): Parameters<typeof api.approve>[1] {
  if (!body || typeof body !== "object") {
    return {};
  }
  const { decided_by: decidedBy, note } = body as {
    decided_by?: unknown;
    note?: unknown;
  };
  return {
    ...(typeof decidedBy === "string" ? { decided_by: decidedBy } : {}),
    ...(typeof note === "string" ? { note } : {}),
  };
}
