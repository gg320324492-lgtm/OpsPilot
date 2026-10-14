"use client";

/**
 * The one button that rebuilds the knowledge index, and the panel that states
 * what it did.
 *
 * ## WHY THE ZERO CASE IS HALF OF THIS FILE
 *
 * `POST /api/knowledge/reindex` is idempotent (contract §9): every document is
 * re-hashed and only the ones whose hash changed are re-embedded. So a second
 * run on an unchanged tree returns `documents_indexed == 0` -- and that is the
 * answer an operator sees most often, because pressing the button again is the
 * natural thing to do when nothing appears to happen.
 *
 * Saying so is the one piece of copy on this screen that matters. An interface
 * that lets `0` be read as failure has misled the operator about a working
 * system, and the misleading version is the one that gets written by default --
 * a bare `0` next to a button labelled "reindex" reads as nothing happening.
 * `ResultPanel` therefore states the zero case explicitly, in the same tone as
 * the non-zero case rather than as a warning, and distinguishes it from
 * `documents_seen == 0`, which is a genuinely empty tree and a real problem.
 *
 * ## WHY IT TAKES `copy`
 *
 * A Client Component cannot read the locale cookie, and importing a catalogue to
 * find out what language it is in would put every string in the dashboard into
 * the browser bundle for the sake of the fifteen this component renders. The
 * server parent (`knowledge/page.tsx`) passes the `common` and `knowledge`
 * sections down instead. See `decision-actions.tsx` for the same arrangement
 * spelled out at more length.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createScopedT } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

type Result = {
  documentsSeen: number;
  documentsIndexed: number;
  chunksWritten: number;
};

/** Exactly the catalogue sections this component renders. */
export type ReindexCopy = Pick<typeof en, "common" | "knowledge">;

export function ReindexButton({ copy, locale }: { copy: ReindexCopy; locale: string }) {
  const router = useRouter();
  const t = createScopedT(copy, locale);
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
        setFailure(describeFailure(payload, response.status, t));
        return;
      }
      setResult(asResult(payload));

      // The refresh is what makes the new counts appear in the table above,
      // which is where the operator actually reads them.
      router.refresh();
    } catch {
      setFailure(t("common.unreachable", { endpoint: "/api/knowledge/reindex" }));
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
          {busy ? t("knowledge.reindex.reindexing") : t("knowledge.reindex.button")}
        </button>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {t("knowledge.reindex.hint_before")} <code>{t("knowledge.reindex.glob")}</code>{" "}
          {t("knowledge.reindex.hint_middle")} <code>{t("knowledge.reindex.dir")}</code>{" "}
          {t("knowledge.reindex.hint_after")}
        </span>
      </div>

      {result ? <ResultPanel result={result} t={t} /> : null}

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
 * What the reindex actually did.
 *
 * `unchanged` is the state that reads as failure and is not, so the panel's
 * border and ground are *emerald* on it -- the same tone as a successful
 * non-zero run, not a warning -- and the copy below says why. A panel that
 * turned red on `0 indexed` would be the misleading version this component
 * exists to prevent.
 */
function ResultPanel({ result, t }: { result: Result; t: ReturnType<typeof createScopedT> }) {
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
        {result.documentsSeen} {t("knowledge.reindex.seenSuffix")} {result.documentsIndexed}{" "}
        {t("knowledge.reindex.indexedSuffix")} {result.chunksWritten}{" "}
        {t("knowledge.reindex.chunksWrittenSuffix")}
      </p>
      {result.documentsSeen === 0 ? (
        <p className="mt-1 text-xs">
          {t("knowledge.reindex.emptyTree_before")}{" "}
          <code>knowledge/*.md</code> {t("knowledge.reindex.emptyTree_after")}
        </p>
      ) : unchanged ? (
        <>
          <p className="mt-1 text-xs font-medium">
            {t("knowledge.reindex.zeroIndexedNote")}
          </p>
          <p className="mt-1 text-xs">{t("knowledge.reindex.zeroIndexedBody")}</p>
        </>
      ) : (
        <p className="mt-1 text-xs">
          {t("knowledge.reindex.someIndexed", { count: result.documentsIndexed })}{" "}
          {t("knowledge.reindex.someIndexedAfter")}
        </p>
      )}
    </div>
  );
}

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

/**
 * The failure as the API wrote it, or a statement that it wrote nothing usable.
 *
 * The envelope's `code` and `message` are the API's own text and go out
 * verbatim, for the reason `decision-error.ts`'s module docstring gives: the
 * contract fixes several `message`s and Python tests assert them.
 */
function describeFailure(
  payload: unknown,
  status: number,
  t: ReturnType<typeof createScopedT>,
): string {
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
  return t("common.apiUnreadable", { status });
}