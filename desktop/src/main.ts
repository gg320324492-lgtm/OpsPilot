/**
 * The main process: one window, and the lifecycle that fills it.
 *
 * ## The shape of a launch
 *
 *   1. A window opens on the bootstrap page — this app's own page, served from
 *      `opspilot://`, showing what the shell is doing while it works.
 *   2. The backend comes up (or is found already up) and the dashboard server is
 *      spawned, with every step streamed to that page.
 *   3. The window is handed to the dashboard at `http://127.0.0.1:<port>`.
 *   4. On quit, the teardown runs to completion and only then does the app exit.
 *
 * ## Why quit waits
 *
 * `before-quit` is given the only thing it can be given — `preventDefault()`,
 * run the teardown, then quit for real. Quitting immediately instead would
 * leave a developer with a half-up stack and a window that said it closed
 * cleanly. The teardown is bounded by `teardownTimeoutMs`, so a wedged Docker
 * delays the close by a known amount rather than forever; `quitAfterTeardown`
 * is what makes the second quit real rather than another `before-quit`.
 *
 * ## What is deliberately not here
 *
 * No renderer-facing API beyond the one `ipcMain.handle` in `registerIpc`, no
 * `shell.openExternal` on anything a page chose, and no token anywhere near the
 * bridge. The operator token is read by the dashboard's *own* server process and
 * is still absent from every browser bundle — the property P2-A2 exists to keep.
 * Where it is *read from* is `lifecycle/token.ts`'s decision; an installed copy
 * keeps a generated one in this Windows user's application-data folder, which is
 * the same place Chromium keeps its own profile.
 */

import { app, BrowserWindow, dialog, ipcMain, protocol } from "electron";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { PROGRESS_CHANNEL, STATE_CHANNEL, type BootstrapState } from "./ipc";
import { denyAllPermissions, registerPrivilegedSchemes, restrictWebContents } from "./security";
import {
  BOOTSTRAP_CSP,
  BOOTSTRAP_FILES,
  BOOTSTRAP_ORIGIN,
  BOOTSTRAP_SCHEME,
  BOOTSTRAP_URL,
  CONTENT_TYPES,
  isTrustedSender,
  originOf,
  SECURE_WEB_PREFERENCES,
} from "./security-policy";
import { StartupError, toUserFacing } from "./lifecycle/errors";
import { dashboardUrl, startApplication, type Application } from "./lifecycle/session";
import { resolveOperatorToken } from "./lifecycle/token";
import type { Progress, SessionDeps, UserFacing } from "./lifecycle/types";
import { buildDependencies, readEnvValue, resolveDashboardPort, resolveStackRoot } from "./runtime/dependencies";
import { ensureOperatorToken, TOKEN_ENV_FILENAME } from "./runtime/operator-token";

// Before `app.whenReady()`: scheme privileges are fixed at registration, and
// registering afterwards does not retroactively apply them.
registerPrivilegedSchemes();

let mainWindow: BrowserWindow | null = null;
let application: Application | null = null;
let tearingDown = false;
let quitAfterTeardown = false;

/** Everything the bootstrap page would have to be told, replayed on demand. */
const history: Progress[] = [];
let lastFailure: UserFacing | null = null;

/**
 * The origins this app's window may ever be on.
 *
 * Both are known before the window exists — the dashboard's port is chosen by
 * `buildDependencies` — so `restrictWebContents` is installed once, from the
 * application-level `web-contents-created` hook below. Installing it twice on
 * the same `webContents` would add a second `will-navigate` listener, and the
 * duplication would be invisible.
 */
let allowedOrigins: string[] = [BOOTSTRAP_ORIGIN];

/**
 * Record one progress event and push it to the window.
 *
 * Every event is kept, not just the latest. The bootstrap page is the only thing
 * between an operator and a blank window while Compose works, so when it fails
 * the operator needs the log — and by the time anyone reads a crash log the
 * window is gone.
 */
