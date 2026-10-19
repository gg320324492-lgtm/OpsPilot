/**
 * The menu's policy: what it contains, in which language, and what it must
 * agree with.
 *
 * ## WHY THIS RUNS WITHOUT ELECTRON
 *
 * `src/menu-policy.ts` imports nothing from Electron, so this spec runs in the
 * `lifecycle` project: no Electron, no Docker, no browser binary, no display.
 * That is the same discipline `src/security-policy.ts` follows and for the same
 * reason — the policy is what the application is judged on, and a policy
 * nobody asserts is a comment.
 *
 * The half that cannot be asserted here — that a cookie really does move the
 * real menu in a real Electron — is `e2e/menu-electron.spec.ts`.
 *
 * ## WHAT EACH TEST IS DEFENDING AGAINST
 *
 *   * The two languages describing *different menus* is the failure a
 *     dictionary-shaped bug produces: an item added to the English template
 *     and forgotten in the Chinese one, so switching language silently
 *     removes a command. The role sequences are compared for that reason, not
 *     the labels.
 *   * The Chinese dictionary quietly holding English is the failure a
 *     copy-and-paste produces, and it is invisible to a reader who does not
 *     read Chinese. Every Chinese label is asserted to contain a Chinese
 *     character.
 *   * The Edit menu losing `copy` and `paste` is the failure a "smaller menu"
 *     produces, and it would take the clipboard away from the ticket form's
 *     textarea without failing anything visible.
 *   * The shell's copy of the cookie name drifting from the dashboard's is the
 *     failure this repository treats as a defect wherever it appears — the
 *     preload's channel names, the token's environment variable — and it is
 *     caught here by reading `web/src/i18n/config.ts` as text.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  DEFAULT_LOCALE,
  LOCALES,
  LOCALE_COOKIE,
  MENU_COPY,
  isLocale,
  menuTemplate,
  resolveLocale,
  type MenuSection,
} from "../src/menu-policy";

/** The dashboard's own declaration of the same three facts. */
const WEB_CONFIG = path.resolve(__dirname, "..", "..", "web", "src", "i18n", "config.ts");

/** The main process, which is what actually installs and keeps the menu. */
const MAIN_SOURCE = path.resolve(__dirname, "..", "src", "main.ts");

/**
 * A Chinese character, in any of the blocks a UI label is written in.
 *
 * A Unicode property escape rather than a range of literal characters, so the
 * pattern cannot be broken by the file being read as something other than
 * UTF-8.
 */
const CJK = /\p{Script=Han}/u;

/** The roles a template asks for, in order, with the gaps marked. */
function rolesOf(template: MenuSection[]): string[] {
  return template.flatMap((section) =>
    section.submenu.map((item) => ("role" in item ? item.role : "separator")),
  );
}

/** Every label a template shows, section headers and items alike. */
function labelsOf(template: MenuSection[]): string[] {
  return template.flatMap((section) => [
    section.label,
    ...section.submenu.flatMap((item) => ("label" in item ? [item.label] : [])),
  ]);
}

/** A `const NAME = "…"` declaration, read out of a source file. */
function declaredString(source: string, name: string): string | undefined {
  const match = new RegExp(`export const ${name} = "([^"]+)"`).exec(source);
  return match?.[1];
}

/** A `const NAME = […]` declaration, read out of a source file. */
function declaredStringArray(source: string, name: string): string[] {
  const match = new RegExp(`export const ${name} = \\[([^\\]]*)\\]`).exec(source);
  const body = match?.[1];
  if (body === undefined) {
    return [];
  }
  return [...body.matchAll(/"([^"]+)"/g)].map((found) => found[1] ?? "");
}

test.describe("the menu's shape", () => {
  test("is the four menus of Electron's own default, in its order", () => {
    const template = menuTemplate("en", { debug: false });
    expect(template.map((section) => section.label)).toEqual(["File", "Edit", "View", "Window"]);
  });

  test("keeps the clipboard where the ticket form's textarea can reach it", () => {
    // `web/src/app/tickets/ticket-form.tsx` has a textarea. Ctrl+C and Ctrl+V
    // in it are these two roles, and a menu without them takes the clipboard
    // away from the one screen where an operator pastes a customer's message.
    for (const locale of LOCALES) {
      const roles = rolesOf(menuTemplate(locale, { debug: false }));
      expect(roles, `${locale} lost the clipboard`).toContain("copy");
      expect(roles, `${locale} lost the clipboard`).toContain("paste");
      expect(roles, `${locale} lost select-all`).toContain("selectAll");
    }
  });

  test("is the default menu, in full, when the debug items are on", () => {
    // Electron's default View menu is Reload, Force Reload, Toggle Developer
    // Tools, then the zoom group and the full-screen toggle. Force Reload is
    // the one item deliberately not carried over, so the roles are asserted
    // rather than compared with the default's.
    const template = menuTemplate("en", { debug: true });
    expect(rolesOf(template)).toEqual([
      "quit",
      "undo",
      "redo",
      "separator",
      "cut",
      "copy",
      "paste",
      "delete",
      "separator",
      "selectAll",
      "reload",
      "toggleDevTools",
      "separator",
      "zoomIn",
      "zoomOut",
      "resetZoom",
      "separator",
      "togglefullscreen",
      "minimize",
      "zoom",
      "close",
    ]);
  });

  test("leaves the debug items out of a packaged build", () => {
    const packaged = rolesOf(menuTemplate("en", { debug: false }));
    expect(packaged).not.toContain("reload");
    expect(packaged).not.toContain("toggleDevTools");
    // Everything else is still there: a packaged menu is a smaller menu, not a
    // different one.
    const development = rolesOf(menuTemplate("en", { debug: true }));
    for (const role of packaged) {
      expect(development).toContain(role);
    }
  });

  test("is built fresh every time, so one language cannot leak into the next", () => {
    const first = menuTemplate("zh-CN", { debug: true });
    const second = menuTemplate("zh-CN", { debug: true });
    expect(first).toEqual(second);
    expect(first[2]?.submenu).not.toBe(second[2]?.submenu);
  });
});

