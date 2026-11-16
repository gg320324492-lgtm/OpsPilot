"use client";

import { useState } from "react";
import { createScopedT } from "@/i18n/format";

import type en from "@/i18n/dictionaries/en.json";

/** Exactly the catalogue sections this component renders. Nothing else crosses. */
export type ExportCopy = Pick<typeof en, "runExport">;

/**
 * The "export summary" control: fetch the run's redacted export and save it.
 *
 * ## WHY A CLIENT COMPONENT, AND WHY `fetch` HERE
 *
 * The Run Detail page is a Server Component: it has no event handlers, and the
 * operator token never crosses into the browser bundle (`lib/api/client.ts`'s
 * argument). A download needs both an event and a credential, so the page
 * renders this small client control and the credential stays behind a Route
 * Handler (`app/api/runs/[runId]/export/route.ts`) -- the browser calls
 * same-origin, the handler attaches the token, and the JSON comes back as an
 * attachment.
 *
 * **The file is assembled in the browser from the response it received.** The
 * handler sends `Content-Disposition: attachment`, which alone would navigate
 * a plain link; doing it in an event handler instead means a failure can be
 * rendered on the page rather than landing the operator on a JSON document.
 * `URL.createObjectURL` plus a synthetic anchor click is the standard
 * mechanism; the URL is revoked immediately so the blob does not outlive the
 * download.
 *
 * **Failure is rendered, never swallowed.** A 404 (a run id pasted from a chat
 * message) and a token-less deployment are different problems, and both are
 * the API's own error envelope carried through the handler; the message is
 * shown next to the button rather than collapsed into "something went wrong".
 */
export function ExportButton({
  runId,
  copy,
  locale,
}: {
  runId: string;
  /** The catalogue section this component renders, passed down from the Server Component. */
  copy: ExportCopy;
  /** The locale the copy is in, for `createScopedT`. */
  locale: string;
}) {
  const t = createScopedT(copy, locale);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setFailure(null);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/export`);
      if (!response.ok) {
        // The handler forwards the API's error envelope verbatim; read the
        // message out of it rather than inventing one.
        let message = t("runExport.failedStatus", { status: String(response.status) });
        try {
          const body = (await response.json()) as {
            error?: { message?: string };
          };
          if (body?.error?.message) {
            message = body.error.message;
          }
        } catch {
          // A non-JSON error body keeps the status-line message.
        }
        setFailure(message);
        return;
      }
      const payload = await response.text();
      const url = URL.createObjectURL(
        new Blob([payload], { type: "application/json" }),
      );
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `opspilot-run-${runId}.json`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch {
      setFailure(t("runExport.unreachable"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void download()}
        disabled={busy}
        aria-label={t("runExport.ariaLabel")}
        className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50 "
      >
        {busy ? t("runExport.exporting") : t("runExport.button")}
      </button>
      {failure ? (
        <p role="alert" className="max-w-xs text-xs text-danger">
          {failure}
        </p>
      ) : null}
    </div>
  );
}