function emit(progress: Omit<Progress, "seq">): void {
  const stamped: Progress = { ...progress, seq: history.length };
  history.push(stamped);
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(PROGRESS_CHANNEL, stamped);
  }
  // Both terminal kinds count. `docker-missing` is a `failed` condition under a
  // more specific name, and `lastFailure` is what `launch` consults to avoid
  // reporting the same problem a second time on the way out of its catch block.
  // Only `failed` here produced a duplicated failure line in the window.
  if ((stamped.kind === "failed" || stamped.kind === "docker-missing") && stamped.problem !== undefined) {
    lastFailure = stamped.problem;
  }
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    show: false,
    backgroundColor: "#0f1115",
    title: "OpsPilot",
    webPreferences: {
      ...SECURE_WEB_PREFERENCES,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  window.once("ready-to-show", () => {
    window.show();
  });

  // A preload that fails to load leaves the page with no bridge and no error
  // anywhere the operator can see — measured on Electron 44.7.1, where a
  // sandboxed preload's `require("./ipc")` failed with `module not found` and
  // the window simply had no progress on it. `preload-error` is the only event
  // that says so, and the bootstrap page is exactly where the message can be
  // read.
  window.webContents.on("preload-error", (_event, preloadPath, error) => {
    const problem: UserFacing = {
      title: "The app's preload script did not load",
      detail: `${preloadPath} failed to load: ${error.message}\n\nWithout it the window cannot report what the app is doing, so it will sit on an empty page.`,
      remedy:
        "This is a bug in the shell rather than something about your machine. Quit and start the " +
        "app again; if it repeats in an installed copy, reinstall it, and tell whoever packaged " +
        "it — the preload ships inside the app and nothing on your machine can remove it.",
    };
    lastFailure = problem;
    emit({ kind: "failed", note: problem.title, problem });
    console.error(`[opspilot] preload failed: ${preloadPath}`, error);
  });

  void window.loadURL(BOOTSTRAP_URL);
  return window;
}

/**
 * Serve the bootstrap page from `opspilot://ui/`.
 *
 * The file list is a closed set and the path is resolved and checked to be
 * inside the renderer directory before it is read, so `opspilot://ui/../../
 * package.json` is answered with a 404 rather than a file from outside the
 * bundle. The CSP travels as a response header rather than in a `<meta>` tag, so
 * it applies to every response and not only to the document itself.
 */
function serveBootstrapPage(): void {
  const rendererDir = path.resolve(__dirname, "renderer");
  protocol.handle(BOOTSTRAP_SCHEME, async (request) => {
    let requested: string;
    try {
      requested = new URL(request.url).pathname;
    } catch {
      return new Response("bad request", { status: 400 });
    }
    const name = requested === "" || requested === "/" ? "index.html" : requested.replace(/^\/+/, "");
    if (!BOOTSTRAP_FILES.has(name)) {
      return new Response("not found", { status: 404 });
    }
    const resolved = path.resolve(rendererDir, name);
    if (path.dirname(resolved) !== rendererDir) {
      return new Response("not found", { status: 404 });
    }
    try {
      const body = await readFile(resolved);
      return new Response(body, {
        status: 200,
        headers: {
          "content-type": CONTENT_TYPES[path.extname(name)] ?? "application/octet-stream",
          "content-security-policy": BOOTSTRAP_CSP,
          "x-content-type-options": "nosniff",
        },
      });
    } catch {
      return new Response("not found", { status: 404 });
    }
  });
}

/**
 * Answer the one question the renderer is allowed to ask (Electron security
 * checklist item 17: validate the sender of every IPC message).
 *
 * An untrusted frame is refused without being told anything. This is the only
 * `ipcMain.handle` in the app, which is what makes "the renderer can do nothing
 * privileged" a countable claim rather than an intention.
 */
function registerIpc(): void {
  ipcMain.handle(STATE_CHANNEL, (event): BootstrapState => {
    if (!isTrustedSender(event.senderFrame)) {
      throw new Error("refused: this frame is not the bootstrap page");
    }
    return { progress: [...history], dashboardUrl: application?.url ?? null };
  });
}

/**
 * Resolve the operator token, and stop before spawning anything if there is
 * none. `docker-compose.yml` sets it with the `:?` form, so an unset token
 * stops Compose rendering the project at all — a start-up that got this wrong
 * would show a Compose parse error instead of the instruction below.
 *
 * A packaged launch adds the fourth source `resolveOperatorToken` knows about,
 * and the order decides who wins: an operator who exports the token still
 * overrides, and the generated value is only reached when nothing else is
 * there. A development launch passes `undefined` for it, because a checkout
 * has a `.env` and an operator working in one should be told to fill it in
 * rather than have a token invented behind them.
 */
function attachOperatorToken(deps: SessionDeps): boolean {
  const resolution = resolveOperatorToken({
    fromEnvironment: process.env.OPSPILOT_OPERATOR_TOKEN,
    fromRepositoryEnv: readEnvValue(path.join(deps.repoRoot, ".env"), "OPSPILOT_OPERATOR_TOKEN"),
    fromWebEnvLocal: readEnvValue(path.join(deps.webDir, ".env.local"), "OPSPILOT_OPERATOR_TOKEN"),
    fromAppDataEnv: app.isPackaged ? ensureOperatorToken(app.getPath("userData")) : undefined,
  });
  if (!resolution.ok) {
    lastFailure = resolution.problem;
    emit({ kind: "failed", note: resolution.problem.title, problem: resolution.problem });
    return false;
  }
  deps.env.OPSPILOT_OPERATOR_TOKEN = resolution.token;
  if (resolution.divergence !== undefined) {
    emit({
      kind: "log",
      source: "dashboard",
      note:
        `Note: ${resolution.divergence.join(" and ")} hold a different operator token from the ` +
        `${resolution.source}. Compose and the dashboard would disagree about it, and every ` +
        "approval would be refused.",
    });
  }
  return true;
}

