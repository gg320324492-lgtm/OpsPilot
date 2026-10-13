/**
 * The IPC surface, declared once.
 *
 * ## How small this is, and why that is the point
 *
 * Two channels. One pushes progress to the renderer; one lets a freshly
 * painted bootstrap page ask what has already happened, so a reload during a
 * slow `compose up` shows the log rather than a blank page. Neither returns
 * anything the renderer could not already see on the dashboard it is about to
 * load, and neither carries a credential — the operator token is read by the
 * dashboard's *own* server process and is still absent from every browser
 * bundle, which is the property `web/README.md` and P2-A2 exist to protect.
 *
 * A desktop shell that could reach the filesystem, run a command, or read an
 * environment variable on behalf of its renderer would be `nodeIntegration:
 * true` with extra steps. Everything the main process can do that the renderer
 * cannot, it does *before* the renderer exists or *instead of* the renderer.
 *
 * ## The channel names live here so they cannot drift
 *
 * A channel name written twice is a second source of truth wearing a constant's
 * clothes — `dashboard-port.ts` is a whole file about that failure mode, and
 * the two halves here fail in exactly that shape: the preload sends on one
 * channel, the main process listens on the other, and the window shows nothing
 * forever with no error anywhere.
 */

import type { Progress } from "./lifecycle/types";

/** Main → renderer: one thing the shell did, or could not do. */
export const PROGRESS_CHANNEL = "opspilot:progress";

/** Renderer → main: "what has happened so far?", answered once, on paint. */
export const STATE_CHANNEL = "opspilot:state";

/** What `STATE_CHANNEL` returns. Plain data: it crosses into a sandboxed page. */
export interface BootstrapState {
  /** Every progress event so far, oldest first. */
  readonly progress: readonly Progress[];
  /** The dashboard URL, once the backend and the server are both ready. */
  readonly dashboardUrl: string | null;
}

/** The whole bridge the preload exposes on `window.opspilot`. */
export interface OpsPilotBridge {
  /** Ask for the state as of now. Used once, on load. */
  getState(): Promise<BootstrapState>;
  /**
   * Subscribe to progress. The callback receives already-seen events first, so
   * a listener registered after the last send still renders a complete log.
   */
  onProgress(callback: (progress: Progress) => void): () => void;
}

declare global {
  interface Window {
    readonly opspilot: OpsPilotBridge;
  }
}