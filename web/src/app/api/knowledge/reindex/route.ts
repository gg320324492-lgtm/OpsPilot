import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api/client";

/**
 * `POST /api/knowledge/reindex`, proxied from the browser.
 *
 * The third and last Route Handler, for the same reason as the other two: the
 * browser cannot hold the operator token. Reindex is a write that reads the
 * whole `knowledge/` tree and re-embeds what changed, so it is slow and it is
 * the one button on this screen that can fail in a way worth naming.
 *
 * The 200 is `{documents_seen, documents_indexed, chunks_written}` and is passed
 * through untouched. **Nothing here interprets `documents_indexed == 0` as a
 * failure**, because it is not one: contract §9 makes reindex idempotent, so a
 * second call on an unchanged tree legitimately reports zero. Turning that into
 * an error in the proxy layer would be a lie introduced one hop from the API
 * and impossible for the screen to correct.
 */
export async function POST() {
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
