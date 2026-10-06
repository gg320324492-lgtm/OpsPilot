"use client";

/**
 * The reindex button, and the client component that owns its result.
 *
 * A Client Component for the same reason as the ticket form: it needs state and
 * a click handler, and it cannot hold the operator token. It POSTs to
 * `app/api/knowledge/reindex/route.ts`, same-origin, and that handler calls the
 * API with the server-side token.
 *
 * **The copy that matters on this screen is the zero case.** Reindex is
 * idempotent (contract §9): documents are re-hashed and unchanged ones are
 * skipped, so a second call on an unchanged tree returns
 * `documents_indexed == 0`. That is a correct answer, not a failure, and it is
 * the most likely answer an operator will see -- they press the button, nothing
 * changes, and `0` is the only number on screen. An interface that lets that be
 * read as "the reindex did not work" has misled the operator about a working
 * system, so the zero case is stated explicitly and in the same tone as the
 * non-zero case rather than as a warning.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

type Result = {
  documentsSeen: number;
  documentsIndexed: number;
  chunksWritten: number;
};

export function ReindexButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function reindex() {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      const response = await fetch("/api/knowledge/reindex", { method: "POST" });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        setFailure(describeFailure(payload, response.status));
        return;
      }
      setResult(asResult(payload));
      // The document list is server-rendered from `GET /api/knowledge`; a
      // reindex that added or changed a document is only visible after the
      // server re-reads it.
      router.refresh();
    } catch {
      setFailure(
        "The dashboard could not reach its own reindex endpoint. Check that the Next.js server is still running.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={reindex}
          disabled={busy}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {busy ? "Reindexing…" : "Reindex knowledge/"}
        </button>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          Re-hashes every <code>*.md</code> under <code>knowledge/</code> and re-embeds what
          changed. Unchanged documents are skipped, so repeating it is safe.
        </span>
      </div>

      {result ? <ResultPanel result={result} /> : null}

      {failure ? (
        <div
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
        >
          {failure}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The three counts, with the zero case stated.
 *
 * `documents_indexed == 0` gets its own sentence rather than being left to be
 * inferred from a number, because it is the answer an operator sees most often
 * and the one most easily misread. `documents_seen == 0` is a *different*
 * zero and is not conflated with it: no documents seen means the tree was not
 * found, which is a real problem worth saying so about.
 */
function ResultPanel({ result }: { result: Result }) {
  const unchanged = result.documentsIndexed === 0;

  return (
    <div
      className={`rounded-md border px-3 py-2 text-sm ${
        unchanged
          ? "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
          : "border-neutral-200 bg-neutral-50 text-neutral-800 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"
      }`}
    >
      <p className="font-medium">
        {result.documentsSeen} seen &middot; {result.documentsIndexed} indexed &middot;{" "}
        {result.chunksWritten} chunks written
      </p>
      {result.documentsSeen === 0 ? (
        <p className="mt-1 text-xs">
          No documents were found. The API reads <code>knowledge/*.md</code> beside{" "}
          <code>src/</code> in the repository root; if that directory is missing or empty there
          is nothing to index, and retrieval will have nothing to cite.
        </p>
      ) : unchanged ? (
        <p className="mt-1 text-xs">
          <strong>0 indexed is the expected result here, not a failure.</strong> Reindex is
          idempotent (contract §9): every document was re-hashed, none of the hashes changed, so
          nothing was re-embedded. The index is up to date. Edit a document and run it again to
          see a non-zero count.
        </p>
      ) : (
        <p className="mt-1 text-xs">
          {result.documentsIndexed} of {result.documentsSeen} had a changed{" "}
          <code>content_hash</code> and were re-embedded.
        </p>
      )}
    </div>
  );
}

/**
 * Narrow the 200 body field by field.
 *
 * A Client Component has no compile-time tie to the generated
 * `KnowledgeReindexResponse`, and the body arrives over `fetch` as `unknown`, so
 * an unchecked cast would compile while the API changed underneath it.
 */
function asResult(payload: unknown): Result {
  if (!payload || typeof payload !== "object") {
    return { documentsSeen: 0, documentsIndexed: 0, chunksWritten: 0 };
  }
  const {
    documents_seen: seen,
    documents_indexed: indexed,
    chunks_written: chunks,
  } = payload as {
    documents_seen?: unknown;
    documents_indexed?: unknown;
    chunks_written?: unknown;
  };
  return {
    documentsSeen: typeof seen === "number" ? seen : 0,
    documentsIndexed: typeof indexed === "number" ? indexed : 0,
    chunksWritten: typeof chunks === "number" ? chunks : 0,
  };
}

/** Turn the forwarded envelope into one sentence. */
function describeFailure(payload: unknown, status: number): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string" &&
    "code" in payload.error &&
    typeof payload.error.code === "string"
  ) {
    const { code, message } = payload.error as { code: string; message: string };
    return `${status} ${code}: ${message}`;
  }
  return `The API answered ${status} with a body this page could not read.`;
}
