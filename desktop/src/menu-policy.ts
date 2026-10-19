/**
 * The native menu, and the language it is in.
 *
 * ## WHY THE POLICY IS NOT IN `menu.ts`
 *
 * `src/security-policy.ts` holds the security policy and `src/security.ts`
 * holds the Electron calls that install it, so a Playwright spec can assert the
 * policy on a runner with no display. The same split is here: this file says
 * what the menu contains and in which language and imports nothing from
 * Electron, so `e2e/menu.spec.ts` can assert all of it in the `lifecycle`
 * project — no Electron, no Docker, no browser binary — and therefore in CI.
 *
 * ## WHY THE MENU HAS ITS OWN COPY OF THE COPY
 *
 * The dashboard's catalogue is `web/src/i18n/dictionaries/*.json`, and the menu
 * cannot read it. A native menu is drawn by the OS, from the main process,
 * which has no Next.js runtime; reading a JSON file out of the dashboard's
 * build output would make the shell depend on a build artifact it does not
 * otherwise need, and a stale one would put the menu in a language the
 * dashboard is not in.
 *
 * So the menu keeps its own two dictionaries, and `e2e/menu.spec.ts` reads
 * `web/src/i18n/config.ts` and fails when the locale list or the cookie name
 * drifts. That is the same technique `e2e/preload-bridge.spec.ts` uses for the
 * preload's channel names: a second copy of a constant is a defect, and the
 * honest way to have one is to make the drift a red test.
 *
 * ## WHAT THE MENU IS
 *
 * The four menus a single-window operator application needs, in the operator's
 * language. It is not a second navigation: the dashboard's own nav is in the
 * page, in the page's language, and the menu does not try to mirror it.
 *
 * Its shape is Electron's own default menu, item for item, because that is the
 * menu an operator's hands already know — and because replacing it with
 * something smaller is how a clipboard quietly disappears. The Edit menu is
 * here for a reason that is not symmetry: `web/src/app/tickets/ticket-form.tsx`
 * has a textarea, and Ctrl+C and Ctrl+V in it are the `copy` and `paste` roles.
 * A menu bar with only a View menu would take the clipboard away from the one
 * screen where an operator pastes a customer's message.
 *
 * Two items are deliberately not carried over: `Force Reload` (a second reload
 * nobody asks for) and Electron's `Help` section, whose two entries point at
 * electronjs.org rather than at anything about this application.
 */

/**
 * The cookie the dashboard stores the operator's locale in.
 *
 * The same name as `LOCALE_COOKIE` in `web/src/i18n/config.ts`, and it has to
 * be: the main process reads the cookie the dashboard writes, so a different
 * name would mean the menu never learns what the page is speaking.
 * `e2e/menu.spec.ts` asserts the two agree.
 */
export const LOCALE_COOKIE = "opspilot_locale";

/**
 * The locales the dashboard ships, in switcher order.
 *
 * A closed tuple rather than `string`, so `isLocale` is a type guard and a
 * cookie holding anything else is a locale this build does not have rather
 * than an index into a dictionary that is not there.
 */
export const LOCALES = ["en", "zh-CN"] as const;

/** One of the locales the dashboard ships. */
export type Locale = (typeof LOCALES)[number];

/**
 * The locale a menu falls back to.
 *
 * English, and for the same reason the dashboard falls back to it
 * (`web/src/i18n/server.ts`): a cookie naming a locale this build does not
 * ship, a cookie that has been cleared, and a cookie the shell could not read
 * at all are all "no choice recorded", and the answer to that is the language
 * the application is written in.
 */
export const DEFAULT_LOCALE: Locale = "en";

/** Narrow anything to a shipped locale, or answer "not one". */
export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/**
 * The locale a cookie value means, or the default when it means nothing.
 *
 * This is the shell's half of the dashboard's own rule: `getLocale` in
 * `web/src/i18n/server.ts` resolves an unreadable cookie to
 * `DEFAULT_LOCALE` rather than failing, and the menu resolves it the same way.
 * A menu that showed an error, or stayed in the previous language, because a
 * cookie held `fr` would be worse than a menu in English.
 */
export function resolveLocale(value: string | null | undefined): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

/**
 * The roles this menu uses.
 *
 * A subset of Electron's `Role`, written out rather than imported so this
 * module stays free of `electron` — the same reason `security-policy.ts`
 * builds origins itself instead of calling `URL.origin`.
 */
export type MenuRole =
  | "quit"
  | "undo"
  | "redo"
  | "cut"
  | "copy"
  | "paste"
  | "delete"
  | "selectAll"
  | "reload"
  | "toggleDevTools"
  | "zoomIn"
  | "zoomOut"
  | "resetZoom"
  | "togglefullscreen"
  | "minimize"
  | "zoom"
  | "close";

/** One entry in a menu, or the gap between two of them. */
export type MenuItemSpec =
  | { readonly type: "separator" }
  | { readonly label: string; readonly role: MenuRole };

