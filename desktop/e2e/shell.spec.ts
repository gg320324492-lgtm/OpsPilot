/**
 * D1's acceptance, in a real window.
 *
 * ## What this file is for
 *
 * The lifecycle specs prove the decisions. This proves the decisions *hold in a
 * running application*: that the window really ends up on the dashboard, that
 * the renderer really is untrusted, that a navigation out of the app really is
 * refused, and that quitting really stops what the app started.
 * `docs/phase2-plan.md` is explicit that "a package that cannot produce that
 * evidence is not finished, however complete the code looks", and the evidence
 * for "the app works" cannot be produced by a function call.
 *
 * ## How it takes the window's word
 *
 * Everything here is observed from the inside rather than read out of the
 * module that configured it:
 *
 *   * `window.evaluate` runs in the **renderer**, so `typeof require ===
 *     "undefined"` and `typeof process === "undefined"` are statements about the
 *     page rather than intentions. A shell whose renderer could reach Node would
 *     render every other security assertion in this repository true and
 *     irrelevant.
 *   * A navigation the *page* attempts is observed being refused. That exercises
 *     the `will-navigate` guard installed by `restrictWebContents` in a running
 *     app, which no unit test can do.
 *   * The renderer process's own command line is read from the OS, which is the
 *     only place `--enable-sandbox` is visible. `SECURE_WEB_PREFERENCES` is
 *     asserted in `security.spec.ts`; this checks that the value reached a
 *     process.
 *
 * ## Why it skips rather than fails without a display
 *
 * The same ADR-0004 pattern `web/e2e/stack.ts` uses for Docker: a machine that
 * cannot open a window skips, naming why. A machine that *can* open one and the
 * app does not come up is a failure, never a skip — that is this spec's whole
 * job.
 */

import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { DEFAULT_DASHBOARD_PORT } from "../src/runtime/dependencies";

const DESKTOP_DIR = path.resolve(__dirname, "..");
const DASHBOARD_URL = `http://127.0.0.1:${process.env.OPSPILOT_DESKTOP_PORT ?? DEFAULT_DASHBOARD_PORT}`;
const DASHBOARD_PATTERN = new RegExp(`^${DASHBOARD_URL.replace(/[.]/g, "\\.")}/`);

/**
 * A PATH with no `docker` on it.
 *
 * `C:\Windows\System32` is used because it exists on every Windows install and
 * does not carry the Docker CLI, which lives under Program Files. Off Windows
 * the test uses a directory that cannot exist, which produces the same `ENOENT`.
 */
const DOCKER_FREE_PATH = process.platform === "win32" ? "C:\\Windows\\System32" : "/nonexistent";

