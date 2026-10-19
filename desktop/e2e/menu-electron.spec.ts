/**
 * The menu, in a real Electron, moved by a real cookie.
 *
 * ## WHAT THIS IS FOR
 *
 * `e2e/menu.spec.ts` proves the policy: what the menu says, in which
 * language, and that the two languages describe the same commands. It cannot
 * prove the other half — that the cookie the dashboard writes is the thing
 * that moves the menu — because that half is Electron's cookie store and
 * Electron's `Menu`, and a fake of either proves nothing.
 *
 * So this drives the real ones. The harness in `e2e/fixtures/menu-harness.mjs`
 * is the shipped `src/menu.ts` in a real Electron; this spec writes a cookie
 * into the real session's store and reads the real application menu back.
 *
 * ## THE ORDER MATTERS, AND THIS IS WHY
 *
 * Chromium emits **two** events when a cookie is overwritten: one carrying
 * the old cookie with `removed: true`, and one carrying the new one. An
 * implementation that trusted the first event's value would put the menu in
 * the language the operator just left. The middle assertion below is the one
 * that catches it: it overwrites `zh-CN` with `en` and waits for the menu to
 * be *English*, which only happens if the handler re-reads the store rather
 * than believing the event.
 *
 * ## WHY IT SKIPS RATHER THAN FAILS WITHOUT A DISPLAY
 *
 * The same ADR-0004 pattern `web/e2e/stack.ts` uses for Docker: a machine that
 * cannot open a window skips, naming why. A machine that can open one and the
 * menu does not follow the cookie is a failure, never a skip.
 */

import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { _electron as electron, expect, test, type ElectronApplication } from "@playwright/test";

const DESKTOP_DIR = path.resolve(__dirname, "..");
const HARNESS = path.join(DESKTOP_DIR, "e2e", "fixtures", "menu-harness.mjs");
const COMPILED_MENU = path.join(DESKTOP_DIR, "dist", "menu.js");

/** The origin the locale cookie lives on, in this harness. */
const DASHBOARD_URL = "http://127.0.0.1:3210/";

/** The cookie the dashboard sets, and this app reads. */
const COOKIE = "opspilot_locale";

/** Every label the real application menu shows, section headers included. */
async function menuLabels(app: ElectronApplication): Promise<string[]> {
  return app.evaluate(({ Menu }) => {
    const menu = Menu.getApplicationMenu();
    if (menu === null) {
      return [];
    }
    return menu.items.flatMap((item) => [
      item.label,
      ...(item.submenu?.items.map((sub) => sub.label) ?? []),
    ]);
  });
}

/** The role of every item in the real application menu. */
async function menuRoles(app: ElectronApplication): Promise<string[]> {
  return app.evaluate(({ Menu }) => {
    const menu = Menu.getApplicationMenu();
    if (menu === null) {
      return [];
    }
    return menu.items.flatMap((item) => item.submenu?.items.map((sub) => sub.role ?? "separator") ?? []);
  });
}

/** Write the locale cookie, exactly as the dashboard's route writes it. */
async function writeCookie(
  app: ElectronApplication,
  value: string,
  options: { persistent?: boolean } = {},
): Promise<void> {
  await app.evaluate(
    ({ session }, { url, name, value: locale, persistent }) =>
      session.defaultSession.cookies.set({
        url,
        name,
        value: locale,
        path: "/",
        httpOnly: true,
        // The dashboard's cookie lasts a year (`LOCALE_COOKIE_MAX_AGE` in
        // `web/src/i18n/config.ts`); a cookie without an expiry is a session
        // cookie and would not survive the application closing.
        ...(persistent ? { expirationDate: Math.floor(Date.now() / 1000) + 31_536_000 } : {}),
      }),
    { url: DASHBOARD_URL, name: COOKIE, value, persistent: options.persistent ?? false },
  );
}

/** Take the cookie away, the way a cleared profile does. */
async function clearCookie(app: ElectronApplication): Promise<void> {
  await app.evaluate(
    ({ session }, { url, name }) => session.defaultSession.cookies.remove(url, name),
    { url: DASHBOARD_URL, name: COOKIE },
  );
}

