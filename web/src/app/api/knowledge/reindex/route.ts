import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";
import { foreignOriginRefusal } from "@/lib/request-origin";

/**
 * `POST /api/knowledge/reindex`, proxied from the browser.
 *
 * The third and last Route Handler, for the same reason as the other two: the
 * browser cannot hold the operator token. Reindex is a write that reads the
 * whole `knowledge/` tree and re-embeds what changed, so it is slow and it is
 * the one button on this screen that can fail in a way worth naming.
 *
 * **It is in the origin check for the same reason the other two are, and this
 * is the one where "same reason" needs saying out loud.** Reindex looks
 * harmless next to a refund: nothing is granted and no run moves. But it is a
 * token-bearing write that reads a whole directory tree, embeds it and rewrites
 * the index, so a page on another site could trigger it -- repeatedly, at the
 * cost of an embedding pass each time. The rule is not "protect what is
 * dangerous" but "no request from outside this page's origin reaches a handler
 * that holds the token", and that rule is one module
 * (`@/lib/request-origin`) shared by all three rather than a judgement made
 * separately per route. It also notes the shape of this one: `POST` used to
 * take no arguments at all, because there was nothing to read. There is now.
 *
 * The 200 is `{documents_seen, documents_indexed, chunks_written}` and is passed
 * through untouched. **Nothing here interprets `documents_indexed == 0` as a
 * failure**, because it is not one: contract §9 makes reindex idempotent, so a
 * second call on an unchanged tree legitimately reports zero. Turning that into
 * an error in the proxy layer would be a lie introduced one hop from the API
 * and impossible for the screen to correct.
 */
export async function POST(request: Request) {
  // Before the token is spent. See the module comment.
  const refusal = foreignOriginRefusal(request);
  if (refusal !== null) {
    return NextResponse.json(
      { error: { code: refusal.code, message: refusal.message, details: null } },
      { status: refusal.status },
    );
  }

  try {
    const result = await api.reindex();
    return NextResponse.json(result, { status: 200 });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { error: { code: error.code, message: error.message, details: error.details } },
        { status: error.status },
      );
    }
    const message =
      error instanceof Error ? error.message : "The dashboard could not reindex.";
    return NextResponse.json(
      { error: { code: "internal_error", message, details: null } },
      { status: 500 },
    );
  }
}