test.describe("the menu's two languages", () => {
  test("describe the same menu, in the same order, with the same commands", () => {
    const english = menuTemplate("en", { debug: true });
    const chinese = menuTemplate("zh-CN", { debug: true });
    expect(chinese).toHaveLength(english.length);
    // The commands, not the words: an item added to one language and
    // forgotten in the other is a command that disappears when the operator
    // switches language, and no label comparison would catch it.
    expect(rolesOf(chinese)).toEqual(rolesOf(english));
    expect(chinese.map((section) => section.submenu.length)).toEqual(
      english.map((section) => section.submenu.length),
    );
  });

  test("say it in Chinese when the locale is Chinese", () => {
    const labels = labelsOf(menuTemplate("zh-CN", { debug: true }));
    for (const label of labels) {
      expect(label, `"${label}" is not Chinese`).toMatch(CJK);
    }
  });

  test("say something different in each language", () => {
    // A label left in English is a copy-and-paste that survived; the Chinese
    // one would then be invisible to a reader who does not read Chinese.
    const english = labelsOf(menuTemplate("en", { debug: true }));
    const chinese = labelsOf(menuTemplate("zh-CN", { debug: true }));
    expect(chinese).toHaveLength(english.length);
    for (const [index, label] of chinese.entries()) {
      expect(label, `label ${index} is the same in both languages`).not.toBe(english[index]);
    }
  });

  test("have a word for every command the template shows", () => {
    for (const locale of LOCALES) {
      const copy = MENU_COPY[locale];
      const used = new Set(
        menuTemplate(locale, { debug: true })
          .flatMap((section) => section.submenu)
          .flatMap((item) => ("label" in item ? [item.label] : [])),
      );
      for (const label of used) {
        expect(Object.values(copy), `${locale} shows "${label}" but does not define it`).toContain(label);
      }
    }
  });
});

test.describe("resolving a locale", () => {
  test("accepts the locales the dashboard ships", () => {
    expect(resolveLocale("en")).toBe("en");
    expect(resolveLocale("zh-CN")).toBe("zh-CN");
    for (const locale of LOCALES) {
      expect(isLocale(locale)).toBe(true);
    }
  });

  test("falls back to English for anything else", () => {
    // The same rule the dashboard applies to the same cookie
    // (`web/src/i18n/server.ts`): a cookie naming a locale this build does not
    // ship is not an error, it is English.
    for (const value of ["fr", "zh-cn", "ZH-CN", "zh_CN", "", " en", "en ", "en-US"]) {
      expect(resolveLocale(value), `${value} should not be a locale`).toBe(DEFAULT_LOCALE);
    }
    expect(resolveLocale(null)).toBe(DEFAULT_LOCALE);
    expect(resolveLocale(undefined)).toBe(DEFAULT_LOCALE);
  });
});

test.describe("the shell and the dashboard agree on the cookie", () => {
  const source = readFileSync(WEB_CONFIG, "utf8");

  test("the cookie has the same name in both", () => {
    expect(declaredString(source, "LOCALE_COOKIE")).toBe(LOCALE_COOKIE);
  });

  test("the locales are the same list in both", () => {
    expect(declaredStringArray(source, "LOCALES")).toEqual([...LOCALES]);
  });

  test("the fallback language is the same in both", () => {
    expect(declaredString(source, "DEFAULT_LOCALE")).toBe(DEFAULT_LOCALE);
  });
});

test.describe("the main process keeps the menu", () => {
  const main = readFileSync(MAIN_SOURCE, "utf8");

  test("installs the menu, and follows the cookie", () => {
    // The behaviour is proven in a real Electron by
    // `e2e/menu-electron.spec.ts`. This pins the *wiring*: a refactor that
    // dropped one of these three calls would leave a menu that never moves,
    // and nothing else in this package would notice.
    expect(main).toContain('from "./menu"');
    expect(main).toMatch(/\binstallMenu\(\{/);
    expect(main).toMatch(/\bsyncMenuToCookie\(/);
    expect(main).toMatch(/\bwatchLocaleCookie\(/);
  });

  test("leaves the debug items to a checkout", () => {
    // `app.isPackaged` is the line between a developer's machine and the
    // software that is delivered; a packaged build must not offer a console.
    expect(main).toMatch(/\bdebug: !app\.isPackaged\b/);
  });
});