/** Launch, and skip cleanly with a reason when this machine has no display. */
async function launchOrSkip(launchEnv?: Record<string, string>): Promise<ElectronApplication | null> {
  if (!existsSync(path.join(DESKTOP_DIR, "dist", "main.js"))) {
    throw new Error(
      `dist/main.js is missing, so there is no app to launch. Run \`npm run build\` in desktop/ ` +
        `first (${DESKTOP_DIR}).`,
    );
  }
  try {
    return await electron.launch({
      args: [DESKTOP_DIR],
      cwd: DESKTOP_DIR,
      ...(launchEnv === undefined ? {} : { env: launchEnv }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/display|DISPLAY|Xvfb|window|dpkg|libnss/i.test(message)) {
      test.skip(true, `this machine cannot open a window, so the shell cannot be launched: ${message}`);
      return null;
    }
    throw error;
  }
}

test("opens the dashboard, keeps the renderer untrusted, and stops what it started", async () => {
  const app = await launchOrSkip();
  test.skip(app === null, "no display");
  if (app === null) {
    return;
  }

  const startedNothing = (await composeServices()).length === 0;

  try {
    const page = await firstDashboardWindow(app);

    // ---- the window is showing the dashboard ---------------------------------
    await expect(page).toHaveTitle(/OpsPilot/);
    // The dashboard's own navigation. These are Server Components: they exist on
    // the page only if the app rendered and the API answered it.
    await expect(page.getByRole("link", { name: /tickets/i }).first()).toBeVisible();

    // ---- the renderer cannot reach Node -------------------------------------
    const reachable = await page.evaluate(() => {
      const globals: Record<string, unknown> = globalThis;
      return {
        require: typeof globals["require"],
        process: typeof globals["process"],
        module: typeof globals["module"],
        bridgeKeys: Object.keys(globals["opspilot"] ?? {}).sort(),
      };
    });
    expect(reachable.require).toBe("undefined");
    expect(reachable.process).toBe("undefined");
    expect(reachable.module).toBe("undefined");
    // Exactly the two functions `src/preload.ts` exposes. Nothing privileged
    // crossed into the page — no `ipcRenderer`, no `shell`, no filesystem.
    expect(reachable.bridgeKeys).toEqual(["getState", "onProgress"]);

    // ---- navigation out of the app is refused -------------------------------
    // A string expression rather than a callback: the callback would be
    // type-checked against this package's `lib`, which is ES2022 and has no
    // `window`, while this code runs in Chromium. Playwright evaluates the
    // string in the page.
    await page.evaluate("window.location.href = 'https://example.com/'");
    // Give the refused navigation every chance to happen before asserting that
    // it did not.
    await page.waitForTimeout(2_000);
    expect(page.url()).toMatch(DASHBOARD_PATTERN);

    // ---- the renderer process really is sandboxed ----------------------------
    await assertRendererIsSandboxed(app);
  } finally {
    // `close()` quits the app, which fires `before-quit` and therefore runs the
    // teardown. The assertions after this block are what make that a claim.
    await app.close();
  }

  // The dashboard server this app spawned is gone and its port released.
  await expect
    .poll(
      async () => {
        try {
          return (await fetch(`${DASHBOARD_URL}/`)).status;
        } catch {
          return 0;
        }
      },
      { timeout: 90_000 },
    )
    .toBe(0);

  if (startedNothing) {
    // Nothing was running before this test, so the app started the stack, so
    // quitting it stopped the stack. The other branch is the case the ownership
    // rule exists for and is asserted in `ownership.spec.ts`; here it would
    // mean this test had adopted somebody's stack.
    await expect.poll(composeServices, { timeout: 120_000 }).toEqual([]);
  }
});

/**
 * D1's other half: a machine with no Docker gets a named error, not a blank
 * window and not a spinner that never resolves.
 *
 * The PATH handed to the app here is one without `docker` on it, which is the
 * state a machine that has not installed Docker Desktop is actually in. The
 * window must arrive at a readable failure, say what to install, and leave
 * nothing running.
 *
 * The assertion that "exactly one" failure is reported is the one that caught a
 * real defect: `docker-missing` and the `failed` event `launch` emits on the way
 * out of its catch were both reaching the page, so the operator read
 * "Docker is not installed" twice and could not tell whether that was one
 * problem or two.
 */
test("names what is missing when Docker is not installed", async () => {
  // `process.env` values can be `undefined`, which Electron's launcher rejects.
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
  const withoutDocker: Record<string, string> = { ...inherited, PATH: DOCKER_FREE_PATH };
  const app = await launchOrSkip(withoutDocker);
  test.skip(app === null, "no display");
  if (app === null) {
    return;
  }

  try {
    const page = await app.firstWindow();
    // Still the bootstrap page: the failure is rendered there and the window is
    // never handed to a dashboard that does not exist.
    await page.waitForURL("opspilot://ui/**", { timeout: 60_000 });
    await page.waitForSelector("#failure:not([hidden])", { timeout: 60_000 });

    await expect(page.locator("#failure-title")).toHaveText("Docker is not installed");
    await expect(page.locator("#failure-detail")).toContainText("not found on PATH");
    await expect(page.locator("#failure-remedy")).toContainText("Docker Desktop");
    // The page does not say `process.exit` or quit: the operator closes it.
    // What it must not do is retry forever.
    const log = await page.locator("#log").innerText();
    expect(log.split("\n").filter((line) => line.includes("Docker is not installed"))).toHaveLength(1);

    // Nothing was started, because the check runs before anything is spawned.
    expect(await composeServices()).toEqual([]);
  } finally {
    await app.close();
  }
});

/** The one window, once the shell has handed it to the dashboard. */
async function firstDashboardWindow(app: ElectronApplication): Promise<Page> {
  const window = await app.firstWindow();
  await window.waitForURL(DASHBOARD_PATTERN, { timeout: 6 * 60_000 });
  return window;
}

/**
 * Read the renderer's own command line and look for `--enable-sandbox`.
 *
 * This is the OS telling us what it did, which is the only place the flag is
 * visible: `SECURE_WEB_PREFERENCES.sandbox` is what the app asked for, and this
 * is whether it got it. Windows-only, because that is where the launcher reads
 * it from with a command that exists on the machine; elsewhere the check is
 * skipped with the reason rather than quietly passing.
 */
async function assertRendererIsSandboxed(app: ElectronApplication): Promise<void> {
  if (process.platform !== "win32") {
    test.skip(true, "the renderer command-line check is implemented for Windows only");
    return;
  }
  const pid = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.webContents.getOSProcessId());
  expect(pid).toBeGreaterThan(0);

  const commandLine = await new Promise<string>((resolve, reject) => {
    execFile(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${String(pid)}").CommandLine`,
      ],
      { encoding: "utf-8" },
      (error, stdout) =>
        error === null
          ? resolve(stdout)
          : reject(error instanceof Error ? error : new Error("powershell could not be run")),
    );
  });

  expect(commandLine).toContain("--enable-sandbox");
}

/** Which compose services are still running for this repository's project. */
async function composeServices(): Promise<string[]> {
  return new Promise((resolve) => {
    execFile(
      "docker",
      ["compose", "ps", "--format", "{{.Service}}"],
      { cwd: path.resolve(DESKTOP_DIR, "..") },
      (error, stdout) => {
        if (error !== null) {
          // A `docker compose ps` that cannot run is not evidence that the stack
          // stopped. Report the failure rather than an empty list, which would be
          // indistinguishable from a clean teardown.
          resolve([`docker compose ps failed: ${error.message}`]);
          return;
        }
        resolve(
          stdout
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line !== ""),
        );
      },
    );
  });
}