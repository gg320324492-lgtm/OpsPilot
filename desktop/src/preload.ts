/**
 * The preload: the entire bridge between the untrusted renderer and the shell.
 *
 * ## What runs here, and under what rules
 *
 * This script executes in the preload context — a separate V8 world from the
 * page, with `contextIsolation: true`, and inside the OS sandbox with
 * `sandbox: true`. It can reach `ipcRenderer`; the page cannot. `contextBridge`
 * copies the two functions below across, and **nothing else crosses**: no
 * `ipcRenderer`, no `process`, no `require`. That is what makes the exposed
 * surface reviewable by reading one short file.
 *
 * ## Why the channel names are literals here and not an import
 *
 * **A preload in a sandboxed renderer cannot `require` a relative module.**
 * Electron replaces `require` there with a limited polyfill that resolves only
 * `electron` and a handful of built-ins; anything else fails with
 * `module not found: ./ipc` and **the preload does not load at all** — measured
 * on Electron 44.7.1, and the symptom is a page whose `window.opspilot` is
 * `undefined` with nothing in the log saying why.
 *
 * So the two names are written out. That is a second copy of a constant, which
 * this repository treats as a defect, so it is not left as one:
 * `desktop/e2e/preload-bridge.spec.ts` reads this file and asserts both literals
 * equal the ones `src/ipc.ts` exports, which turns the drift into a red test in
 * CI. The alternative — bundling the preload, or turning `sandbox` off so that
 * `require` works — is not worth a build step or a weaker renderer to save two
 * strings.
 *
 * ## Why the subscription replays what it missed
 *
 * The bootstrap page loads while the backend is already starting, and the first
 * progress events — including the one that says Docker is missing — can be
 * emitted before the page has a listener. A subscribe-only bridge would then
 * render an empty window on exactly the failure that matters most, which is the
 * silent failure D1's acceptance rules out. `getState` covers the same gap from
 * the other direction: a repaint during a long `compose up` gets the whole log.
 *
 * ## The one risk, and why it is accepted
 *
 * This preload also runs on the *dashboard* page, once `main.ts` hands the window
 * over. The dashboard's own scripts do not use `window.opspilot`, and the bridge
 * carries no privileged operation — reading the state back, and receiving a
 * progress string — so a compromised dashboard could learn nothing it cannot
 * already see in the page it is running. Scoping the preload to the bootstrap
 * origin instead would mean a second window or a second load, which buys nothing
 * here.
 */

import { contextBridge, ipcRenderer } from "electron";

import type { BootstrapState, OpsPilotBridge } from "./ipc";

/** Must equal `PROGRESS_CHANNEL` in `src/ipc.ts`. See this file's docstring. */
const PROGRESS_CHANNEL = "opspilot:progress";

/** Must equal `STATE_CHANNEL` in `src/ipc.ts`. See this file's docstring. */
const STATE_CHANNEL = "opspilot:state";

const bridge: OpsPilotBridge = {
  async getState(): Promise<BootstrapState> {
    // The main process validates this frame's origin before it answers
    // (`isTrustedSender`); the cast is the boundary, not an assumption.
    return (await ipcRenderer.invoke(STATE_CHANNEL)) as BootstrapState;
  },

  onProgress(callback: (progress: BootstrapState["progress"][number]) => void): () => void {
    const handler = (_event: unknown, payload: unknown): void => {
      callback(payload as BootstrapState["progress"][number]);
    };
    ipcRenderer.on(PROGRESS_CHANNEL, handler);
    return () => {
      ipcRenderer.removeListener(PROGRESS_CHANNEL, handler);
    };
  },
};

contextBridge.exposeInMainWorld("opspilot", bridge);