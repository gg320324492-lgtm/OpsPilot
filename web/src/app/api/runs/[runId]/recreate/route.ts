import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";
import { foreignOriginRefusal } from "@/lib/request-origin";

/**
 * `POST /api/runs/{id}/recreate`, proxied from the browser to the API.
 *
 * Same shape as `app/api/tickets/route.ts`, and for the same reasons: the
 * button lives in a Client Component, a Client Component cannot hold the
 * operator token (it is deliberately not `NEXT_PUBLIC_`-prefixed), and
 * re-creating a run is a write that eventually reaches an approval gate with a
 * refund behind it -- so the browser posts here, same-origin, and this handler
 * checks the origin first and forwards the API's answer verbatim.
 *
 * **The 409 is forwarded with its own status, verbatim.** `run_not_recreatable`
 * is the guard that keeps two agents off one ticket; re-wrapping it as a 500
 * would tell the operator the dashboard broke when what actually happened is
 * that the run still has a live path. The status, the `code` and the API's own
 * `message` are passed through untouched; the button renders them through the
 * same failure panel the decision buttons use.
 *
 * @param request - The browser's POST. No body: the run id is in the path, and
 *   the endpoint takes no options -- a follow-up is one human decision to look
 *   again, not a configuration.
 * @param params - The dynamic segment, awaited per Next.js 15+ conventions.
 * @returns The API's 201 `{original_run_id, original_write_status, run}` on
 *   success, or the API's error envelope with its own status.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  const refusal = foreignOriginRefusal(request);
  if (refusal !== null) {
    return NextResponse.json(
      { error: { code: refusal.code, message: refusal.message, details: null } },
      { status: refusal.status },
    );
  }

  const { runId } = await params;
  try {
    const created = await api.recreateRun(runId);
    return NextResponse.json(created, { status: 201 });
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
        { status: error.status },
      );
    }
    const message =
      error instanceof Error
        ? error.message
        : "The dashboard could not re-create the run.";
    return NextResponse.json(
      { error: { code: "internal_error", message, details: null } },
      { status: 500 },
    );
  }
}