async function launch(): Promise<void> {
  // The stack root is a checkout when this runs from one and `resources/stack`
  // when it does not. `resolveStackRoot` checks the marker in both cases, so a
  // launch that cannot find either fails here with a message naming which of
  // the two was missing — not three steps later inside Compose.
  const deps = buildDependencies({
    repoRoot: resolveStackRoot({ packaged: app.isPackaged, resourcesPath: process.resourcesPath }),
    packaged: app.isPackaged,
    // Only a packaged launch has an env file of its own to name. In a checkout
    // Compose reads the repository's `.env` from beside the compose file, so
    // naming one there would point the remedy at a file that does not exist.
    composeEnvFile: app.isPackaged
      ? path.join(app.getPath("userData"), TOKEN_ENV_FILENAME)
      : undefined,
    dashboardPort: resolveDashboardPort(process.env.OPSPILOT_DESKTOP_PORT),
  });

  // Known before the window opens, so the navigation allowlist is complete the
  // first time it is installed.
  allowedOrigins = [BOOTSTRAP_ORIGIN, originOf(dashboardUrl(deps)) ?? dashboardUrl(deps)];

  if (!attachOperatorToken(deps)) {
    return;
  }

  try {
    application = await startApplication(deps, emit);
  } catch (error) {
    // Every condition `startApplication` recognises has already emitted a
    // `failed`. This is the catch-all, so the window always ends in a state an
    // operator can read rather than on a spinner that never resolves.
    if (lastFailure === null) {
      const problem = error instanceof StartupError ? error.problem : toUserFacing(error);
      lastFailure = problem;
      emit({ kind: "failed", note: problem.title, problem });
    }
    return;
  }

  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    await mainWindow.loadURL(application.url);
  }
}

/**
 * Stop what this app started, then really quit.
 *
 * The dashboard stops first and the backend second: the dashboard is the child
 * process, and while it is up it is the thing making requests. Both steps run
 * even if the first fails — `Application.stop` continues past a failed step on
 * purpose — because a teardown that skips the containers because the dashboard
 * misbehaved is the teardown that leaves a stack running after a window that
 * said it closed.
 */
async function teardownAndQuit(): Promise<void> {
  if (tearingDown) {
    return;
  }
  tearingDown = true;
  const pending = application;
  application = null;
  if (pending !== null) {
    await pending.stop();
  }
  quitAfterTeardown = true;
  app.quit();
}

// Electron's own item 13/14 example hangs these off `web-contents-created`,
// which also covers a `webContents` this app did not construct by hand. This
// app creates exactly one.
app.on("web-contents-created", (_event, contents) => {
  // A getter, not the array: this fires while `createWindow()` runs, and
  // `launch()` assigns the dashboard's origin afterwards. Passing the array
  // captured the pre-launch value — see the block comment on
  // `restrictWebContents` for the defect that caused.
  restrictWebContents(contents, () => allowedOrigins);
});

void app
  .whenReady()
  .then(async () => {
    serveBootstrapPage();
    denyAllPermissions();
    registerIpc();

    mainWindow = createWindow();
    mainWindow.on("closed", () => {
      mainWindow = null;
    });

    await launch();
  })
  .catch((error: unknown) => {
    const problem = toUserFacing(error);
    lastFailure = problem;
    emit({ kind: "failed", note: problem.title, problem });
    dialog.showErrorBox(problem.title, `${problem.detail}\n\n${problem.remedy}`);
  });

app.on("window-all-closed", () => {
  // A single-window app: closing the window is closing the app. Hiding to a
  // tray instead would leave the stack this app started running with nothing on
  // screen to explain it.
  app.quit();
});

app.on("before-quit", (event) => {
  if (quitAfterTeardown) {
    return;
  }
  event.preventDefault();
  void teardownAndQuit();
});

/** An unhandled failure during launch still has to be readable, not silent. */
process.on("uncaughtException", (error) => {
  console.error("[opspilot] uncaught exception", error);
  if (lastFailure === null) {
    const problem = error instanceof StartupError ? error.problem : toUserFacing(error);
    lastFailure = problem;
    emit({ kind: "failed", note: problem.title, problem });
    dialog.showErrorBox(problem.title, `${problem.detail}\n\n${problem.remedy}`);
  }
});