import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";

/**
 * `GET /api/runs/[runId]/export`, proxied from the browser for a file download.
 *
 * The export is a **read** that the operator saves as evidence, so the browser
 * has to receive the JSON body itself -- not a server-rendered page, and not a
 * redirect to the API (which would need the token in the browser). A Route
 * Handler is the one server-side place that holds the token and can hand the
 * body back: the browser GETs here, same-origin, and this handler calls the API
 * with the token from the server environment (`lib/api/client.ts`'s argument
 * for why the token is never `NEXT_PUBLIC_`).
 *
 * **No origin check, on purpose.** `@/lib/request-origin` guards the three
 * *privileged write* handlers -- ticket creation, approval decisions, reindex
 * -- because each spends the token to change operator data. This handler is a
 * read of one run's already-redacted export: it grants nothing, moves no run
 * and writes nothing. A cross-site `<a href>` or `<form>` GET that lands here
 * would trigger a navigation the operator's own browser renders as JSON -- the
 * same class of request as every read the dashboard's Server Components make.
 * The token constraint (browser never holds it) is what does the protecting;
 * the origin check is for state-changing requests.
 *
 * **The response is passed through untouched.** The API has already applied
 * the redactions the contract names (§4.2); this handler adds a
 * `Content-Disposition: attachment` header so the browser saves the document
 * as `opspilot-run-<id>.json` instead of trying to render it, and forwards the
 * status and body verbatim. A 404 stays a 404 with the API's error envelope;
 * re-wrapping it would be the second copy of an answer the API already gave.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  const { runId } = await params;
  try {
    const payload = await api.exportRun(runId);
    return NextResponse.json(payload, {
      status: 200,
      headers: {
        "Content-Disposition": `attachment; filename="opspilot-run-${runId}.json"`,
      },
    });
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
        // `error.status`, not a fixed 500: the 404 for a run id pasted from a
        // chat message means "there is no such run", and that is what the
        // operator should see.
        { status: error.status },
      );
    }
    // A plain `Error` from `apiFetch` means the dashboard has no token
    // configured -- a misconfiguration, not an API answer.
    const message =
      error instanceof Error
        ? error.message
        : "The dashboard could not produce the export.";
    return NextResponse.json(
      { error: { code: "internal_error", message, details: null } },
      { status: 500 },
    );
  }
}