/** Launch the harness, and skip cleanly with a reason when there is no display. */
async function launchOrSkip(env?: Record<string, string>): Promise<ElectronApplication | null> {
  if (!existsSync(COMPILED_MENU)) {
    throw new Error(
      `dist/menu.js is missing, so there is no menu to test. Run \`npm run build\` in desktop/ ` +
        `first (${DESKTOP_DIR}).`,
    );
  }
  try {
    // Electron's launcher rejects an `env` with an undefined value in it, and
    // `process.env` is full of them. The same filtering `shell.spec.ts` does.
    const inherited = Object.fromEntries(
      Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
    );
    return await electron.launch({
      args: [HARNESS],
      cwd: DESKTOP_DIR,
      ...(env === undefined ? {} : { env: { ...inherited, ...env } }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/display|DISPLAY|Xvfb|window|dpkg|libnss/i.test(message)) {
      test.skip(true, `this machine cannot open a window, so the menu cannot be observed: ${message}`);
      return null;
    }
    throw error;
  }
}

test("the menu follows the locale cookie, in a real Electron", async () => {
  const app = await launchOrSkip();
  test.skip(app === null, "no display");
  if (app === null) {
    return;
  }

  try {
    // ---- a profile with no cookie is in the default language ---------------
    // The harness points `sessionData` at a throwaway directory, so this is a
    // profile that has never seen the dashboard, and the menu is English.
    await expect.poll(async () => await menuLabels(app)).toContain("View");
    expect(await menuLabels(app)).not.toContain("视图");

    // ---- the cookie puts the menu in Chinese -------------------------------
    await writeCookie(app, "zh-CN");
    await expect.poll(async () => await menuLabels(app)).toContain("视图");
    const chinese = await menuLabels(app);
    expect(chinese).toContain("文件");
    expect(chinese).toContain("编辑");
    expect(chinese).toContain("窗口");
    expect(chinese).not.toContain("View");

    // ---- and it is the same menu, in another language ----------------------
    const rolesInChinese = await menuRoles(app);

    // ---- overwriting the cookie moves the menu the other way ---------------
    // This is the case Chromium emits two events for. The menu must end up in
    // the language the cookie now says, not the one it said a moment ago.
    await writeCookie(app, "en");
    await expect.poll(async () => await menuLabels(app)).toContain("View");
    expect(await menuLabels(app)).not.toContain("视图");

    const rolesInEnglish = await menuRoles(app);
    expect(rolesInEnglish).toEqual(rolesInChinese);

    // ---- a locale this build does not ship is English ----------------------
    await writeCookie(app, "fr");
    await expect.poll(async () => await menuLabels(app)).toContain("View");
    expect(await menuLabels(app)).not.toContain("视图");

    // ---- a cookie that is taken away is English ----------------------------
    // The step that has the most teeth. A deleted cookie is reported by
    // Chromium as an event carrying the cookie that was deleted — the *old*
    // value, with `removed: true` — so an implementation that believed the
    // event instead of re-reading the store would leave the menu in Chinese
    // with no cookie to say so.
    await writeCookie(app, "zh-CN");
    await expect.poll(async () => await menuLabels(app)).toContain("视图");

    await clearCookie(app);
    await expect.poll(async () => await menuLabels(app)).toContain("View");
    expect(await menuLabels(app)).not.toContain("视图");
  } finally {
    await app.close();
  }
});

test("the menu starts in the language the operator chose last time", async () => {
  const profile = mkdtempSync(path.join(tmpdir(), "opspilot-menu-profile-"));

  // The first run makes the choice, and the choice is a *persistent* cookie —
  // which is what the dashboard's route writes, and what has to outlive the
  // application.
  const first = await launchOrSkip({ OPSPILOT_MENU_HARNESS_PROFILE: profile });
  test.skip(first === null, "no display");
  if (first === null) {
    return;
  }
  try {
    await writeCookie(first, "zh-CN", { persistent: true });
    await expect.poll(async () => await menuLabels(first)).toContain("视图");
  } finally {
    await first.close();
  }

  // Chromium writes the cookie out as the session ends; give it a moment.
  await new Promise((resolve) => setTimeout(resolve, 1_000));

  // The second run writes nothing at all. If the menu is in Chinese, it is
  // because the cookie was already there when the application started and the
  // start-up read found it — the one thing a language switch inside a running
  // application cannot do, and the reason the menu reads the cookie rather
  // than being told.
  const second = await launchOrSkip({ OPSPILOT_MENU_HARNESS_PROFILE: profile });
  test.skip(second === null, "no display");
  if (second === null) {
    return;
  }
  try {
    await expect.poll(async () => await menuLabels(second)).toContain("视图");
    expect(await menuLabels(second)).not.toContain("View");
  } finally {
    await second.close();
  }
});