/** One menu in the bar. */
export interface MenuSection {
  readonly label: string;
  readonly submenu: MenuItemSpec[];
}

/** Every word the menu says, in one language. */
export interface MenuCopy {
  readonly file: string;
  readonly quit: string;
  readonly edit: string;
  readonly undo: string;
  readonly redo: string;
  readonly cut: string;
  readonly copy: string;
  readonly paste: string;
  readonly delete: string;
  readonly selectAll: string;
  readonly view: string;
  readonly reload: string;
  readonly developerTools: string;
  readonly zoomIn: string;
  readonly zoomOut: string;
  readonly actualSize: string;
  readonly fullScreen: string;
  readonly window: string;
  readonly minimize: string;
  readonly maximize: string;
  readonly close: string;
}

/**
 * The menu's words, per locale.
 *
 * A closed registry rather than a directory scan, for the reason
 * `web/src/i18n/server.ts` gives: a file that happens to be on disk is not
 * thereby a language the application speaks.
 *
 * The Chinese is Simplified, because the dashboard's Chinese is Simplified
 * (`web/src/i18n/dictionaries/zh-CN.json` says 总览, 工单, 知识库), and a menu
 * that said 视图 next to a page that said 總覽 would be two applications.
 */
export const MENU_COPY: Record<Locale, MenuCopy> = {
  en: {
    file: "File",
    quit: "Exit",
    edit: "Edit",
    undo: "Undo",
    redo: "Redo",
    cut: "Cut",
    copy: "Copy",
    paste: "Paste",
    delete: "Delete",
    selectAll: "Select All",
    view: "View",
    reload: "Reload",
    developerTools: "Toggle Developer Tools",
    zoomIn: "Zoom In",
    zoomOut: "Zoom Out",
    actualSize: "Actual Size",
    fullScreen: "Toggle Full Screen",
    window: "Window",
    minimize: "Minimize",
    maximize: "Maximize",
    close: "Close",
  },
  "zh-CN": {
    file: "文件",
    quit: "退出",
    edit: "编辑",
    undo: "撤销",
    redo: "重做",
    cut: "剪切",
    copy: "复制",
    paste: "粘贴",
    delete: "删除",
    selectAll: "全选",
    view: "视图",
    reload: "重新加载",
    developerTools: "开发者工具",
    zoomIn: "放大",
    zoomOut: "缩小",
    actualSize: "实际大小",
    fullScreen: "全屏",
    window: "窗口",
    minimize: "最小化",
    maximize: "最大化",
    close: "关闭",
  },
};

/** How the menu is built. */
export interface MenuOptions {
  /**
   * Whether the development-only items are in the menu.
   *
   * `Reload` and `Toggle Developer Tools` are in a checkout, where a
   * developer needs them, and out of a packaged build, where the person
   * running the application does not: a debug entry in delivered software is
   * an invitation to open a console on a machine that holds an operator
   * token, and the application has no reason to offer it.
   */
  readonly debug: boolean;
}

/**
 * The menu, in one language.
 *
 * The sections are the four of Electron's default menu, in its order, and the
 * items within them are its items. The only thing `debug` changes is whether
 * the two development entries and their separator are in the View menu — so
 * the packaged menu is the default menu minus the parts that are not for
 * whoever receives the package.
 */
export function menuTemplate(locale: Locale, options: MenuOptions): MenuSection[] {
  const copy = MENU_COPY[locale];

  const view: MenuItemSpec[] = [];
  if (options.debug) {
    view.push({ label: copy.reload, role: "reload" });
    view.push({ label: copy.developerTools, role: "toggleDevTools" });
    view.push({ type: "separator" });
  }
  view.push({ label: copy.zoomIn, role: "zoomIn" });
  view.push({ label: copy.zoomOut, role: "zoomOut" });
  view.push({ label: copy.actualSize, role: "resetZoom" });
  view.push({ type: "separator" });
  view.push({ label: copy.fullScreen, role: "togglefullscreen" });

  return [
    {
      label: copy.file,
      submenu: [{ label: copy.quit, role: "quit" }],
    },
    {
      label: copy.edit,
      submenu: [
        { label: copy.undo, role: "undo" },
        { label: copy.redo, role: "redo" },
        { type: "separator" },
        { label: copy.cut, role: "cut" },
        { label: copy.copy, role: "copy" },
        { label: copy.paste, role: "paste" },
        { label: copy.delete, role: "delete" },
        { type: "separator" },
        { label: copy.selectAll, role: "selectAll" },
      ],
    },
    {
      label: copy.view,
      submenu: view,
    },
    {
      label: copy.window,
      submenu: [
        { label: copy.minimize, role: "minimize" },
        { label: copy.maximize, role: "zoom" },
        { label: copy.close, role: "close" },
      ],
    },
  ];
}
